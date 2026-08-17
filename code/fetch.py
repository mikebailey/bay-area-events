"""Daily fetch orchestrator.

Runs every source in isolation, merges duplicates, and writes to the store.

The isolation matters more than it looks. These are a dozen third-party websites
that owe us nothing and change without warning, so the design assumption is that
one of them is always broken. A failure is caught, recorded in source_runs, and
surfaced on the page, rather than aborting the run.

    python code/fetch.py              # normal daily run
    python code/fetch.py --only ticketmaster
    python code/fetch.py --dry-run    # fetch and report, write nothing
"""
import argparse
import os
import re
import sys
import time
import traceback
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import FUNCHEAP_MAX_DAYS, HORIZON_DAYS, ROOT, TRIBE_VENUES
import store
from sources import dothebay, funcheap, ticketmaster, tribe


def load_env(path=None):
    """Minimal .env loader.

    Deliberately dependency-free so the daily job needs no pip install, and
    deliberately NON-overriding for values already in the environment, which is
    what lets GitHub Actions secrets win in CI where no .env file exists.
    """
    path = Path(path or ROOT / ".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


def build_registry():
    """Every source as (key, label, callable). Adding a source happens here."""
    reg = [("ticketmaster", "Ticketmaster", ticketmaster.fetch)]
    for v in TRIBE_VENUES:
        reg.append((v["key"], v["name"],
                    lambda ws, we, v=v: tribe.fetch_venue(v, ws, we)))
    reg.append(("dothebay", "DoTheBay", dothebay.fetch))
    reg.append(("funcheap", "Funcheap (day archives)",
                lambda ws, we: funcheap.fetch(ws, we, FUNCHEAP_MAX_DAYS)))
    return reg


# Sources tack a location or edition onto the same event's title:
#   "Brick Blast: LEGO Fan Convention"
#   "Brick Blast: LEGO Fan Convention | Santa Clara"
# Both are real listings of one event. Strip the trailing qualifier before
# comparing, or they occupy two slots in a 20-card Highlights band.
_TITLE_SUFFIX = re.compile(
    r"\s*[|–—-]\s*[^|–—-]{2,28}$"   # "... | Santa Clara", "... - Oakland"
    r"|\s*\([^)]{2,28}\)\s*$"                          # "... (San Mateo)"
    r"|\s*\b(day\s*\d|weekend|session\s*\d)\b\s*$",     # "... Day 2"
    re.I)


def _norm_title(t):
    t = (t or "").strip()
    prev = None
    while prev != t:            # a title can carry two qualifiers
        prev = t
        t = _TITLE_SUFFIX.sub("", t).strip()
    return re.sub(r"[^a-z0-9]+", "", t.lower())[:40]


def _word_set(t):
    """Significant words of a title, for order-insensitive comparison."""
    words = re.findall(r"[a-z0-9]+", (t or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


_STOPWORDS = {"the", "and", "for", "with", "featuring", "presents", "annual"}


def _same_event(a, b, title_a=None, title_b=None):
    """True when two normalized titles describe one event.

    Three tests, cheapest first:
      1. Equality.
      2. Long shared prefix, for "X" vs "X plus extra words".
      3. Identical significant-word sets, for titles that REORDER rather than
         extend -- "2026 Oakland Pride Parade & Festival" against "Oakland
         Pride Parade & Festival 2026". A prefix test cannot see those as one
         event, and they were taking two slots and carrying different scores.
    """
    if a == b:
        return True
    lo, hi = (a, b) if len(a) <= len(b) else (b, a)
    if len(lo) >= 18 and hi.startswith(lo):
        return True
    if title_a and title_b:
        wa, wb = _word_set(title_a), _word_set(title_b)
        if len(wa) >= 3 and wa == wb:
            return True
    return False


def _completeness(ev):
    """How much a record actually tells us. Used to pick a winner when merging."""
    score = 0
    for field in ("lat", "price_min", "image", "description", "end_local", "venue", "city"):
        if ev.get(field) is not None:
            score += 1
    if not ev.get("all_day"):
        score += 1          # a real start time beats a date-only guess
    return score


def dedupe(events):
    """Collapse the same event reported by several sources.

    Matches on date plus normalized title, ignoring venue, because feeds disagree
    about venue naming ("Shoreline" vs "Shoreline Amphitheatre"). The richest
    record wins and absorbs the others' source labels.

    Kept deliberately conservative: no fuzzy string distance, so two genuinely
    different shows at the same venue on the same night stay separate.
    """
    # Bucket by date first, then merge within a date using prefix matching, so
    # "X" and "X | Santa Clara" land together instead of in separate buckets.
    by_date = {}
    for ev in events:
        by_date.setdefault(ev["start_local"][:10], []).append(ev)

    groups = {}
    for day, todays in by_date.items():
        canon = []          # (normalized_title, group_key)
        for ev in todays:
            norm = _norm_title(ev["title"])
            match = next((k for n, k, raw in canon
                          if _same_event(n, norm, raw, ev["title"])), None)
            if match is None:
                match = (day, norm)
                canon.append((norm, match, ev["title"]))
            groups.setdefault(match, []).append(ev)

    merged, collapsed = [], 0
    for key, group in groups.items():
        if len(group) == 1:
            merged.append(group[0])
            continue
        group.sort(key=_completeness, reverse=True)
        winner = dict(group[0])
        for other in group[1:]:
            winner["sources"] = list(winner["sources"]) + list(other["sources"])
            # Backfill anything the winner happens to be missing.
            for field in ("lat", "lon", "price_min", "price_max", "image",
                          "description", "end_local", "venue", "city", "url"):
                if winner.get(field) is None and other.get(field) is not None:
                    winner[field] = other[field]
        winner["sources"] = sorted(set(winner["sources"]))
        collapsed += len(group) - 1
        merged.append(winner)
    return merged, collapsed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run a single source by key")
    ap.add_argument("--dry-run", action="store_true", help="fetch but do not write")
    ap.add_argument("--days", type=int, default=HORIZON_DAYS)
    args = ap.parse_args()

    load_env()
    window_start = date.today()
    window_end = window_start + timedelta(days=args.days)
    print("Window: %s to %s (%d days)\n" % (window_start, window_end, args.days))

    conn = None if args.dry_run else store.connect()
    registry = build_registry()
    if args.only:
        registry = [r for r in registry if r[0] == args.only]
        if not registry:
            sys.exit("No source with key %r" % args.only)

    all_events, failures = [], 0
    for key, label, fn in registry:
        t0 = time.time()
        try:
            events = fn(window_start, window_end)
            elapsed = time.time() - t0
            all_events.extend(events)
            print("  ok    %-22s %5d events  %5.1fs" % (key, len(events), elapsed))
            if conn:
                store.record_run(conn, key, True, len(events), elapsed)
        except Exception as e:
            elapsed = time.time() - t0
            failures += 1
            msg = "%s: %s" % (type(e).__name__, e)
            print("  FAIL  %-22s %s" % (key, msg[:80]))
            if os.environ.get("BAE_DEBUG"):
                traceback.print_exc()
            if conn:
                store.record_run(conn, key, False, 0, elapsed, msg[:500])

    merged, collapsed = dedupe(all_events)
    print("\nFetched %d raw, merged %d duplicates, %d unique events, %d source failures"
          % (len(all_events), collapsed, len(merged), failures))

    if args.dry_run:
        print("(dry run: nothing written)")
        return

    new, updated = store.upsert_events(conn, merged)
    print("Stored: %d new, %d updated" % (new, updated))


if __name__ == "__main__":
    main()
