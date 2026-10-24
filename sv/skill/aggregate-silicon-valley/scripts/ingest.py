#!/usr/bin/env python3
"""Persist a curated edition and prove it landed.

  python3 ingest.py --date YYYY-MM-DD --curated curated.json --sources sources.json
  python3 ingest.py --check                     # token + API reachability only
  python3 ingest.py movers movers.json          # propose/update movers (new ones wait on the founders' desk)
  python3 ingest.py movers --propose new.json   # a run's proposals: at most 3, new ids only, scored
  python3 ingest.py movers --propose new.json --publish-min 45   # ...and publish the ones scoring 45 or more
  python3 ingest.py movers --list               # every mover: id, kind, status, name
  python3 ingest.py link MOVER_ID STORY_URL...  # point stories already in the feed at a mover
  python3 ingest.py archive MOVER_ID            # stop watching a mover (hides it from the site)
  python3 ingest.py notes MOVER_ID notes.json   # update only the take or the office-hours score
  python3 ingest.py feedback                    # the founders' votes, reasons and stages
  python3 ingest.py research research.json      # record research runs on the founders' Research page (private)
  python3 ingest.py research --list             # the research runs stored: id, verdict, ideas, best score

Talks only to the public site's ingest endpoint (default
https://induscore.site/sv/api/ingest), which forwards to the VM API with your
own ingest token. Steps: start a run, send items in batches, finish the run
with every source's status, then verify (1) the database holds every story
this run placed in the edition and (2) the public edition page shows them.

Token: SV_INGEST_TOKEN in the environment, or a line SV_INGEST_TOKEN=... in
~/.config/keeping-up-sv/ingest.env. Base URL: SV_BASE_URL (optional).
Safe to rerun: the server upserts by canonical URL and keeps one row per
story per edition.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect import SSL_CONTEXT, UA  # noqa: E402
import svlib  # noqa: E402

CONFIG = Path.home() / ".config" / "keeping-up-sv" / "ingest.env"
BATCH = 40


def setting(name: str, default: str | None = None) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    if CONFIG.exists():
        for line in CONFIG.read_text().splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return default


def call(method: str, url: str, token: str | None = None, body: dict | None = None, timeout: float = 60) -> tuple[int, dict | str]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"User-Agent": UA, "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(3):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as r:
                raw = r.read().decode()
                status = r.status
        except urllib.error.HTTPError as e:
            raw, status = e.read().decode(errors="replace"), e.code
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if attempt == 2:
                return 0, f"{type(e).__name__}: {e}"
            time.sleep(3 + attempt * 5)
            continue
        if status in (502, 503, 504) and attempt < 2:
            time.sleep(3 + attempt * 5)
            continue
        try:
            return status, json.loads(raw)
        except json.JSONDecodeError:
            return status, raw
    return 0, "unreachable"


REQUIRED = ("url", "title", "source", "source_name", "category", "score")


def run_status(statuses: list[dict], totals: dict, errors: list[str]) -> str:
    """failed: nothing stored; partial: a source failed or an item was refused;
    complete otherwise. A source skipped on purpose (it needs a founder's login,
    "skipped: ..." on the edition) is not a failure."""
    if totals["inserted"] + totals["updated"] + totals["merged"] == 0:
        return "failed"
    if any(not s["ok"] and not s.get("skipped") for s in statuses) or totals["rejected"] or errors:
        return "partial"
    return "complete"


def load_curated(path: Path) -> list[dict]:
    doc = json.loads(path.read_text())
    items = doc["items"] if isinstance(doc, dict) else doc
    problems = [f"item {i}: missing {k}" for i, it in enumerate(items) for k in REQUIRED if it.get(k) in (None, "")]
    if problems:
        sys.exit("curated file is incomplete:\n  " + "\n  ".join(problems[:20]))
    allowed = {"url", "title", "source", "source_name", "category", "tags", "excerpt", "summary", "author",
               "published_at", "claim_status", "funding", "embed_url", "mentions", "score", "score_detail",
               "image_url", "image_kind"}
    return [{k: v for k, v in it.items() if k in allowed and v is not None} for it in items]


# ---------------------------------------------------------------- movers

MOVER_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,59}$")
RESERVED_MOVER_IDS = {"unlock", "lock", "updates", "research"}
MOVER_KINDS = {"company", "app", "skill"}
MOVER_FIELDS = {"id", "name", "kind", "url", "one_liner", "summary", "traction", "markets_live", "claim_status",
                "tags", "image_url", "image_kind", "status", "notes", "item_urls"}
TRACTION_FIELDS = {"label", "value", "source_url", "as_of"}
NOTE_FIELDS = {"take": 1500, "learn": 1000, "localize": 1000}
# The office-hours rubric (teamctx k:878): line -> maximum points.
RUBRIC = {"demand": 25, "problem": 10, "fit": 15, "wedge": 10, "market": 10, "future": 15, "moat": 10, "whitespace": 5}
MOVER_STATUSES = ("proposed", "watching", "archived")


def score_problems(score: object) -> list[str]:
    """notes.score: {"lines": {8 rubric lines}, "market": "named market", "verdict": "..."}."""
    if not isinstance(score, dict):
        return ["notes.score must be an object with lines, market and verdict"]
    out = []
    if set(score) - {"lines", "market", "verdict", "total"}:
        out.append(f"notes.score has unknown fields {sorted(set(score) - {'lines', 'market', 'verdict', 'total'})}")
    lines = score.get("lines")
    if not isinstance(lines, dict) or set(lines) != set(RUBRIC):
        out.append(f"notes.score.lines must be exactly {', '.join(RUBRIC)}")
    else:
        for k, cap in RUBRIC.items():
            if not isinstance(lines[k], int) or not 0 <= lines[k] <= cap:
                out.append(f"notes.score.lines.{k} must be a whole number from 0 to {cap}")
        if "total" in score and score["total"] != sum(v for v in lines.values() if isinstance(v, int)):
            out.append("notes.score.total is not the sum of the lines (leave it out; the server adds it up)")
    if not isinstance(score.get("market"), str) or not score["market"].strip() or len(score["market"]) > 120:
        out.append("notes.score.market must name the target market (1-120 characters)")
    if score.get("verdict") is not None and (not isinstance(score["verdict"], str) or len(score["verdict"]) > 300):
        out.append("notes.score.verdict must be text of at most 300 characters")
    return out


def notes_problems(notes: object) -> list[str]:
    if not isinstance(notes, dict):
        return ["notes must be an object with take / learn / localize / score"]
    out = []
    allowed = set(NOTE_FIELDS) | {"score"}
    if set(notes) - allowed:
        out.append(f"notes has unknown fields {sorted(set(notes) - allowed)}")
    for k, cap in NOTE_FIELDS.items():
        if isinstance(notes.get(k), str) and len(notes[k]) > cap:
            out.append(f"notes.{k} is over {cap} characters")
    if notes.get("score") is not None:
        out += score_problems(notes["score"])
    return out
MOVER_BATCH = 50


def mover_problems(m: object) -> list[str]:
    """What the server would refuse, caught before anything is sent. The
    server checks again; this is for a clear message, not for safety."""
    if not isinstance(m, dict):
        return ["not an object"]
    out: list[str] = []
    unknown = sorted(set(m) - MOVER_FIELDS)
    if unknown:
        out.append(f"unknown fields {unknown} (the take goes under notes: take / learn / localize)")
    for k in ("id", "name", "kind", "url", "one_liner"):
        if not isinstance(m.get(k), str) or not m[k].strip():
            out.append(f"missing {k}")
    mid = m.get("id")
    if isinstance(mid, str) and (not MOVER_ID.match(mid) or mid in RESERVED_MOVER_IDS):
        out.append(f"id {mid!r} must be a lowercase slug (a-z, 0-9, -), 2-60 characters, not unlock/lock/updates/research")
    if m.get("kind") is not None and m.get("kind") not in MOVER_KINDS:
        out.append(f"kind must be one of {sorted(MOVER_KINDS)}")
    if isinstance(m.get("one_liner"), str) and len(m["one_liner"]) > 200:
        out.append("one_liner is over 200 characters")
    if isinstance(m.get("summary"), str) and len(m["summary"]) > 800:
        out.append("summary is over 800 characters")
    if m.get("claim_status", "reported") not in ("confirmed", "reported", "unverified"):
        out.append("claim_status must be confirmed, reported or unverified")
    if m.get("status") is not None and m["status"] not in MOVER_STATUSES:
        out.append("status must be proposed, watching or archived (leave it out: a new mover is proposed)")
    traction = m.get("traction") or []
    if not isinstance(traction, list) or len(traction) > 8:
        out.append("traction must be a list of at most 8 lines")
        traction = []
    for i, t in enumerate(traction):
        if not isinstance(t, dict):
            out.append(f"traction[{i}] is not an object")
            continue
        if set(t) - TRACTION_FIELDS:
            out.append(f"traction[{i}] has unknown fields {sorted(set(t) - TRACTION_FIELDS)}")
        if not str(t.get("source_url") or "").startswith("https://"):
            out.append(f"traction[{i}] needs a source_url (https): no source, no line")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(t.get("as_of") or "")):
            out.append(f"traction[{i}] needs as_of as YYYY-MM-DD")
        for k in ("label", "value"):
            if not str(t.get(k) or "").strip() or len(str(t.get(k))) > 120:
                out.append(f"traction[{i}].{k} must be 1-120 characters")
    markets = m.get("markets_live") or []
    if not isinstance(markets, list) or len(markets) > 8:
        out.append("markets_live must be a list of at most 8")
    if m.get("notes") is not None:
        out += notes_problems(m["notes"])
    urls = m.get("item_urls") or []
    if not isinstance(urls, list) or len(urls) > 50:
        out.append("item_urls must be a list of at most 50 story URLs")
    return out


MAX_PROPOSALS = 3
# Sites that host many products: there a mover is identified by its page, not the host.
SHARED_HOSTS = {"apps.apple.com", "play.google.com", "github.com", "x.com", "youtube.com", "reddit.com", "producthunt.com",
                "ycombinator.com", "huggingface.co", "chromewebstore.google.com", "xiaohongshu.com", "medium.com",
                "substack.com", "linkedin.com"}


def _norm_name(name: object) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(name or "").casefold())


def site_key(url: object) -> str | None:
    """What makes two movers' URLs the same product: the host, or on a shared
    host the page (an App Store app by its id, in any country)."""
    try:
        key = svlib.url_key(str(url or ""))
    except ValueError:
        return None
    host, _, path = key.partition("/")
    if host == "apps.apple.com":
        m = re.search(r"/id(\d+)", "/" + path)
        return f"apps.apple.com/id{m.group(1)}" if m else key
    if host in SHARED_HOSTS:
        return key.split("?")[0].rstrip("/").lower()
    return host


def proposal_problems(movers: list[dict], existing: list[dict]) -> list[str]:
    """The daily run's rules for proposals, checked before anything is sent:
    at most MAX_PROPOSALS, no status (they wait on the founders' desk), never a
    mover that already exists (same id, same name or same site), and each with
    a sourced traction line, a private take and an office-hours score."""
    out = []
    if len(movers) > MAX_PROPOSALS:
        out.append(f"a run proposes at most {MAX_PROPOSALS} movers; this file has {len(movers)}")
    ids = {m.get("id"): m for m in existing}
    names = {_norm_name(m.get("name")): m.get("id") for m in existing if _norm_name(m.get("name"))}
    sites = {site_key(m.get("url")): m.get("id") for m in existing if site_key(m.get("url"))}
    for m in movers:
        mid = m.get("id")
        if mid in ids:
            out.append(f"{mid}: already exists ({ids[mid].get('status')}); never propose an existing mover "
                       "(update its take with `ingest.py notes`, or link stories with `ingest.py link`)")
        name, site = _norm_name(m.get("name")), site_key(m.get("url"))
        if name and names.get(name) not in (None, mid):
            out.append(f"{mid}: has the same name as the existing mover {names[name]}")
        if site and sites.get(site) not in (None, mid):
            out.append(f"{mid}: has the same site as the existing mover {sites[site]} ({site})")
        if "status" in m:
            out.append(f"{mid}: a proposal has no status field (it stays proposed until a founder publishes it)")
        if not m.get("traction"):
            out.append(f"{mid}: needs at least one public traction line with its source_url and as_of date")
        notes = m.get("notes") if isinstance(m.get("notes"), dict) else {}
        if not str(notes.get("take") or "").strip():
            out.append(f"{mid}: needs notes.take, the private take")
        if not isinstance(notes.get("score"), dict):
            out.append(f"{mid}: needs notes.score, the office-hours rubric for one named market")
    return out


def score_total(m: dict) -> int:
    """A mover's office-hours total: the sum of its rubric lines (0 when unscored)."""
    score = (m.get("notes") or {}).get("score") if isinstance(m.get("notes"), dict) else None
    lines = score.get("lines") if isinstance(score, dict) else None
    return sum(v for v in lines.values() if isinstance(v, int)) if isinstance(lines, dict) else 0


# ---------------------------------------------------------------- research

RESEARCH_CUTOFF = 40
RESEARCH_OUTCOMES = ("makes_list", "below_cutoff", "no", "replaced", "input")
RESEARCH_FIELDS = {"id", "title", "run_date", "outcome", "verdict", "summary", "method", "findings", "sources", "refs",
                   "position", "ideas"}
IDEA_FIELDS = {"idea", "market", "total", "lines", "note", "mover_id"}


def _text_problem(where: str, v: object, cap: int, required: bool = False) -> list[str]:
    if v is None and not required:
        return []
    if not isinstance(v, str) or (required and not v.strip()):
        return [f"{where} must be text{' (required)' if required else ''}"]
    if len(v) > cap:
        return [f"{where} is over {cap} characters"]
    if "<" in v and ">" in v:
        return [f"{where} has markup; plain text only"]
    return []


def idea_problems(i: object, where: str) -> list[str]:
    if not isinstance(i, dict):
        return [f"{where} is not an object"]
    out = [f"{where} has unknown fields {sorted(set(i) - IDEA_FIELDS)}"] if set(i) - IDEA_FIELDS else []
    out += _text_problem(f"{where}.idea", i.get("idea"), 200, True) + _text_problem(f"{where}.market", i.get("market"), 120, True)
    out += _text_problem(f"{where}.note", i.get("note"), 300)
    total = i.get("total")
    if not isinstance(total, int) or isinstance(total, bool) or not 0 <= total <= 100:
        out.append(f"{where}.total must be a whole number from 0 to 100")
    lines = i.get("lines")
    if lines is not None:
        if not isinstance(lines, dict) or set(lines) != set(RUBRIC):
            out.append(f"{where}.lines must be exactly {', '.join(RUBRIC)} (or left out)")
        elif any(not isinstance(lines[k], int) or not 0 <= lines[k] <= cap for k, cap in RUBRIC.items()):
            out.append(f"{where}.lines must be whole numbers within each line's maximum")
        elif isinstance(total, int) and sum(lines.values()) != total:
            out.append(f"{where}.total {total} is not the sum of the lines ({sum(lines.values())})")
    if i.get("mover_id") is not None and not (isinstance(i["mover_id"], str) and MOVER_ID.match(i["mover_id"])):
        out.append(f"{where}.mover_id must be a mover's id")
    return out


def research_problems(r: object) -> list[str]:
    """What the server would refuse in one research run, caught before sending."""
    if not isinstance(r, dict):
        return ["not an object"]
    out = [f"unknown fields {sorted(set(r) - RESEARCH_FIELDS)}"] if set(r) - RESEARCH_FIELDS else []
    if not (isinstance(r.get("id"), str) and MOVER_ID.match(r["id"])):
        out.append("id must be a lowercase slug (a-z, 0-9, -), 2-60 characters, e.g. 2026-10-22-example-lead")
    out += _text_problem("title", r.get("title"), 160, True) + _text_problem("verdict", r.get("verdict"), 120, True)
    out += _text_problem("summary", r.get("summary"), 1500) + _text_problem("method", r.get("method"), 1500)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(r.get("run_date") or "")):
        out.append("run_date must be YYYY-MM-DD")
    if r.get("outcome") not in RESEARCH_OUTCOMES:
        out.append(f"outcome must be one of {', '.join(RESEARCH_OUTCOMES)}")
    findings = r.get("findings") or []
    if not isinstance(findings, list) or len(findings) > 20:
        out.append("findings must be a list of at most 20")
        findings = []
    for n, f in enumerate(findings):
        if not isinstance(f, dict) or set(f) - {"group", "title", "text"}:
            out.append(f"findings[{n}] must be an object with text (and optional group, title)")
            continue
        out += (_text_problem(f"findings[{n}].text", f.get("text"), 900, True)
                + _text_problem(f"findings[{n}].title", f.get("title"), 160)
                + _text_problem(f"findings[{n}].group", f.get("group"), 60))
    sources = r.get("sources") or []
    if not isinstance(sources, list) or len(sources) > 15:
        out.append("sources must be a list of at most 15")
        sources = []
    for n, src in enumerate(sources):
        if not isinstance(src, dict) or set(src) != {"label", "url"} or not str(src.get("url") or "").startswith("https://"):
            out.append(f"sources[{n}] must be {{label, url}} with an https url")
    refs = r.get("refs") or []
    if not isinstance(refs, list) or len(refs) > 12 or any(not isinstance(x, str) or not x.strip() or len(x) > 120 for x in refs):
        out.append("refs must be a list of at most 12 short strings (file names, teamctx ids)")
    ideas = r.get("ideas") or []
    if not isinstance(ideas, list) or len(ideas) > 60:
        out.append("ideas must be a list of at most 60")
        ideas = []
    for n, i in enumerate(ideas):
        out += idea_problems(i, f"ideas[{n}]")
    totals = [i["total"] for i in ideas if isinstance(i, dict) and isinstance(i.get("total"), int)]
    if r.get("outcome") == "makes_list" and not any(t >= RESEARCH_CUTOFF for t in totals):
        out.append(f"outcome makes_list needs an idea at {RESEARCH_CUTOFF} or more")
    if r.get("outcome") == "below_cutoff" and (not totals or max(totals) >= RESEARCH_CUTOFF):
        out.append(f"outcome below_cutoff needs scored ideas, all under {RESEARCH_CUTOFF}")
    return out


