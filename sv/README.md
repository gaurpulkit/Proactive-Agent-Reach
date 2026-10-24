# Keeping Up with Silicon Valley

A daily, source-linked feed of startup funding, launches, founder discussions and VC news, published at
**https://induscore.site/sv**. Stories are collected and curated once a day by Claude Code (either founder's),
stored in a dedicated database on the IndusCore prod VM, and served by a Next.js site on Vercel.

This directory is self-contained; nothing outside `sv/` belongs to it, and it does not touch the Agent-Reach
package that shares the repository.

```
sv/
  web/     Next.js 15 site (Vercel project induscore-sv, basePath /sv)
  api/     FastAPI read + ingest API and SQL migrations (runs on the prod VM)
  skill/   the Claude Code skill /aggregate-silicon-valley (shipped to both laptops via teamctx)
  ops/     VM setup, deploy, tunnel, skill publishing
  tests/   unit tests for the skill's pure logic (stdlib only)
```

## How it fits together

```
Claude Code (Pulkit or Michael)                         visitors, crawlers
  /aggregate-silicon-valley                                  │
  collect.py ── RSS, HN, YC, YouTube, Reddit, charts         ▼
  curate (Claude) ── curated.json                   induscore.site  (Vercel project ai-native-crm-website)
  ingest.py ──POST /sv/api/ingest/* ───────────────▶   rewrite /sv/* ─▶ induscore-sv (Next.js, Vercel)
                                                                           │ server-side only, read token
                                                                           ▼
                                           ntfy topic (HMAC-signed tunnel URL) ◀── discovery
                                                                           │
                                       Cloudflare quick tunnel ─▶ prod VM 127.0.0.1:8793 (FastAPI)
                                                                           ▼
                                                     Postgres 17, database induscore_sv (role sv_app)
```

- **URL**: `induscore.site/sv`. The marketing project rewrites `/sv` and `/sv/:path*` to
  `https://induscore-sv.vercel.app/sv/...` (multi-zone). Subdomains were not used because `*.induscore.site`
  is the tenant wildcard of the IndusCore app.
- **Database**: `induscore_sv` on the prod VM's Postgres 17, owned by role `sv_app`, CONNECT revoked from
  PUBLIC, listening on localhost only. Separate from every IndusCore database. Test copy: `induscore_sv_test`.
- **VM → Vercel**: the VM only admits SSH, so the API is exposed by its own Cloudflare quick tunnel. Its URL
  changes when the tunnel restarts; `ops/run-tunnel.sh` announces each new URL on an ntfy topic, signed with
  HMAC-SHA256, and the site trusts only a correctly signed `*.trycloudflare.com` URL (`web/lib/api.ts`).
  Same pattern as teamctx.
- **Freshness**: pages render per request from Next's data cache (5 minutes); every successful ingest call
  clears that cache, so new stories appear on the next request without a redeploy.
- **Schedule**: the daily edition is run by Claude Code, headless, from `skill/aggregate-silicon-valley/scripts/daily.sh`
  at 07:00 India time on whichever machine is up first (see "Daily aggregation"). The other VM cron entries tagged
  `keeping-up-sv-*` keep the API and tunnel alive and re-announce the tunnel URL every 6 hours (ntfy keeps
  messages ~12 hours).

## Daily aggregation

In Claude Code: `/aggregate-silicon-valley` (today, Pacific time) or `/aggregate-silicon-valley 2026-10-22`.
The skill (`skill/aggregate-silicon-valley/SKILL.md`) collects, has Claude curate, persists, then verifies
the database and the public page. Reruns and backfills are safe: stories upsert by canonical URL and each
story belongs to exactly one edition.

