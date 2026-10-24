---
name: aggregate-silicon-valley
description: >
  Run the daily aggregation for Keeping Up with Silicon Valley (induscore.site/sv): collect startup, funding,
  launch, founder and VC stories from the configured sources (news, essays, YouTube, Reddit, App Store chart
  movers; 小红书 and X only with a founder's login), curate them, persist them to the VM database through the
  site's ingest API, verify they are live, and propose up to 3 movers (publishing those scoring 45 or more). Also records a founder's
  research runs (verdicts and every scored idea) on the private Research page. Use when the user says /aggregate-silicon-valley
  [YYYY-MM-DD], "run today's Silicon Valley feed", "aggregate the SV feed", "backfill the feed for <date>",
  "rerun the startup feed", or asks why an edition is missing or partial. Not for changing the site's code.
---

# aggregate-silicon-valley

You are the orchestrator of one edition of **Keeping Up with Silicon Valley** (https://induscore.site/sv).
Scripts do the fetching and the writing; you do the judgment: which stories matter, what kind each is,
which duplicates are the same event, and a one-line context note grounded in the source. This skill is
the **only** way stories get into the feed. Every morning a scheduler starts it headless in Claude Code
(see "The daily schedule"); a founder can also run it by hand.

Paths below are relative to this skill's directory (`~/.claude/skills/aggregate-silicon-valley/` when
installed by teamctx). Use `python3 -I` so nothing from the current directory is imported.

## One-time setup (per person)

1. You need an ingest token. If `python3 -I scripts/ingest.py --check` says "No ingest token", get one
   from the VM (each founder has their own; issuing a new one replaces your old one):

   ```bash
   ssh <vm> 'cd ~/keeping-up-sv/sv/api && set -a && . ~/.config/keeping-up-sv/env && set +a && .venv/bin/python manage.py issue-token <your-name>' \
     | sed 's/^/SV_INGEST_TOKEN=/' > ~/.config/keeping-up-sv/ingest.env && chmod 600 ~/.config/keeping-up-sv/ingest.env
   ```

   `<vm>` is the prod VM's ssh target (host and key are in teamctx, the prod VM workflow). Create
   `~/.config/keeping-up-sv/` first (`mkdir -p -m 700`). Never paste the token into chat, teamctx or git.
2. `python3 -I scripts/ingest.py --check` must print `ingest token: ok`.

## The run

**0. Date.** Use the date the user gave (YYYY-MM-DD). Otherwise today in Pacific time:
`python3 -I -c "import sys; sys.path.insert(0,'scripts'); import svlib; print(svlib.today_pt())"`.
Future dates are refused. An edition for date D covers stories published in the 48 hours before the end
of D (Pacific); a story already featured in another edition is never repeated, so reruns and backfills
are safe.

**1. Collect.**
```bash
python3 -I scripts/collect.py --date D --out "${TMPDIR:-/tmp}/keeping-up-sv/D"
```
It prints each source's status and writes three files to that directory: `candidates.json` (stories to
curate), `sources.json` (each source's status, sent with the edition) and `chart-movers.json` (App Store
chart movers for step 5; `--no-charts` skips them). What the sources need:

- **No login**: news and launch feeds, Hacker News, YC Launches, investor and operator essays, company
  blogs, YouTube channels (public channel feeds) and Apple's public chart feeds. These should all say `ok`.
- **Best effort**: Reddit. Reddit allows anonymous readers about one request a minute, so its four sources
  are read one after another, as slowly as Reddit's headers ask; a full run takes a few minutes longer than
  before. A 429 is retried once, then that source fails and the others are marked "not tried". That is
  expected sometimes; the failure is recorded on the edition and the run is `partial`.
- **Needs a founder's login** (optional): 小红书 and X search through agent-reach's OpenCLI bridge, i.e. the
  founder's own logged-in Chrome. They run only when `agent-reach doctor --json` reports the channel ok
  through OpenCLI. Doctor never checks a login itself, so a founder who is logged in to xiaohongshu.com or
  x.com in Chrome can add `--login-sources` to the collect command: one read-only search proves the login
  first. Otherwise they print `skip` with "skipped: needs login (run agent-reach configure)"; that is
  recorded on the edition and does not make the run partial. Never log in for the founder, never export
  or store cookies, never work around a captcha.

Do not retry a failed source more than once, and never work around a block (403, robots, login walls).