def load_research(path: Path) -> list[dict]:
    doc = json.loads(path.read_text())
    runs = doc.get("runs") if isinstance(doc, dict) else doc
    if not isinstance(runs, list) or not runs or len(runs) > 20:
        sys.exit('research file must be {"runs": [...]} with 1 to 20 runs')
    problems = [f"run {n} ({r.get('id') if isinstance(r, dict) else '?'}): {p}"
                for n, r in enumerate(runs) for p in research_problems(r)]
    ids = [r.get("id") for r in runs if isinstance(r, dict)]
    problems += [f"id {i!r} appears more than once" for i in sorted({i for i in ids if ids.count(i) > 1})]
    if problems:
        sys.exit("research file has problems:\n  " + "\n  ".join(problems[:30]))
    return runs


def research_texts(runs: list[dict]) -> list[str]:
    """The private strings in a research file (findings, summaries, ideas), for the leak check."""
    out = []
    for r in runs:
        out += [v for v in (r.get("summary"), r.get("method")) if isinstance(v, str) and v.strip()]
        out += [f["text"] for f in r.get("findings") or [] if isinstance(f, dict) and isinstance(f.get("text"), str)]
        out += [i["idea"] for i in r.get("ideas") or [] if isinstance(i, dict) and isinstance(i.get("idea"), str)]
    return out


