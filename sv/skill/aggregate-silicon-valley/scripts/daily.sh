#!/usr/bin/env bash
# The unattended daily run: one edition a day, from whichever founder's machine
# (or the VM) gets there first. The scheduler (launchd or cron, see
# install-schedule.sh) calls this every 20 minutes; it does nothing until the
# machine's start time (SV_DAILY_AT, India time, default 07:00), then runs
# /aggregate-silicon-valley headless for the Pacific date that is ending, unless
# a run for that edition already finished after 07:00 India time today, from
# any machine. Duplicate runs are harmless (the server upserts), so machines are
# staggered rather than locked.
#
# Settings: ~/.config/keeping-up-sv/schedule.env (optional)
#   SV_DAILY_AT=07:00            India time this machine starts trying
#   SV_DAILY_TRIES=2             runs per day before this machine gives up
#   SV_CLAUDE=claude             the Claude Code CLI
#   CLAUDE_CODE_OAUTH_TOKEN=...  only where there is no interactive login (the VM: `claude setup-token`)
#   bash daily.sh --now          run now, ignoring the start time (still skips a finished edition)
# Log and state: ~/.local/state/keeping-up-sv/daily/<edition>.{log,tries,done}
set -uo pipefail
SKILL="$(cd "$(dirname "$0")/.." && pwd)"
STATE="${KEEPING_UP_SV_STATE:-$HOME/.local/state/keeping-up-sv}/daily"
CONF="$HOME/.config/keeping-up-sv/schedule.env"
mkdir -p "$STATE"
# shellcheck disable=SC1090
[ -f "$CONF" ] && { set -a; . "$CONF"; set +a; }
AT="${SV_DAILY_AT:-07:00}"
TRIES="${SV_DAILY_TRIES:-2}"
CLAUDE="${SV_CLAUDE:-claude}"
NOW=0; [ "${1:-}" = "--now" ] && NOW=1

# India clock: the time now, and the edition (the Pacific date that is ending at
# 07:00 India time, which is yesterday's India date).
read -r HHMM EDITION < <(TZ=Asia/Kolkata python3 -I -c \
  'import datetime as d; n = d.datetime.now(); print(n.strftime("%H:%M"), (n.date() - d.timedelta(days=1)).isoformat())')
EDITION="${SV_EDITION:-$EDITION}"   # a backfill or a test: SV_EDITION=YYYY-MM-DD bash daily.sh --now
[ "$NOW" = 1 ] || [[ ! "$HHMM" < "$AT" ]] || exit 0
[ -e "$STATE/$EDITION.done" ] && exit 0
LOG="$STATE/$EDITION.log"
log() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }

# Done already, from any machine? A run for this edition that finished after 07:00 India time today.
edition_done() {
  python3 -I - "$SKILL/scripts" "$EDITION" <<'EOF'
import datetime as d, sys
sys.path.insert(0, sys.argv[1])
import ingest
base, token = ingest.connection()
status, res = ingest.call("GET", f"{base}/api/ingest/editions/{sys.argv[2]}", token)
if status != 200:
    sys.exit(2)
cutoff = d.datetime.combine(d.date.fromisoformat(sys.argv[2]) + d.timedelta(days=1), d.time(1, 30), d.timezone.utc)
done = [r for r in res.get("runs", []) if r.get("status") in ("complete", "partial") and r.get("finished_at")
        and d.datetime.fromisoformat(r["finished_at"].replace("Z", "+00:00")) >= cutoff]
sys.exit(0 if done else 1)
EOF
}
edition_done; rc=$?
if [ $rc = 0 ]; then log "edition $EDITION already done"; touch "$STATE/$EDITION.done"; exit 0; fi
[ $rc = 2 ] && log "could not reach the ingest API to check $EDITION; running anyway"

tries=$(cat "$STATE/$EDITION.tries" 2>/dev/null || echo 0)
if [ "$tries" -ge "$TRIES" ]; then exit 0; fi
echo $((tries + 1)) > "$STATE/$EDITION.tries"
log "run $((tries + 1))/$TRIES for edition $EDITION on $(hostname -s)"

PROMPT="/aggregate-silicon-valley $EDITION

This is the unattended daily run (07:00 India time). Nobody is watching: never ask a question, make each call \
yourself and finish the whole run, including step 5 (propose movers with --publish-min 45). End with the step 6 report."
cd "$SKILL" || exit 1
# a hung run must not hold the day: 90 minutes at most
perl -e 'alarm shift; exec @ARGV or die "exec: $!"' 5400 \
  "$CLAUDE" -p "$PROMPT" --dangerously-skip-permissions >> "$LOG" 2>&1
code=$?
log "claude exited $code"

if edition_done; then
  log "edition $EDITION verified"
  touch "$STATE/$EDITION.done"
  exit 0
fi
log "edition $EDITION NOT verified after this run"
exit 1