**Every morning, unattended.** `bash skill/aggregate-silicon-valley/scripts/install-schedule.sh --at 07:00` puts
`daily.sh` on a 20-minute launchd (Mac) or cron (Linux) timer on that machine. From the start time (India time)
it runs `/aggregate-silicon-valley <edition>` with `claude -p` for the Pacific date that is ending, unless the
ingest API shows a run for that edition finished after 07:00 India time today, from any machine. Each founder's
laptop and the VM can carry it, staggered (for example 07:00, 07:45, 08:30); a duplicate run only upserts. The
machine needs its own ingest token and a Claude Code login that works headless (`claude -p "say ok"`; on the VM,
`claude setup-token` and `CLAUDE_CODE_OAUTH_TOKEN` in `~/.config/keeping-up-sv/schedule.env`). Logs:
`~/.local/state/keeping-up-sv/daily/<edition>.log`.

One-time per person: an ingest token in `~/.config/keeping-up-sv/ingest.env` (command in SKILL.md). Tokens
are per person (`manage.py issue-token <name>`), stored only as sha256 on the VM, revocable
(`manage.py revoke-token <name>`), and the run records who ran it.

### Sources

Configured in `skill/aggregate-silicon-valley/sources.json` (50 enabled; every source added on 2026-10-22 was
checked live that day). By what they need:

**Work without a login** (public feeds and APIs, no keys):

- News and launches: Hacker News (Algolia API), YC Launches (JSON), Product Hunt, TechCrunch, Crunchbase News,
  Techmeme, StrictlyVC, GeekWire, SiliconANGLE, The Verge, Wired, The Next Web.
- Investors: Sequoia, a16z, Lenny's Newsletter, Not Boring, Tomasz Tunguz, Latent Space.
- Founder and operator essays: Sam Altman, Simon Willison (long-form entries), Elad Gil, Benedict Evans, Andrej
  Karpathy, Dwarkesh Patel, Andrew Chen, Stripe's blog; from China and India, Interconnected (Kevin Xu), ChinAI
  (Jeffrey Ding) and Sajith Pai (Blume Ventures).
- Company and lab blogs: OpenAI, Google AI, Hugging Face, GitHub.
- YouTube channels, one public Atom feed each (`youtube.com/feeds/videos.xml?channel_id=...`): Y Combinator,
  a16z, Lenny's Podcast, Greg Isenberg, Starter Story, My First Million, All-In, Latent Space, Every (AI & I),
  硅谷101 and Think School. A video becomes a story whose URL is the watch page, so the site shows it with the
  existing YouTube embed (click to load, youtube-nocookie.com). Shorts are dropped, except Starter Story's.
  Transcripts are never collected or stored; while curating, the skill may read one with yt-dlp (agent-reach's
  YouTube channel) to judge a video.
- App Store charts: Apple's public top-100 feeds (top free and top paid iPhone apps) for us, in, gb, es, id,
  au, br, de, sg, my, ae and sa. These are not news: `charts.py` keeps a dated snapshot on the laptop
  (`~/.local/state/keeping-up-sv/charts/`) and writes `chart-movers.json` (apps rising in a country, apps
  that work abroad but not in the US, US hits missing in India and Southeast Asia), which the run uses to
  propose movers. The first run has nothing to compare and says so.

**Best effort:**

- Reddit: four sources, each one public RSS feed of the day's top posts for a group of subreddits (r/startups,
  r/Entrepreneur, r/indiehackers, r/ycombinator, r/venturecapital; r/SaaS, r/SideProject; r/ClaudeAI,
  r/LocalLLaMA; r/StartUpIndia, r/indianstartups). Reddit allowed one anonymous request a minute when checked,
  so one shared fetcher with a descriptive User-Agent asks one feed at a time and waits as long as Reddit's
  rate-limit headers say (a full pass takes two to four minutes). A 429 is retried once; then that source
  fails and the rest are marked "not tried". Each failure shows on the edition. No login, no API keys.

**Need a founder's login** (optional):