**2. Curate.** Read `candidates.json` (ranked by a heuristic `score`). Write `curated.json` in the same
directory: `{"items": [...]}`. Aim for the best 25–45 stories; on a quiet day fewer is right. Never
add a story, number, date or link that is not in the candidates or in a page you actually opened.

For each kept candidate, start from its fields and fix them:

- **Keep** stories about startups, funding, acquisitions, launches, founders, investors, AI/dev tools/infra
  and other technology businesses, and the big-company moves that shape them. **Drop** general politics,
  celebrity, sports, consumer deals, routine patch releases, and HN posts with no startup or technology-business
  angle. Prefer the original publisher's article over a re-write.
- **Same event, different headlines** (e.g. TechCrunch and SiliconANGLE both on one round): keep the best
  article as the item and put the others in its `mentions` as `{"url", "source", "label"}` (label = the
  publication's name). Keep existing Hacker News/Techmeme mentions with their points/comments.
- `category`: `funding` (a round or new fund, as reported), `acquisition`, `launch` (a product, model or
  company launch), `news`, `discussion` (Ask HN, Reddit threads, debates), `analysis` (VC theses, essays,
  predictions, market maps).
- `claim_status`: `confirmed` when the company itself announced it (its blog, its launch page, YC Launch,
  Product Hunt maker post); `reported` when a publication reported it, including "sources say";
  `unverified` for claims in posts or threads (growth numbers, revenue, "we hit $1M ARR"); `opinion` for
  analysis and predictions.
- `funding` (funding items only, and only what the source states): `{"company", "round", "amount",
  "investors": [...]}`. `funding_hint` in the candidate is a regex guess; check it against the title and
  excerpt and drop anything you cannot see there. No funding object on an `unverified` item.
- `tags`: up to 6 lowercase slugs from: ai, ai-agents, devtools, infra, chips, robotics, fintech, crypto,
  security, climate, health, defense, consumer, enterprise, open-source, vc, ipo, yc, m-and-a, plus a few
  specific ones when they recur (e.g. `openai`, `nvidia`). Keep the vocabulary small so topic pages fill up.
- `title`: the source's headline. You may shorten a very long Techmeme summary headline to its first
  clause; never change its meaning or add claims.
- `summary` (optional, max 1–2 sentences, ≤ 400 chars): why it matters, using only facts in the title or
  excerpt (e.g. who led the round, what the product does). Leave it out rather than guess.
- `excerpt`: keep the candidate's (the source's own words). Do not write your own text into `excerpt`.
- `score`: start from the candidate's score; move it by at most ±20 and say why in
  `score_detail.why` (one plain sentence a reader would understand, e.g. "Largest disclosed AI round this
  week; covered by three outlets."). Keep the rest of `score_detail`.
- `embed_url`: only if the story *is* an X post or YouTube video (the site uses the platforms' own embeds).
  YouTube candidates and X candidates from the login search already have the right URL; keep it.
- **YouTube videos** (sources `yt-*`): keep interviews and talks with a clear startup, product or investing
  angle; drop clips, promos and off-topic episodes. `analysis`/`opinion` for investor and founder talks;
  `discussion`/`unverified` for founder case studies with revenue claims (Starter Story). The excerpt is the
  channel's own description. To judge a video you are unsure about, you may read its subtitles
  (`yt-dlp --skip-download --write-auto-subs --sub-langs "en.*,zh.*" -o "$DIR/subs/%(id)s" <url>`, the
  agent-reach YouTube channel). Use them only to decide and to write a summary grounded in what is said;
  never put a transcript into `curated.json`, the database or a summary longer than the usual two
  sentences, and delete `$DIR/subs` before you finish.
- **Reddit and 小红书/X posts** are `discussion` with `claim_status: unverified`: any growth or revenue
  figure in them is the poster's claim. A 小红书 candidate's `extra.open_url` (with the session's
  `xsec_token`) is for you to open while curating; the item's `url` stays the bare note URL.
- Copy `url`, `source`, `source_name`, `published_at`, `author`, `mentions` from the candidate. Drop the
  helper fields (`id`, `found_in`, `weight`, `relevant`, `engagement`, `funding_hint`, `extra`,
  `engagement_self`); the ingest script ignores unknown fields anyway. Keep `image_url` and `image_kind`
  when the candidate has them (feed images, YC logos).

Writing `curated.json` with a short Python snippet that loads candidates, applies your decisions by
`id`, and dumps the result is the most reliable way for 30+ items.

**3. Pictures and embeds.**
```bash
python3 -I scripts/enrich.py --curated "$DIR/curated.json"
```
It opens each curated story's own page once (robots.txt respected) and adds `image_url` (the page's
preview image, a link to the publisher's file, never a copy) and `embed_url` (a YouTube video or an X
post the publisher embedded or cited in the article). Review every embed it prints: each line shows the
post's author and opening words, or the video's channel and title. Keep it only if it is about the story
(the company's own announcement, the founder's post, the official advisory, the product demo). Replace it
with a better "other candidate" or remove `embed_url` for that item in `curated.json` when it is
off-topic: a publisher's promo for its own podcast, a reply or tribute, an unrelated post. Never invent an
embed URL; only use ones the script found or that the source itself links.

**4. Persist and verify.**
```bash
python3 -I scripts/ingest.py --date D --curated "$DIR/curated.json" --sources "$DIR/sources.json"
```
It starts a run, sends the items, finishes the run with every source's status (`complete` when every
source worked or was skipped for want of a login and nothing was rejected, otherwise `partial`), then checks the database holds every story
and the public page `https://induscore.site/sv/D` shows the top ones. Exit code 0 means both checks passed.
If items are rejected, read the reason, fix `curated.json`, and rerun the same command: reruns update in
place and never duplicate.