def load_movers(path: Path) -> list[dict]:
    doc = json.loads(path.read_text())
    movers = doc.get("movers") if isinstance(doc, dict) else doc
    if not isinstance(movers, list) or not movers:
        sys.exit('movers file must be {"movers": [...]} with at least one mover')
    problems = [f"mover {i} ({m.get('id') if isinstance(m, dict) else '?'}): {p}"
                for i, m in enumerate(movers) for p in mover_problems(m)]
    ids = [m.get("id") for m in movers if isinstance(m, dict)]
    problems += [f"id {i!r} appears more than once" for i in sorted({i for i in ids if ids.count(i) > 1})]
    if problems:
        sys.exit("movers file has problems:\n  " + "\n  ".join(problems[:30]))
    return movers


def note_texts(movers: list[dict]) -> list[str]:
    """Every private note string in the file (and a score's verdict), for the leak check."""
    out = []
    for m in movers:
        notes = m.get("notes") or {}
        out += [v for v in notes.values() if isinstance(v, str) and v.strip()]
        verdict = (notes.get("score") or {}).get("verdict") if isinstance(notes.get("score"), dict) else None
        if isinstance(verdict, str) and verdict.strip():
            out.append(verdict)
    return out


def leaked(page: str, secrets_: list[str]) -> list[str]:
    """The note strings (or a 40-character run of one) that appear in a public page."""
    text = " ".join(html.unescape(page).split())
    hits = []
    for s in secrets_:
        flat = " ".join(s.split())
        probes = [flat] if len(flat) <= 40 else [flat[i:i + 40] for i in range(0, len(flat) - 39, 20)]
        if any(p in text for p in probes):
            hits.append(flat[:60])
    return hits


