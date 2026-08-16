"""Build the static dashboard: read the store, write site/events.json.

The page itself does all sectioning and filtering in the browser off this one
JSON file, so filters are instant and there is no server anywhere in the design.

Phase 1 note on ranking: the interest score here is a transparent HEURISTIC, not
the LLM family-fit score from Phase 2. It exists so the page has a sane default
order on day one. Every input to it is visible in score_reasons, so when the
ordering looks wrong you can see exactly why.
"""
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (DAY_TRIP_DRIVE_MAX_MIN, HOME_LABEL, HORIZON_DAYS,
                    LOCAL_DRIVE_MAX_MIN, SITE)
import store
from sources.base import is_noise

# Words that suggest something big enough to justify a long drive.
EPIC_RE = re.compile(
    r"\b(festival|fair|parade|expo|carnival|championship|world cup|grand prix|"
    r"renaissance|air show|county fair|marathon|regatta|powwow|rodeo)\b", re.I)

# Words suggesting a good fit for kids roughly 9-14.
KID_GOOD_RE = re.compile(
    r"\b(science|museum|planetarium|dinosaur|reptile|aquarium|maker|robot|lego|"
    r"minecraft|coding|space|astronomy|hike|kayak|climb|skate|arcade|comic|"
    r"anime|magic|circus|zoo|farm|train|aviation|rocket)\b", re.I)

# Words suggesting it is aimed at much younger children, which we down-weight.
TOO_YOUNG_RE = re.compile(
    r"\b(toddler|preschool|babies|baby|storytime|story time|sing-?along|"
    r"ages 2|ages 3|ages 0|infant|mommy|lap-sit)\b", re.I)

# Adults-only signals. These stay on the page, in their own lane.
ADULT_RE = re.compile(r"\b(21\+|18\+|bar crawl|wine tasting|beer fest|brewery|"
                      r"burlesque|nightclub|speakeasy|cocktail)\b", re.I)


def score_event(ev):
    """Heuristic interest score with an explanation. Replaced by the LLM in Phase 2."""
    score, reasons = 50, []
    title = ev["title"] or ""

    drive = ev["drive_minutes"]
    if drive is None:
        score -= 12
        reasons.append("location unknown")
    elif drive <= 20:
        score += 18
        reasons.append("very close")
    elif drive <= 40:
        score += 10
        reasons.append("close")
    elif drive <= LOCAL_DRIVE_MAX_MIN:
        score += 2
    else:
        score -= 8
        reasons.append("long drive")

    if KID_GOOD_RE.search(title):
        score += 16
        reasons.append("kid interests 9-14")
    if TOO_YOUNG_RE.search(title):
        score -= 25
        reasons.append("aimed at little kids")
    if EPIC_RE.search(title):
        score += 12
        reasons.append("festival/large event")
    if ev["is_free"]:
        score += 8
        reasons.append("free")
    if ADULT_RE.search(title):
        reasons.append("adults")
    # Multiple independent sources listing it is weak evidence it matters.
    if len(json.loads(ev["sources"] or "[]")) > 1:
        score += 5
        reasons.append("listed by several sources")
    if not ev["all_day"]:
        score += 3

    return max(0, min(100, score)), reasons


def classify(ev, today):
    """Assign an event to a section of the page."""
    start = datetime.fromisoformat(ev["start_local"]).date()
    end = None
    if ev["end_local"]:
        try:
            end = datetime.fromisoformat(ev["end_local"]).date()
        except ValueError:
            end = None

    drive = ev["drive_minutes"]
    is_day_trip = drive is not None and drive > LOCAL_DRIVE_MAX_MIN
    epic = bool(EPIC_RE.search(ev["title"] or ""))

    # A run of more than three days is an exhibit or season, not an outing.
    if end and (end - start).days >= 3:
        return "ongoing"
    if is_day_trip:
        # Only genuinely big things justify the drive; the rest are simply dropped
        # from the day-trip section rather than padding it out.
        return "daytrip" if epic else "far"
    if start <= today + timedelta(days=6):
        return "week"
    if start <= today + timedelta(days=30):
        return "month"
    # Everything beyond a month still ships, because concerts worth seeing go on
    # sale long before they happen. The page collapses this section by default.
    return "later"


# Type carries the color on the page; attributes carry text badges.
#
# This split is forced by measurement, not taste. Running the palette validator
# over candidate sets, six category colors fail badly (green vs orange comes out
# at deltaE 3.2 under protanopia, effectively identical) and five fail the
# normal-vision floor. Only three hues plus a neutral pass all-pairs checking in
# both light and dark mode.
#
# It is also the better model. "Family-friendly" is not a peer of "music" — it is
# a property a concert can have. So type is a small closed set that gets color,
# and everything crosscutting becomes a badge.
TYPE_MAP = {
    "music": "music",
    "arts": "arts", "film": "arts",
    "sports": "sports",
}