**5. Propose movers (at most 3).** Every run feeds the founders' desk (see "Movers to watch" below). Spend
a few minutes here; zero proposals is a fine answer on a weak day.

a. Read the founders' calls first: `python3 -I scripts/ingest.py feedback` (votes with reasons, stage,
   status, score) and `python3 -I scripts/ingest.py movers --list` (every id, including passed and archived).
b. Gather signals: `chart-movers.json` in the run directory (apps rising in a country's chart, apps that
   work abroad but not in the US, US hits missing in India and Southeast Asia), plus the strongest signals
   in today's candidates: a funding round, a revenue figure a founder states in public (MRR, ARR), a chart
   jump, fast GitHub star growth, a post or video with unusual engagement (HN points, views, likes, a top
   Reddit post of the day).
c. Pick at most 3 that fit the founders' context: they build software for manufacturing, e-commerce and
   their suppliers, in India, Southeast Asia, Europe, Australia and New Zealand. Good picks: something that
   works in one market and is missing in theirs, a product their customers' world could copy or localize,
   a tool or skill they could use themselves.
d. Let the votes steer: more like the movers they upvoted or moved to researching or building; nothing like
   the ones they passed or downvoted; read every vote reason and follow it (a reason beats these defaults).
e. Never propose a mover that already exists: no existing id, name or site, whatever its status.
f. For each pick, open the pages you cite, then write one file under `${TMPDIR:-/tmp}/keeping-up-sv/` with
   the shape shown under "Movers to watch" and **no `status` field**:
   - public facts only in the public fields, every traction line with a `source_url` you opened and the
     `as_of` date the figure was true. For a chart signal, the line is the chart itself, e.g.
     `{"label": "Top free iPhone apps, India", "value": "#7, new to the top 100", "source_url": "<the feed
     URL from chart-movers.json>", "as_of": "<its snapshot date>"}`, and `url` is the app's own page;
   - `notes.take` (why it matters for the founders, privately), and `learn` / `localize` when you can
     support them;
   - `notes.score`: the office-hours rubric, eight lines with their maximums (demand 25, problem 10, fit 15,
     wedge 10, market 10, future 15, moat 10, whitespace 5) for one market you name in `notes.score.market`,
     with a one-line verdict. Score honestly; if the total is under 40, do not propose it;
   - `item_urls`: stories in today's edition about it, if any.
g. Send them with `python3 -I scripts/ingest.py movers --propose <file.json> --publish-min 45`. It refuses the
   whole file, before sending anything, if it has more than 3 movers, a `status`, an id, name or site that
   already exists, or a mover without a sourced traction line, a take or a score. Proposals scoring 45 or
   more go straight on the public list (`published` in its report); the rest wait on the desk.

**6. Report** to the user, from `report.json`, without rounding up: edition date and URL, stories in the
edition, inserted/updated/merged/rejected, sources that worked, those that failed (with the error) and
those skipped because they need a login, whether the database and page checks passed, and the run status
(`complete` or `partial`). Then the movers you proposed (id, name, score, market, the signal behind it) or
why you proposed none. Say plainly if anything failed.

## The daily schedule