def connection() -> tuple[str, str]:
    base = (setting("SV_BASE_URL", "https://induscore.site/sv") or "").rstrip("/")
    token = setting("SV_INGEST_TOKEN")
    if not token:
        sys.exit(f"No ingest token. Set SV_INGEST_TOKEN or put SV_INGEST_TOKEN=... in {CONFIG} (see the skill's SKILL.md, 'One-time setup').")
    return base, token


def cmd_movers(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="ingest.py movers", description="Add or update movers from a JSON file.")
    ap.add_argument("file", nargs="?", type=Path)
    ap.add_argument("--list", action="store_true", help="print every mover (id, kind, status, name) and exit")
    ap.add_argument("--propose", action="store_true",
                    help=f"a run's proposals: at most {MAX_PROPOSALS}, new ids, names and sites only, no status, each "
                         "with a sourced traction line, notes.take and notes.score; refused before sending otherwise")
    ap.add_argument("--publish-min", type=int, metavar="SCORE",
                    help="with --propose: publish (status watching) the proposals whose office-hours total is at "
                         "least SCORE; the rest wait on the founders' desk")
    args = ap.parse_args(argv)
    if args.publish_min is not None and not args.propose:
        ap.error("--publish-min goes with --propose")
    base, token = connection()
    if args.list:
        status, res = call("GET", f"{base}/api/ingest/movers", token)
        if status != 200:
            sys.exit(f"could not list movers: HTTP {status} {res}")
        for m in res["movers"]:
            print(f"{m['id']}\t{m['kind']}\t{m['status']}\t{m['name']}\t{m['url']}")
        print(f"{len(res['movers'])} movers")
        return
    if not args.file:
        ap.error("give a movers JSON file, or --list")
    movers = load_movers(args.file)
    if args.propose:
        status, listing = call("GET", f"{base}/api/ingest/movers", token)
        if status != 200:
            sys.exit(f"could not list the existing movers to check the proposals against: HTTP {status} {listing}")
        if problems := proposal_problems(movers, listing.get("movers", [])):
            sys.exit("proposals refused, nothing was sent:\n  " + "\n  ".join(problems[:30]))
    # the founders' standing rule (2026-10-24): a proposal at or above the bar goes straight on the list
    published = []
    if args.publish_min is not None:
        for m in movers:
            if score_total(m) >= args.publish_min:
                m["status"] = "watching"
                published.append(m["id"])
    results: list[dict] = []
    errors: list[str] = []
    for i in range(0, len(movers), MOVER_BATCH):
        status, res = call("POST", f"{base}/api/ingest/movers", token, {"movers": movers[i:i + MOVER_BATCH]})
        if status != 200:
            errors.append(f"batch {i // MOVER_BATCH + 1}: HTTP {status} {str(res)[:300]}")
            continue
        results += res["results"]
    rejected = [r for r in results if r["outcome"] == "rejected"]

    # verify 1: the database lists every mover this file sent
    status, listing = call("GET", f"{base}/api/ingest/movers", token)
    stored = {m["id"]: m for m in listing.get("movers", [])} if status == 200 else {}
    sent = [m["id"] for m in movers]
    missing_db = [i for i in sent if i not in stored]

    # verify 2: each watched mover's public page shows its name and none of the notes
    secrets_ = note_texts(movers)
    page_problems: list[str] = []
    for m in movers:
        if m["id"] in missing_db or stored.get(m["id"], {}).get("status") != "watching":
            continue
        st, page = call("GET", f"{base}/movers/{m['id']}?v={int(time.time())}")
        if st != 200 or not isinstance(page, str):
            page_problems.append(f"{m['id']}: public page answered HTTP {st}")
            continue
        if html.escape(m["name"], quote=False) not in page and m["name"] not in html.unescape(page):
            page_problems.append(f"{m['id']}: public page does not show the name yet")
        if hits := leaked(page, secrets_):
            page_problems.append(f"{m['id']}: PUBLIC PAGE SHOWS PRIVATE NOTES: {hits}")
    report = {
        "sent": len(movers), "inserted": sum(r["outcome"] == "inserted" for r in results),
        "updated": sum(r["outcome"] == "updated" for r in results), "rejected": rejected,
        "published": published, "scores": {m["id"]: score_total(m) for m in movers if args.propose},
        "links": {r["id"]: {"linked": r.get("linked", 0), "missing": r.get("missing", [])} for r in results if r["outcome"] != "rejected"},
        "errors": errors, "db_verified": not missing_db and status == 200, "missing_in_db": missing_db,
        "pages_verified": not page_problems, "page_problems": page_problems,
        "pages": [f"{base}/movers/{i}" for i in sent if i not in missing_db],
    }
    print(json.dumps(report, indent=1, ensure_ascii=False))
    sys.exit(0 if report["db_verified"] and not rejected and not errors and not page_problems else 2)