- 小红书 and X search, for a short keyword list in `sources.json`, through agent-reach's OpenCLI bridge, which
  drives the founder's own logged-in Chrome. It runs only when `agent-reach doctor --json` reports the channel
  ok through OpenCLI, or when the founder passes `collect.py --login-sources` (one read-only search checks the
  login first). Otherwise the edition records "skipped: needs login (run agent-reach configure)", which does
  not make the run partial. It never logs in, never reads or stores cookies and never goes behind a paywall.
  A 小红书 note is stored as its bare note URL; the session's `xsec_token` stays on the laptop.
- X posts that other sources link to are embedded with X's own widget, with or without a login.

**Left out on purpose:**

- Stratechery, SemiAnalysis, SaaStr and Naval answer 403 to automated readers; we respect that.
- Paul Graham: paulgraham.com has no feed, and the only public feed of his essays stops in 2023 and has no
  dates; Hacker News carries new essays within hours. Patrick Collison's site has no feed.

## Movers to watch

`/sv/movers` lists companies, apps and skills gaining traction that the founders want to learn from, kept
apart from the daily news. A story can point at a mover (`mover_items`); its page then shows "Mover: <name>".
The public list sorts by **Trending** (most stories in the last 14 days) or **Newest**, and
`/sv/movers/updates` shows movers added and new stories about them.

**The founders' desk.** Behind the unlock, the same pages become a decision desk:

- a **queue** of proposed movers (`status = 'proposed'`): a run or a founder proposes (`ingest.py movers`
  without `status`), a founder publishes or passes from the desk; until then it is on no public page;
- the founders' take, an **office-hours score** (rubric k:878, eight lines, 100 points, for one named market),
  **votes** with reasons (one per founder, recorded under the name chosen at unlock), and a **stage** (new,
  researching, building, passed);
- sorting by fit, votes, trending or newest, and filters by kind, stage and status;
- on each mover, **next steps** to copy into Claude Code: an REA reverse-engineering prompt (for skills: take
  the source apart), a build brief, and a scoring prompt whose result goes back with `ingest.py notes`;
- a log of every change (`mover_events`) on `/sv/movers/updates`, with who did what.

Each daily run proposes at most three movers (`ingest.py movers --propose --publish-min 45`), from the App Store chart movers and
the day's strongest signals (a round, a public revenue figure, a chart jump, unusual engagement). It reads the
votes and reasons with `ingest.py feedback` first, so the founders' calls steer what gets found next, and the
script refuses a proposal for anything already on the desk (same id, name or site). A proposal scoring 45 or
more on the rubric goes straight on the public list (the founders' rule since 2026-10-24); the rest wait in the
queue, and a founder can still pass or archive any of them from the desk.

**Research.** `/sv/movers/research` (founders only; anyone else gets a 404) keeps every research run: a prompt
from a founders' call, a lead, a rerun on new sources. Each run has its verdict, how it was found, what it found
and its sources, and every idea it scored on the rubric, including the ones under the 40 cutoff that never
reach the queue. The page ranks all ideas with the cutoff drawn in, filters by run, and links an idea to its
mover on the desk when it became one. Runs live in `research_runs` and `research_ideas` (migration 005), are
recorded with `ingest.py research <file.json>` (a run sent again replaces the stored one) and are served only by
`/v1/private/research`. The write-ups' data files stay outside the repo.

**Public facts, private take.**

- Everyone sees each mover's neutral facts: what it is, its traction (every line with its source and the date
  it was true), markets, claim status and linked news. These are indexed: `/sv/movers`, `/sv/movers/<id>`
  (`WebPage` + `BreadcrumbList`), the sitemap and one line in llms.txt. Kind filters (`?kind=`) are
  `noindex, follow`. RSS stays news-only.
- The founders' take (how it helps us, what to learn, where to localize), score and stage live in
  `mover_notes`, votes in `mover_votes`, and who did what in `mover_events`; no public query touches them.
  Only `/v1/private/*` serves them and takes the desk's votes, stages and publishing, behind its own token
  (`SV_FOUNDERS_TOKEN_SHA256`; the read token gets 403, and so does a deployment where both are equal). The
  public updates endpoint serves only "added" and "new stories" lines, without who or why, and only for
  movers on the list.