Every morning at 07:00 India time the edition for the Pacific date that is ending (yesterday's India date)
is run headless by `scripts/daily.sh`, from whichever machine gets there first: the VM and each founder's
laptop. The scheduler calls it every 20 minutes. It does nothing before the machine's start time, and nothing
once a run for that edition has finished after 07:00 India time today, from any machine (it asks the ingest
API). Machines are staggered rather than locked: duplicate runs are harmless.

- Install on a laptop or the VM: `bash scripts/install-schedule.sh --at 07:00` (launchd on a Mac, crontab on
  Linux). The start time is India time, whatever the machine's own time zone; use a later one (for example
  `--at 08:30`) on a backup machine. `--uninstall` removes it.
- Needs this machine's ingest token (One-time setup) and a Claude Code login that works without a terminal:
  `claude -p "say ok"`. Where nobody logs in interactively (the VM), run `claude setup-token` once and put
  `CLAUDE_CODE_OAUTH_TOKEN=...` in `~/.config/keeping-up-sv/schedule.env` (mode 600).
- Logs and state: `~/.local/state/keeping-up-sv/daily/<edition>.log`. Each machine tries an edition at most
  twice a day (`SV_DAILY_TRIES`). A backfill or test: `SV_EDITION=YYYY-MM-DD bash scripts/daily.sh --now`.

## Movers to watch

`https://induscore.site/sv/movers` lists companies, apps and skills gaining traction, kept apart from the
daily news. Behind the founders' unlock the same page is their **desk**: a queue of proposed movers, each with
an office-hours score, votes (with reasons), a stage (new, researching, building, passed) and copyable next
steps (an REA prompt, a build brief, a scoring prompt).

**A run proposes movers; the strong ones publish themselves.** A mover sent without `status` is `proposed`:
it waits in the desk's queue and appears on no public page, feed, sitemap or story until a founder publishes
it there. The founders' standing rule (Pulkit, 2026-10-24) is that a run's proposal scoring 45 or more on the
office-hours rubric is published by the run itself (`--publish-min 45`); a founder can still pass or archive
it from the desk. Otherwise send `"status": "watching"` only when a founder told you to put it straight on
the list. Each daily run proposes at most 3 (step 5 of the run, always with `--propose --publish-min 45`).

**Before proposing**, read what the founders thought of the last ones:
`python3 -I scripts/ingest.py feedback`. Propose more like the upvoted ones and the ones in "researching" or
"building"; none like the passed or downvoted ones; read every vote's reason and follow it.

**Linking stories**, after step 4: `python3 -I scripts/ingest.py movers --list` prints the existing movers.
When a story you just published is clearly about one of them (the company raised, the app launched in a new
market, the skill shipped a release), link it:
`python3 -I scripts/ingest.py link <mover-id> <story-url> [<story-url> ...]`. Link only clear matches; a
passing mention is not one. Missing stories are reported, never created.

**When a founder asks to add or update one**, write a JSON file under `${TMPDIR:-/tmp}/keeping-up-sv/`
(never in a repo, teamctx or chat) and run `python3 -I scripts/ingest.py movers <file.json>` (a run's own
proposals use `movers --propose`, which also refuses existing movers). The file:

```json
{"movers": [{
  "id": "example-robotics", "name": "Example Robotics", "kind": "company",
  "url": "https://example.com", "one_liner": "Robot arms for small kitchens (max 200 chars).",
  "summary": "Neutral facts only, max 800 chars.",
  "traction": [{"label": "Restaurants live", "value": "120", "source_url": "https://example.com/blog/120", "as_of": "2026-10-01"}],
  "markets_live": ["US", "UK"], "claim_status": "confirmed", "tags": ["robotics"],
  "image_url": "https://example.com/logo.png", "image_kind": "logo",
  "notes": {"take": "How it helps us", "learn": "What to learn", "localize": "Where to localize",
            "score": {"lines": {"demand": 10, "problem": 6, "fit": 9, "wedge": 6, "market": 6, "future": 9, "moat": 4, "whitespace": 3},
                      "market": "The named target market", "verdict": "One line: in or out, and why."}},
  "item_urls": ["https://techcrunch.com/..."]
}]}
```

- Everything except `notes` is **public** and indexed: what it is, its traction, its sources. Every traction
  line needs a `source_url` (https) you actually opened and the date the figure was true; no source, no line
  (at most 8). `claim_status` is `confirmed` (the company states it), `reported` or `unverified`.
- `notes` is the founders' **private** take; it is shown only after the founders' unlock and never in public
  HTML, the sitemap, RSS, llms.txt or JSON-LD. Write it only from what the founder told you or from research
  you did and cited. Leaving a note field out keeps what is stored; `null` clears it.
