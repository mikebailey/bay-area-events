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

from config import (CACHE_PATH, FEEDBACK_PATH, DAY_TRIP_DRIVE_MAX_MIN, HOME_LABEL, HORIZON_DAYS,
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


def classify(ev, today, llm=None):
    """Assign an event to a section of the page.

    The day-trip gate asks the model whether an event is genuinely worth a long
    drive, since that is a judgment call the EPIC_RE keyword list makes badly.
    Unscored events fall back to the keyword test.
    """
    start = datetime.fromisoformat(ev["start_local"]).date()
    end = None
    if ev["end_local"]:
        try:
            end = datetime.fromisoformat(ev["end_local"]).date()
        except ValueError:
            end = None

    drive = ev["drive_minutes"]
    is_day_trip = drive is not None and drive > LOCAL_DRIVE_MAX_MIN
    epic = bool(llm["epic"]) if llm else bool(EPIC_RE.search(ev["title"] or ""))

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


def badges_for(ev, llm=None):
    """Crosscutting attributes, shown as text so they never rely on color."""
    title = ev["title"] or ""
    tags = [t.lower() for t in json.loads(ev["tags"] or "[]")]
    out = []
    if "funcheap top pick" in tags:
        out.append("top pick")
    if llm:
        # Trust the model's read of who the event is for.
        if llm["family_fit"] >= 65 and not llm["adults_only"]:
            out.append("family")
        if llm["epic"]:
            out.append("epic")
    else:
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


# "For the adults" is a view, not a category: an event stays in its normal day
# section AND surfaces here if it qualifies. The rule is deliberately narrow —
# strong adult appeal, plus a reason it is not a family outing (age-restricted,
# poor family fit, or simply an evening thing).
DATE_NIGHT_MIN_ADULT = 75
DATE_NIGHT_HOUR = 19


def is_date_night(ev, llm):
    if not llm or llm.get("adult_interest") is None:
        return False
    if llm["adult_interest"] < DATE_NIGHT_MIN_ADULT:
        return False
    if llm["adults_only"] or llm["family_fit"] < 55:
        return True
    try:
        return int(ev["start_local"][11:13]) >= DATE_NIGHT_HOUR
    except (ValueError, IndexError):
        return False


# Volunteering with the kids is its own category: not entertainment, but a good
# way to spend a Saturday morning and something worth surfacing deliberately.
VOLUNTEER_RE = re.compile(
    r"\b(clean[\s-]?up|cleanup|volunteer|restoration|habitat|"
    r"tree planting|beach clean|creek clean|park steward|stewardship|"
    r"trail work|food bank|donation drive|service day|litter|weed pull|"
    r"native plant|beautification|adopt-a-|community service)\b", re.I)


def series_stem(ev):
    """A key shared by every occurrence of a recurring event.

    Weekly and franchise events ("Pokemon GO ... Community Day", a roller disco
    every Sunday, a venue's standing series) should not crowd out one-off
    occasions in Highlights. Grouping them lets us both damp their ranking and
    smooth their scores.
    """
    t = re.sub(r"[^a-z0-9 ]", " ", (ev["title"] or "").lower())
    words = [w for w in t.split() if len(w) > 2 and not w.isdigit()]
    return " ".join(words[:4])


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


def load_overrides():
    """Hand-set scores that beat the model outright, by title substring."""
    if not FEEDBACK_PATH.exists():
        return []
    try:
        data = json.loads(FEEDBACK_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("WARNING: feedback.json is malformed; ignoring overrides")
        return []
    out = []
    for row in data.get("events") or []:
        m = (row.get("match") or "").strip().lower()
        if m and (row.get("family_fit") is not None
                  or row.get("adult_interest") is not None):
            out.append((m, row))
    return out


def load_scores():
    """LLM scores from the local scorer, if any exist yet.

    Deliberately optional. The daily cloud build has no GPU and no model, so it
    reads whatever this file happens to contain and falls back to the heuristic
    for anything unscored. A missing file is a normal state, not an error.
    """
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("WARNING: %s is malformed; ignoring it" % CACHE_PATH)
        return {}


def main():
    conn = store.connect()
    today = date.today()
    horizon = today + timedelta(days=HORIZON_DAYS)
    rows = store.upcoming(conn, today.isoformat(), horizon.isoformat())
    scores = load_scores()

    overrides = load_overrides()
    override_hits = 0

    standing = find_standing(rows)
    standing_kept = set()

    # Series stats, computed once over the whole window.
    #
    # The scorer is noticeably noisy on repeats: the same "Manny's Neighborhood
    # Trash Cleanup" came back 55, 85, 40, 85, 40, 40 on different dates, and an
    # identical weekly roller disco ranged 30 to 85. Identical input, different
    # answer. Taking the median across a series turns that noise into one stable
    # number, so a good recurring event is not randomly buried in the week it
    # happened to score badly.
    series = {}
    for r in rows:
        series.setdefault(series_stem(r), []).append(r["id"])
    score_by_id = {}

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
        llm = scores.get(ev["id"])
        if llm:
            # The model's judgment replaces the keyword heuristic outright.
            score = llm["family_fit"]
            reasons = ["scored locally"]

        title_l = (ev["title"] or "").lower()
        ov = next((row for m, row in overrides if m in title_l), None)
        if ov and ov.get("family_fit") is not None:
            score = ov["family_fit"]
            reasons = ["your rating"]
            override_hits += 1

        stem = series_stem(ev)
        members = series.get(stem, [])
        if len(members) >= 3:
            sibling_scores = sorted(
                scores[i]["family_fit"] for i in members if i in scores)
            if sibling_scores and not ov:
                median = sibling_scores[len(sibling_scores) // 2]
                if llm and median != score:
                    reasons.append("smoothed across %d in series" % len(sibling_scores))
                score = median

        section = "ongoing" if key in standing else classify(ev, today, llm)
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
            "adults": bool(llm["adults_only"]) if llm else bool(ADULT_RE.search(ev["title"] or "")),
            "type": event_type(ev),
            "badges": badges_for(ev, llm),
            "blurb": llm["blurb"] if llm else None,
            "scored": bool(llm),
            "adultScore": (ov.get("adult_interest") if ov and ov.get("adult_interest") is not None
                           else (llm or {}).get("adult_interest")),
            "dateNight": is_date_night(ev, llm),
            "seriesSize": len(series.get(series_stem(ev), [])),
            "volunteer": bool(VOLUNTEER_RE.search(ev["title"] or "")),
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
    if overrides:
        print("  your ratings applied: %d event(s) from feedback.json" % override_hits)
    n_vol = sum(1 for e in events if e["volunteer"])
    n_series = sum(1 for e in events if e["seriesSize"] >= 4)
    print("  volunteer events: %d | in a recurring series (4+): %d" % (n_vol, n_series))
    n_date = sum(1 for e in events if e["dateNight"])
    print("  date-night events: %d" % n_date)
    n_scored = sum(1 for e in events if e["scored"])
    print("  scored by local model: %d/%d (%.0f%%)"
          % (n_scored, len(events), 100.0 * n_scored / max(1, len(events))))


if __name__ == "__main__":
    main()