- The site asks for it only on a request with a valid `sv_founders` cookie, with `cache: 'no-store'` (never in
  Next's data cache). Pages that check the cookie render per request with `Cache-Control: private, no-cache,
  no-store`, so neither Vercel nor induscore.site's proxy can hand one founder's page to anyone else. It never
  appears in HTML served without the cookie, the sitemap, RSS, llms.txt or JSON-LD. Archived movers leave every
  public view.

**Unlocking.** Founders bookmark `https://induscore.site/sv/movers/unlock` (`noindex`, linked from nowhere).
The passphrase's sha256 is compared with `SV_FOUNDERS_PASSPHRASE_SHA256` in constant time, with a one-second
delay on a miss. The form also asks who is unlocking. A match sets
`sv_founders = <exp>.<founder>.<hmacSHA256("<exp>.<founder>", SV_UNLOCK_SECRET)>`: httpOnly, Secure,
`SameSite=Strict`, `Path=/sv`, 30 days. "Lock" on the founders bar clears it. Because of `SameSite=Strict`, a
link to /sv/movers clicked on another site opens the public view; reload, or use the bookmark.

**Archive through the script** (`ingest.py archive <id>`): it goes through the site's ingest route, which clears
the page cache. Next caches only 200 responses, so after a direct API call on the VM a cached mover page can
outlive the archive until any successful ingest POST clears the cache.

## Configuration

| Where | Name | What |
| --- | --- | --- |
| Vercel `induscore-sv` (prod + preview) | `SV_READ_TOKEN` | read token; its sha256 is `SV_READ_TOKEN_SHA256` on the VM |
| Vercel | `SV_ANNOUNCE_SECRET`, `SV_NTFY_TOPIC` | verify the signed tunnel URL; same values as the VM |
| Vercel (optional) | `SV_API_URL` | fallback API URL when no announcement is found |
| Vercel (production only) | `SV_FOUNDERS_TOKEN` | the founders' desk token (reads the take, scores and votes; records votes, stages and publishing); its sha256 is `SV_FOUNDERS_TOKEN_SHA256` on the VM; never the same as `SV_READ_TOKEN` |
| Vercel (production only) | `SV_FOUNDERS_PASSPHRASE_SHA256` | sha256 (hex) of the founders' passphrase; the passphrase itself is stored nowhere |
| Vercel (production only) | `SV_UNLOCK_SECRET` | HMAC key for the `sv_founders` cookie, at least 32 characters |
| VM `~/.config/keeping-up-sv/env` (600) | `SV_DATABASE_URL`, `SV_TEST_DATABASE_URL`, `SV_PORT`, `SV_READ_TOKEN_SHA256`, `SV_ANNOUNCE_SECRET`, `SV_NTFY_TOPIC` | written by `ops/vm-setup.sh` |
| VM env file | `SV_FOUNDERS_TOKEN_SHA256` | added empty by `ops/vm-setup.sh`; while empty, `/v1/private/*` answers 403 and founders see the public list with a notice |
| Each laptop `~/.config/keeping-up-sv/ingest.env` (600) | `SV_INGEST_TOKEN` (+ optional `SV_BASE_URL`) | personal ingest token |

Rotating the read token: generate one locally, `vercel env add SV_READ_TOKEN production` from stdin, put its
sha256 into the VM env file, `bash sv/ops/deploy-vm.sh main`, redeploy the site.

Issuing or rotating the founders' view secrets (from `sv/web`, linked to `induscore-sv`; nothing is echoed or
written to shell history). Leave them off preview deployments: without them a page never unlocks.