def cmd_link(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="ingest.py link", description="Point stories already in the feed at a mover.")
    ap.add_argument("mover_id")
    ap.add_argument("story_urls", nargs="+")
    args = ap.parse_args(argv)
    if not MOVER_ID.match(args.mover_id):
        ap.error("mover_id must be a mover's id (see: ingest.py movers --list)")
    base, token = connection()
    linked, missing = 0, []
    for i in range(0, len(args.story_urls), MOVER_BATCH):
        status, res = call("POST", f"{base}/api/ingest/movers/{args.mover_id}/link", token,
                           {"item_urls": args.story_urls[i:i + MOVER_BATCH]})
        if status != 200:
            sys.exit(f"could not link: HTTP {status} {res}")
        linked += res["linked"]
        missing += res["missing"]
    print(json.dumps({"id": args.mover_id, "linked": linked, "missing": missing}, indent=1))
    sys.exit(0 if not missing else 2)


def cmd_archive(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="ingest.py archive", description="Stop watching a mover.")
    ap.add_argument("mover_id")
    args = ap.parse_args(argv)
    if not MOVER_ID.match(args.mover_id):
        ap.error("mover_id must be a mover's id (see: ingest.py movers --list)")
    base, token = connection()
    status, res = call("POST", f"{base}/api/ingest/movers/{args.mover_id}/archive", token, {})
    print(json.dumps(res, indent=1) if status == 200 else f"could not archive: HTTP {status} {res}")
    sys.exit(0 if status == 200 else 1)


