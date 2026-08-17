"""Weekly editorial sweep: find what the feeds structurally cannot.

Every other source in this project is a calendar. Calendars carry ticketed and
listed events. They do not carry the thing a local editor decided was worth
writing about this weekend — the SFGate roundup, the Chronicle's picks, a parent
blog's "10 things to do with kids". That gap is the real difference between this
dashboard and a Google search, and no amount of extra feeds closes it.

So this shells out to Claude Code in headless mode (`claude -p`), which can search
the live web and read those articles. It uses the local Claude subscription — no
API key, no per-call cost.

Like enrich.py, this CANNOT run in GitHub Actions: a cloud runner has no Claude
session. It runs here, weekly, and its findings ride to the cloud in the same
committed cache the scorer uses.

Trust model: the model is asked for a source URL per event and we verify each one
resolves before storing. Anything unverifiable is dropped rather than published,
because a plausible-looking event with a dead link is worse than no event.

    python code/sweep.py                 # search, verify, store
    python code/sweep.py --dry-run       # search and print, store nothing
    python code/sweep.py --days 14
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import HOME_LABEL, ROOT, USER_AGENT
import store
from sources.base import BROWSER_UA, clean_text, make_event
import geo

CLAUDE_TIMEOUT = 900     # web search across many sources is not fast


def find_claude():
    """Locate the Claude Code CLI.

    npm's global bin is often absent from a non-login shell's PATH on Windows,
    so fall back to the known install location rather than failing.
    """
    found = shutil.which("claude")
    if found:
        return found
    candidates = [
        Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd",
        Path(os.environ.get("APPDATA", "")) / "npm" / "claude",
        Path.home() / ".npm-global" / "bin" / "claude",
        Path("/usr/local/bin/claude"),
    ]
    for c in candidates:
        if c and c.exists():
            return str(c)
    raise RuntimeError(
        "Claude Code CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
    )


PROMPT = """\
Search the web for things to do in the Bay Area between {start} and {end}.

I am looking for a family based in {home} with kids aged 9 to 14. Cast a wide \
net: festivals, fairs, museum openings, live music, sports, cultural events, \
food events, outdoor happenings. Adults-only events are fine too — include \
them and say so.

IMPORTANT — what makes this search worth doing. I already pull every major \
ticketing feed and event calendar automatically (Ticketmaster, DoTheBay, \
Funcheap, and museum calendars). Do NOT spend your effort re-listing big \
ticketed concerts and stadium sports; I have those. What I cannot get \
automatically, and what I want from you, is the editorially curated stuff:

- Weekend roundups and "things to do" columns from SFGate, the San Francisco \
Chronicle, TimeOut SF, Berkeleyside, The Mercury News, Palo Alto Online, and \
local parent blogs.
- One-off community events: school and library festivals, county fairs, \
maker fairs, cultural celebrations, farm and harvest events, small-town \
parades and street fairs.
- Anything unusual, seasonal, or once-a-year that a local would know about \
and a tourist would not.

For each event, report:
  title        — the event name
  date         — YYYY-MM-DD, the specific day it happens
  time         — HH:MM 24-hour if stated, otherwise null
  venue        — the venue name if stated, otherwise null
  city         — the Bay Area city or town
  price        — a number in dollars for the cheapest adult entry, 0 if free, \
null if not stated
  url          — a WORKING link to a page about this event. This is required. \
Prefer the event's own page or the venue's page; a news article is acceptable. \
Never invent a URL, and never give me a search-results page or a homepage.
  why          — one sentence on why this family might like it

Rules:
- Only include events you actually found on a page you read. If you are not \
confident an event is real and happening on that date, leave it out.
- Do not include recurring weekly things (regular farmers markets, weekly \
trivia nights, standing museum hours).
- Aim for 15 to 30 genuinely interesting events. Quality over quantity.