OUTDOOR_RE = re.compile(r"\b(park|beach|garden|trail|hike|outdoor|picnic|"
                        r"pier|plaza|farm|open space|waterfront)\b", re.I)


def event_type(ev):
    """One of music / arts / sports / other. Only these four get a color."""
    return TYPE_MAP.get(ev["category"], "other")


def badges_for(ev):
    """Crosscutting attributes, shown as text so they never rely on color."""
    title = ev["title"] or ""
    tags = [t.lower() for t in json.loads(ev["tags"] or "[]")]
    out = []
    if "funcheap top pick" in tags:
        out.append("top pick")
    if ev["category"] == "family" or (KID_GOOD_RE.search(title)
                                      and not ADULT_RE.search(title)):
        out.append("family")
    if EPIC_RE.search(title):
        out.append("festival")
    if ev["is_free"]:
        out.append("free")
    if ADULT_RE.search(title):
        out.append("adults")
    if OUTDOOR_RE.search(title):
        out.append("outdoor")
    if "sold out" in tags:
        out.append("sold out")
    return out[:4]


# A venue that runs the same thing every single day is describing an attraction,
# not an event. Hiller's "Drone Plex" ran 35 times in one window. We keep the next
# occurrence as a standing attraction and drop the rest.
STANDING_MIN_DAYS = 6


def find_standing(rows):
    """Return {(title, venue): earliest_id} for anything that repeats most days."""
    days = {}
    for r in rows:
        key = ((r["title"] or "").lower(), (r["venue"] or "").lower())
        days.setdefault(key, set()).add(r["start_local"][:10])
    return {k for k, v in days.items() if len(v) >= STANDING_MIN_DAYS}


def main():
    conn = store.connect()
    today = date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    rows = store.upcoming(conn, today.isoformat(), horizon.isoformat())

    standing = find_standing(rows)
    standing_kept = set()

    events = []
    for r in rows:
        ev = dict(r)
        drive = ev["drive_minutes"]
        if drive is not None and drive > DAY_TRIP_DRIVE_MAX_MIN:
            continue  # beyond any reasonable trip

        # Operating-hours pseudo-events, re-checked here so rows already in the
        # database get filtered without needing a refetch.
        if is_noise(ev["title"]):
            continue

        key = ((ev["title"] or "").lower(), (ev["venue"] or "").lower())
        if key in standing:
            if key in standing_kept:
                continue          # already kept the soonest one
            standing_kept.add(key)

        score, reasons = score_event(ev)
        section = "ongoing" if key in standing else classify(ev, today)
        if section == "far":
            continue  # long drive, not big enough to be worth it

        events.append({
            "id": ev["id"],
            "title": ev["title"],
            "start": ev["start_local"],
            "end": ev["end_local"],
            "allDay": bool(ev["all_day"]),
            "venue": ev["venue"],
            "city": ev["city"],
            "region": ev["region"],
            "drive": drive,
            "category": ev["category"],
            "tags": json.loads(ev["tags"] or "[]"),
            "price": ev["price_band"],
            "priceMin": ev["price_min"],
            "free": bool(ev["is_free"]),
            "url": ev["url"],
            "image": ev["image"],
            "adults": bool(ADULT_RE.search(ev["title"] or "")),
            "type": event_type(ev),
            "badges": badges_for(ev),
            "score": score,
            "why": reasons,
            "section": section,
            "sources": json.loads(ev["sources"] or "[]"),
            "firstSeen": ev["first_seen"],
        })

    runs = [dict(r) for r in store.latest_runs(conn)]
    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "home": HOME_LABEL,
        "horizonDays": HORIZON_DAYS,
        "localDriveMax": LOCAL_DRIVE_MAX_MIN,
        "counts": {
            s: sum(1 for e in events if e["section"] == s)
            for s in ("week", "month", "later", "daytrip", "ongoing")
        },
        "sources": [
            {"key": r["source"], "ok": bool(r["ok"]), "count": r["count"], "error": r["error"]}
            for r in runs
        ],
        "events": events,
    }

    SITE.mkdir(parents=True, exist_ok=True)
    out = SITE / "events.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")

    print("Wrote %s" % out)
    print("  %d events: %s" % (len(events), payload["counts"]))
    healthy = sum(1 for r in runs if r["ok"])
    print("  sources healthy: %d/%d" % (healthy, len(runs)))


if __name__ == "__main__":
    main()