def cmd_notes(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="ingest.py notes",
                                 description="Update only a mover's take or score (e.g. after office hours), from a JSON file.")
    ap.add_argument("mover_id")
    ap.add_argument("file", type=Path, help='{"score": {...}} and/or take / learn / localize')
    args = ap.parse_args(argv)
    if not MOVER_ID.match(args.mover_id):
        ap.error("mover_id must be a mover's id (see: ingest.py movers --list)")
    notes = json.loads(args.file.read_text())
    if problems := notes_problems(notes) or ([] if notes else ["the file is empty"]):
        sys.exit("notes file has problems:\n  " + "\n  ".join(problems))
    base, token = connection()
    status, res = call("POST", f"{base}/api/ingest/movers/{args.mover_id}/notes", token, notes)
    print(json.dumps(res, indent=1) if status == 200 else f"could not update notes: HTTP {status} {res}")
    sys.exit(0 if status == 200 else 1)


def cmd_feedback(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="ingest.py feedback",
                                 description="What the founders thought of each mover: votes with reasons, stage, status, score. "
                                             "Read it before proposing more.")
    ap.parse_args(argv)
    base, token = connection()
    status, res = call("GET", f"{base}/api/ingest/movers/feedback", token)
    if status != 200:
        sys.exit(f"could not read feedback: HTTP {status} {res}")
    print(json.dumps(res, indent=1, ensure_ascii=False))