```bash
# founders' desk token: Vercel gets the token, the VM gets its sha256
T=$(python3 -c 'import secrets; print("svf_" + secrets.token_urlsafe(32))')
printf %s "$T" | vercel env add SV_FOUNDERS_TOKEN production
printf %s "$T" | shasum -a 256 | cut -d' ' -f1    # -> SV_FOUNDERS_TOKEN_SHA256= in the VM env file
unset T
# passphrase: pick a long one (five or more random words); only its sha256 leaves your terminal
read -rs P && printf %s "$P" | shasum -a 256 | cut -d' ' -f1 | tr -d '\n' | vercel env add SV_FOUNDERS_PASSPHRASE_SHA256 production; unset P
# cookie signing key
python3 -c 'import secrets; print(secrets.token_urlsafe(48), end="")' | vercel env add SV_UNLOCK_SECRET production
```

Then `bash sv/ops/deploy-vm.sh main` (the API reads its env at start) and `vercel deploy --prod`. To replace a
value, `vercel env rm <NAME> production` first. Share the passphrase between the founders in person or a
password manager, never teamctx, chat or git. A new passphrase does not sign anyone out; a new
`SV_UNLOCK_SECRET` signs every browser out at once, so rotate both if the passphrase leaks.

## Deploying

- **API** (from either laptop): `SV_VM=<vm ssh target> SV_VM_KEY=<path to the team key> bash sv/ops/deploy-vm.sh main`.
  The VM host and key are in teamctx (prod VM workflow), not in this public repo.
  Clones/pulls into `~/keeping-up-sv` on the VM, applies migrations, restarts tmux `keeping-up-sv:api`
  (the tunnel keeps its URL).
- **Site**: `cd sv/web && vercel deploy --prod` (project `induscore-sv`, linked by `vercel link --project induscore-sv`).
  No Git integration on purpose: upstream Agent-Reach syncs must not trigger site builds.
- **Skill**: after changing `skill/`, `SV_VM=... bash sv/ops/publish-skill.sh`, then `ctx update` on each laptop.

## Tests

```bash
python3 -m pytest -q sv/tests                                   # skill logic, no network
cd sv/web && npm test && npx tsc --noEmit && npx next build     # site (incl. unlock cookie + ingest path tests)
# API, on the VM against induscore_sv_test:
ssh <vm> 'cd ~/keeping-up-sv/sv/api && set -a && . ~/.config/keeping-up-sv/env && set +a && .venv/bin/python -m pytest -q tests'
```

## SEO

Server-rendered pages; unique titles, descriptions and canonicals; `CollectionPage` + `ItemList` on editions,
`WebPage` + `isBasedOn` (the original source) + `BreadcrumbList` on stories, `AboutPage` on the methodology.
`/sv/sitemap.xml` (listed in the apex robots.txt) holds hub pages, every edition, topics with at least three
stories, and every story. Filtered views, search, empty dates and thin topics are `noindex, follow`.
`/sv/feed.xml` (RSS) and `/sv/llms.txt` exist for readers and assistants. Wrong story slugs 308 to the
canonical slug; invalid or future dates 404.

## Analytics

Vercel Web Analytics of the `induscore.site` project (`ai-native-crm-website`), which already has it enabled:
the zone loads `/_vercel/insights/script.js` from the host root and reports `/sv/...` paths
(`components/SiteAnalytics.tsx`, `@vercel/analytics/react` 1.5.0). Search terms are stripped before
sending; no cookies. Filter the dashboard by path `/sv`.

## Troubleshooting

- `https://induscore.site/sv/api/health` shows whether the site reaches the API and the latest edition.
- On the VM: `tmux ls | grep keeping-up-sv`; `bash ~/keeping-up-sv/sv/ops/start.sh`; logs in
  `~/.local/state/keeping-up-sv/logs/`; current tunnel in `~/.local/state/keeping-up-sv/tunnel_url`.
- A run that died mid-way stays `running` and is marked failed after 3 hours; rerun the date.

## Backups

`induscore_sv` is small (metadata only). Back it up with
`sudo -u postgres pg_dump -Fc induscore_sv > induscore_sv-$(date +%F).dump` and restore with `pg_restore -d
induscore_sv`. The schema is reproducible from `api/migrations/` (`manage.py migrate`), and every story links
to its original, so losing the database loses the archive's curation, not the sources.