A script reads your reply, not a person. The response shape is enforced by a \
schema, so just fill it in: one entry per event, and nothing you cannot \
support with a page you actually read."""


SWEEP_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "date": {"type": "string", "description": "YYYY-MM-DD"},
                    "time": {"type": ["string", "null"], "description": "HH:MM 24h, or null"},
                    "venue": {"type": ["string", "null"]},
                    "city": {"type": ["string", "null"]},
                    "price": {"type": ["number", "null"],
                              "description": "cheapest adult entry in dollars, 0 if free"},
                    "url": {"type": "string", "description": "a working link to this event"},
                    "why": {"type": "string"},
                },
                "required": ["title", "date", "url", "why"],
            },
        }
    },
    "required": ["events"],
}


def run_claude(prompt, cli):
    """Run headless Claude with web access and return the parsed JSON payload."""
    # Two independent limits, and no permission bypass anywhere:
    #   --tools  means this session has ONLY the two web tools in existence —
    #            no shell, no filesystem, no edits, whatever else happens.
    #   .claude/settings.json in this project grants exactly those two, so a
    #            non-interactive run does not stall on a prompt nobody can answer.
    # The permission system stays on; it simply has a standing answer here.
    # Do NOT add --permission-mode: `dontAsk` denies these outright, and
    # `bypassPermissions` would disable checks rather than grant two of them.
    # The prompt goes in on STDIN, not as an argument. claude.cmd is a batch
    # wrapper, so every argument is re-parsed by cmd.exe -- a prompt containing
    # braces, quotes or percent signs gets mangled on the way through, and the
    # symptom is a plain-prose reply with no JSON envelope at all rather than
    # any kind of error. stdin avoids the whole class of problem.
    cmd = [
        cli, "-p",
        "--output-format", "json",
        # Constrain the reply structurally instead of asking for JSON in prose.
        # Two rounds of increasingly emphatic "return only JSON" instructions
        # were ignored -- headless Claude defaults to a readable summary, and a
        # user-turn instruction does not override that. A schema does.
        "--json-schema", json.dumps(SWEEP_SCHEMA),
        "--tools", "WebSearch", "WebFetch",
    ]
    proc = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=CLAUDE_TIMEOUT,
        # cwd matters: Claude reads .claude/settings.json relative to it, and
        # that file is what grants WebSearch/WebFetch.
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise RuntimeError("claude exited %d: %s" % (proc.returncode, (proc.stderr or "")[:400]))

    # The envelope carries BOTH a human-readable `result` and, when --json-schema
    # is set, an already-parsed `structured_output`. Read the structured field
    # first: `result` can hold a chatty summary even when the schema was honoured,
    # which is what made this look broken for three rounds of prompt-tweaking.
    try:
        env = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return _extract_json(proc.stdout)

    structured = env.get("structured_output")
    if isinstance(structured, dict) and "events" in structured:
        return structured

    return _extract_json(env.get("result") or proc.stdout)


def _extract_json(text):
    """Pull the payload out, tolerating the ways a model wraps it.

    Order matters: a fenced block is the most reliable signal, so try that
    before falling back to brace matching, which can otherwise grab a stray
    object out of prose.
    """
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        return json.loads(fenced.group(1))

    # Widest balanced-looking span containing an "events" key.
    m = re.search(r'\{[^{]*"events".*\}', text, re.S)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass

    raise ValueError(
        "Claude replied in prose rather than JSON. First 300 chars:\n%s"
        % text[:300])


def url_resolves(url):
    """Verify a link actually loads. The whole point is not publishing fiction."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    for ua in (BROWSER_UA, USER_AGENT):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua}, method="HEAD")
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status < 400:
                    return True
        except urllib.error.HTTPError as e:
            # Some sites reject HEAD but serve GET fine.
            if e.code in (403, 405):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": ua})
                    with urllib.request.urlopen(req, timeout=20) as r:
                        if r.status < 400:
                            return True
                except Exception:
                    continue
        except Exception:
            continue
    return False


def to_event(raw):
    title = clean_text(raw.get("title"), 200)
    day = str(raw.get("date") or "")[:10]
    if not title or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return None

    tod = raw.get("time")
    if isinstance(tod, str) and re.fullmatch(r"\d{1,2}:\d{2}", tod.strip()):
        start_local = "%sT%02d:%s:00" % (day, int(tod.split(":")[0]), tod.split(":")[1])
        all_day = False
    else:
        start_local, all_day = day + "T00:00:00", True

    price = raw.get("price")
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None

    city = clean_text(raw.get("city"), 60)
    return make_event(
        source="sweep",
        title=title,
        start_local=start_local,
        all_day=all_day,
        url=raw.get("url"),
        venue=clean_text(raw.get("venue"), 120),
        city=city if city and geo.coords_for_city(city) else city,
        category="community",
        tags=["editorial pick"],
        price_min=price,
        is_free=(price == 0),
        description=clean_text(raw.get("why"), 300),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cli = find_claude()
    start = date.today()
    end = start + timedelta(days=args.days)
    prompt = PROMPT.format(start=start, end=end, home=HOME_LABEL)

    print("Sweeping %s to %s via %s" % (start, end, cli))
    payload = run_claude(prompt, cli)
    found = payload.get("events") or []
    print("Claude returned %d events; verifying links...\n" % len(found))

    kept, dropped = [], []
    for raw in found:
        ev = to_event(raw)
        if not ev:
            dropped.append((raw.get("title", "?"), "unparseable date or title"))
            continue
        if not url_resolves(ev["url"]):
            dropped.append((ev["title"], "link did not resolve"))
            continue
        kept.append(ev)
        print("  ok   %-52s %s  %s" % (ev["title"][:52], ev["start_local"][:10],
                                       ev["city"] or "?"))

    for title, why in dropped:
        print("  DROP %-52s %s" % (str(title)[:52], why))

    print("\n%d verified, %d dropped" % (len(kept), len(dropped)))
    if args.dry_run:
        print("(dry run: nothing stored)")
        return
    if kept:
        conn = store.connect()
        new, updated = store.upsert_events(conn, kept)
        store.record_run(conn, "sweep", True, len(kept))
        print("Stored: %d new, %d updated" % (new, updated))
        print("Run `python code/enrich.py` to score them, then `python code/build.py`.")


if __name__ == "__main__":
    main()