def cmd_research(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="ingest.py research",
                                 description="Record research runs (verdict, method, findings, sources, scored ideas) on the "
                                             "founders' private Research page. A run sent again replaces the stored one.")
    ap.add_argument("file", nargs="?", type=Path)
    ap.add_argument("--list", action="store_true", help="print the stored runs and exit")
    args = ap.parse_args(argv)
    base, token = connection()
    if args.list:
        status, res = call("GET", f"{base}/api/ingest/research", token)
        if status != 200:
            sys.exit(f"could not list research: HTTP {status} {res}")
        for r in res["runs"]:
            print(f"{r['id']}\t{r['run_date']}\t{r['verdict']}\t{r['ideas']} ideas\tbest {r['best']}")
        print(f"{len(res['runs'])} runs")
        return
    if not args.file:
        ap.error("give a research JSON file, or --list")
    runs = load_research(args.file)
    status, res = call("POST", f"{base}/api/ingest/research", token, {"runs": runs})
    if status != 200:
        sys.exit(f"could not record research: HTTP {status} {str(res)[:300]}")
    rejected = [r for r in res["results"] if r["outcome"] == "rejected"]
    unlinked = {r["id"]: r["unlinked"] for r in res["results"] if r.get("unlinked")}

    # verify 1: the database lists every run with the number of ideas sent
    list_status, listing = call("GET", f"{base}/api/ingest/research", token)
    stored = {r["id"]: r for r in listing.get("runs", [])} if list_status == 200 else {}
    wrong = [r["id"] for r in runs if stored.get(r["id"], {}).get("ideas") != len(r.get("ideas") or [])]
    # verify 2: the Research page shows nothing to a browser that is not unlocked
    _, page = call("GET", f"{base}/movers/research?v={int(time.time())}")
    hits = leaked(page, research_texts(runs)) if isinstance(page, str) else []
    report = {
        "sent": len(runs), "inserted": res["inserted"], "updated": res["updated"], "rejected": rejected,
        "unlinked_movers": unlinked, "db_verified": list_status == 200 and not wrong, "missing_or_wrong_in_db": wrong,
        "locked_page_clean": not hits, "locked_page_problems": hits,
    }
    print(json.dumps(report, indent=1, ensure_ascii=False))
    sys.exit(0 if not rejected and list_status == 200 and not wrong and not hits else 2)


