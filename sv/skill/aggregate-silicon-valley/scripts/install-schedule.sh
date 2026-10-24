#!/usr/bin/env bash
# Schedule the unattended daily run (daily.sh) on this machine: a launchd agent
# on a Mac, a crontab line on Linux. Both call daily.sh every 20 minutes; it
# acts once a day, from this machine's start time (India time) on.
#   bash install-schedule.sh --at 07:00     install (or move the start time)
#   bash install-schedule.sh --uninstall
# Needs: an ingest token (ingest.py --check says ok) and a Claude Code login
# that works headless (`claude -p "say ok"`).
set -euo pipefail
DAILY="$(cd "$(dirname "$0")" && pwd)/daily.sh"
CONF_DIR="$HOME/.config/keeping-up-sv"
CONF="$CONF_DIR/schedule.env"
LABEL=site.induscore.keeping-up-sv.daily
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TAG='# job:keeping-up-sv-daily'

AT="" UNINSTALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --at) AT="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    *) echo "usage: install-schedule.sh [--at HH:MM] [--uninstall]" >&2; exit 64 ;;
  esac
done

if [ "$UNINSTALL" = 1 ]; then
  if [ "$(uname)" = Darwin ]; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
  else
    (crontab -l 2>/dev/null | grep -vF "$TAG") | crontab -
  fi
  echo "daily run unscheduled"
  exit 0
fi

if [ -n "$AT" ]; then
  [[ "$AT" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || { echo "--at takes HH:MM (India time)" >&2; exit 64; }
  mkdir -p "$CONF_DIR"
  touch "$CONF" && chmod 600 "$CONF"
  { grep -v '^SV_DAILY_AT=' "$CONF" || true; echo "SV_DAILY_AT=$AT"; } > "$CONF.new" && mv "$CONF.new" "$CONF"
fi

CLAUDE_BIN="$(command -v "${SV_CLAUDE:-claude}" || true)"
[ -n "$CLAUDE_BIN" ] || { echo "claude is not on PATH" >&2; exit 1; }
PATH_FOR_JOB="$(dirname "$CLAUDE_BIN"):$(dirname "$(command -v python3)"):/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"

if [ "$(uname)" = Darwin ]; then
  mkdir -p "$(dirname "$PLIST")"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$DAILY</string></array>
  <key>StartInterval</key><integer>1200</integer>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATH_FOR_JOB</string></dict>
  <key>StandardOutPath</key><string>/tmp/keeping-up-sv-daily.out</string>
  <key>StandardErrorPath</key><string>/tmp/keeping-up-sv-daily.out</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "launchd agent $LABEL installed (every 20 minutes)"
else
  (crontab -l 2>/dev/null | grep -vF "$TAG"; echo "*/20 * * * * PATH=$PATH_FOR_JOB /bin/bash $DAILY $TAG") | crontab -
  echo "crontab line installed (every 20 minutes)"
fi
echo "start time: ${AT:-$(grep '^SV_DAILY_AT=' "$CONF" 2>/dev/null | cut -d= -f2 || true)} India time (default 07:00); logs: ~/.local/state/keeping-up-sv/daily/"