- `notes.score` is the office-hours rubric (teamctx k:878, unchanged) for the **localized** idea in one named
  market: the eight lines with their maximums (demand 25, problem 10, fit 15, wedge 10, market 10, future 15,
  moat 10, whitespace 5); the server adds up the total. Under 40 is below the cutoff: say so in the verdict.
- To change only the take or the score later (after an office-hours run, say), put
  `{"score": {...}}` (and/or take / learn / localize) in a file and run
  `python3 -I scripts/ingest.py notes <mover-id> <file.json>`; the public facts stay as they are.
- Reruns update by `id`. The script checks the file first, then verifies every mover is stored and that its
  public page (for movers on the list) shows the name and **none** of the note text; exit code 0 means both
  passed. Proposed movers have no public page to check.
- `python3 -I scripts/ingest.py archive <mover-id>` stops watching one (it leaves the site; the founder can
  restore it by sending it again with `"status": "watching"`).

## Research runs

When a founder asks for research (a prompt from a call, a lead, a rerun) and you score ideas on the rubric,
record the result on the founders' private **Research** page (`/sv/movers/research`) so it is not only in a
file on someone's Desktop. Write a JSON file under `${TMPDIR:-/tmp}/keeping-up-sv/` or next to the write-up
(never in a repo, teamctx or chat) and run `python3 -I scripts/ingest.py research <file.json>`:

```json
{"runs": [{
  "id": "2026-10-22-example-lead", "title": "Example lead", "run_date": "2026-10-22", "position": 3,
  "outcome": "below_cutoff", "verdict": "Out, 30",
  "summary": "What was asked and the answer, max 1500 chars.", "method": "Where you looked, max 1500 chars.",
  "findings": [{"title": "The lead in bold", "text": "The finding, with its numbers"}, {"group": "How they grew", "text": "..."}],
  "sources": [{"label": "Example source", "url": "https://example.com/page"}],
  "refs": ["03-example-lead.md", "k:123"],
  "ideas": [{"idea": "Reference -> our localized version", "market": "The named market", "total": 30,
             "lines": {"demand": 0, "problem": 5, "fit": 6, "wedge": 6, "market": 6, "future": 4, "moat": 2, "whitespace": 1},
             "note": "One line", "mover_id": "an-existing-mover-id"}]
}]}
```

- `outcome` is `makes_list` (an idea at 40 or more), `below_cutoff` (scored, none at 40), `no` (a verdict
  without a score), `replaced` (folded into another run) or `input` (a long list that fed other runs).
- Every idea is in, **including those under 40**: this page is where they are kept. `lines` must add up to
  `total`; leave `lines` out only when the source recorded just the total.
- Ideas at 40 or more also go to the desk queue as movers (`ingest.py movers`); give the idea that mover's
  `mover_id` so the page links them. An unknown `mover_id` is reported and left unlinked.
- Plain text only, numbers with their source in the write-up; no customer names or customer data.
- A run sent again replaces the stored one, ideas included. The script verifies every run is stored with its
  ideas and that the page shows nothing to a browser that is not unlocked; `--list` prints what is stored.

## When something is wrong

- `could not start a run: HTTP 502` / `feed API unreachable`: the VM API or its tunnel is down. On the VM:
  `tmux ls | grep keeping-up-sv`, `bash ~/keeping-up-sv/sv/ops/start.sh`, logs in
  `~/.local/state/keeping-up-sv/logs/`. The site rediscovers a new tunnel URL by itself within minutes.
- `HTTP 403 invalid ingest token`: your token was rotated or revoked; redo the one-time setup.
- A run that died midway stays `running` and is marked failed after 3 hours; just rerun the date.
- Page check failed but DB check passed: the page cache refreshes within 5 minutes; open the page again.
- Reddit sources `ERR ... HTTP 429` or `not tried: Reddit rate-limited this run`: expected some days. Do not
  rerun collect just for Reddit; the next run tries again.
- `skip xiaohongshu` / `skip x-search`: no confirmed login (see step 1). The edition is complete without
  them.
- `chart movers: N/24 feeds`: a few chart feeds time out some days; the lists use the ones that loaded, and
  `chart-movers.json` names the missing ones. Snapshots live in `~/.local/state/keeping-up-sv/charts/`
  (or `$KEEPING_UP_SV_STATE`); deleting them only means the next run has nothing to compare.

More: `sv/README.md` in https://github.com/gaurpulkit/Proactive-Agent-Reach and the teamctx items tagged
`keeping-up-sv`.