SUBCOMMANDS = {"movers": cmd_movers, "link": cmd_link, "archive": cmd_archive, "notes": cmd_notes, "feedback": cmd_feedback,
               "research": cmd_research}


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in SUBCOMMANDS:
        SUBCOMMANDS[sys.argv[1]](sys.argv[2:])
        return
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date")
    ap.add_argument("--curated", type=Path)
    ap.add_argument("--sources", type=Path, help="sources.json written by collect.py")
    ap.add_argument("--notes", default=None)
    ap.add_argument("--check", action="store_true", help="verify token and connectivity, write nothing")
    args = ap.parse_args()

    base, token = connection()

    if args.check:
        status, health = call("GET", f"{base}/api/health")
        print(f"site health: HTTP {status} {health}")
        status, res = call("GET", f"{base}/api/ingest/editions/2026-01-01", token)
        print(f"ingest token: {'ok' if status == 200 else f'REFUSED (HTTP {status}: {res})'}")
        sys.exit(0 if status == 200 else 1)

    if not (args.date and args.curated and args.sources):
        ap.error("--date, --curated and --sources are required")
    items = load_curated(args.curated)
    statuses = json.loads(args.sources.read_text())
    run_dir = args.curated.parent

    status, res = call("POST", f"{base}/api/ingest/runs", token, {"feed_date": args.date, "notes": args.notes})
    if status != 200:
        sys.exit(f"could not start a run: HTTP {status} {res}")
    run_id = res["run_id"]
    (run_dir / "run.json").write_text(json.dumps({"run_id": run_id, "date": args.date}))
    print(f"run {run_id} started for {args.date} as {res['operator']}")

    totals = {"inserted": 0, "updated": 0, "merged": 0, "rejected": 0}
    results: list[dict] = []
    errors: list[str] = []
    for i in range(0, len(items), BATCH):
        chunk = items[i : i + BATCH]
        status, res = call("POST", f"{base}/api/ingest/runs/{run_id}/items", token, {"items": chunk})
        if status != 200:
            errors.append(f"batch {i // BATCH + 1}: HTTP {status} {str(res)[:300]}")
            continue
        for k in totals:
            totals[k] += res[k]
        for r in res["results"]:
            r["index"] += i
            results.append(r)
    for r in results:
        if r["outcome"] == "rejected":
            errors.append(f"rejected item {r['index']} ({items[r['index']]['url']}): {r['reason']}")

    kept_by_source: dict[str, int] = {}
    for it in items:
        kept_by_source[it["source"]] = kept_by_source.get(it["source"], 0) + 1
    src_report = [{"source": s["source"], "name": s.get("name"), "ok": s["ok"], "fetched": s["fetched"], "kept": kept_by_source.get(s["source"], 0),
                   "error": s["error"]} for s in statuses]
    final = run_status(statuses, totals, errors)
    status, fin = call("POST", f"{base}/api/ingest/runs/{run_id}/finish", token,
                       {"status": final, "sources": src_report, "errors": [e[:500] for e in errors][:100], "notes": args.notes})
    if status != 200:
        sys.exit(f"could not finish run {run_id}: HTTP {status} {fin}")

    # ---- verify 1: the database has every story this run put in the edition
    status, ed = call("GET", f"{base}/api/ingest/editions/{args.date}", token)
    in_edition = {r["item_id"] for r in ed.get("items", [])} if status == 200 else set()
    expected = {r["id"] for r in results if r["outcome"] != "rejected" and r.get("edition") == args.date}
    missing_db = sorted(expected - in_edition)

    # ---- verify 2: the public page shows the top stories (cache is cleared on ingest; allow a minute)
    top = [r for r in ed.get("items", [])][:5] if status == 200 else []
    page_ok, missing_page = False, [t["title"] for t in top]
    for _ in range(6):
        st, page = call("GET", f"{base}/{args.date}?v={int(time.time())}")
        if st == 200 and isinstance(page, str):
            text = html.unescape(page)
            missing_page = [t["title"] for t in top if t["title"] not in text]
            if not missing_page:
                page_ok = True
                break
        time.sleep(10)

    report = {
        "run_id": run_id, "date": args.date, "status": fin["status"], "sent": len(items), **totals,
        "edition_items": fin["edition_items"], "already_in_other_editions": [r for r in results if r.get("note")],
        "sources_ok": [s["source"] for s in statuses if s["ok"]],
        "sources_failed": [{"source": s["source"], "error": s["error"]} for s in statuses if not s["ok"] and not s.get("skipped")],
        "sources_skipped": [{"source": s["source"], "error": s["error"]} for s in statuses if s.get("skipped")],
        "errors": errors, "db_verified": not missing_db and status == 200, "missing_in_db": missing_db,
        "page_verified": page_ok, "missing_on_page": missing_page, "page": f"{base}/{args.date}",
    }
    (run_dir / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(json.dumps({k: v for k, v in report.items() if k != "already_in_other_editions"}, indent=1, ensure_ascii=False))
    if report["already_in_other_editions"]:
        print(f"{len(report['already_in_other_editions'])} stories were already featured in an earlier edition and were not repeated.")
    sys.exit(0 if report["db_verified"] and page_ok and final != "failed" else 2)


if __name__ == "__main__":
    main()
