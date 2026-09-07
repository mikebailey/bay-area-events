"""Funcheap day archives: the wide net for small, local, often free one-offs.

This is the source most likely to surface something like a county reptile expo,
which no ticketing API will ever carry.

We scrape the day archive pages (sf.funcheap.com/YYYY/MM/DD/). Established while
building, on 2026-08-15:

  * The RSS feed is hard-capped at 10 items and aggressively cached. Every
    pagination form WordPress normally honors (?paged=N, /page/N/feed/,
    ?posts_per_rss=N) returns byte-identical content, so the feed can never yield
    more than 10 events no matter how many requests you make.
  * The /city/<region>/feed/ URLs are not region-filtered; same firehose.
  * The day archives ARE keyed to the event date, carry 20-40 events per day, and
    each listing is a <div id="post-NNN"> block with structured attributes:
        data-event-date / data-event-date-end   exact local start and end
        class="... category-foo category-bar"   Funcheap's own taxonomy
        <span class="cost">                     price as printed
    So we parse those blocks rather than the visible text.

Parsing the post blocks (rather than every event-looking link on the page) also
fixes a real bug: the sidebar carries "most popular" widgets full of links to
events on other dates, and scraping those stamped unrelated events with the
archive day's date, producing the same festival four times running.
"""
import re
import time
from datetime import datetime, timedelta

from sources.base import clean_text, http_get, make_event
import geo

BASE = "https://sf.funcheap.com/%Y/%m/%d/"

# Each listing on a day archive page.
_POST_RE = re.compile(r'<div id="post-\d+"(.*?)(?=<div id="post-\d+"|<div id="sidebar")', re.S)
_TITLE_RE = re.compile(r'<span class="title entry-title">\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DATE_RE = re.compile(r'data-event-date="([^"]+)"')
_DATE_END_RE = re.compile(r'data-event-date-end="([^"]+)"')
_COST_RE = re.compile(r'<span class="cost">(.*?)</span>', re.S)
_CATEGORY_RE = re.compile(r"category-([a-z0-9-]+)")
_PRICE_NUM_RE = re.compile(r"\$\s?(\d+(?:\.\d{2})?)")
_PAREN_RE = re.compile(r"\(([^)]{3,40})\)\s*$")
# The meta line runs "<span class="cost">Cost: FREE</span> | <span>Venue</span>".
# The venue is the last bare span on that line, and it is the only location a
# lot of Funcheap listings carry, since their titles often name no town at all.
_VENUE_RE = re.compile(r'<span class="cost">.*?</span>\s*\|\s*<span>(.*?)</span>', re.S)

# Funcheap tags every post with the area it is in. This is by far the most
# reliable location signal on the page -- more reliable than the title, which is
# where the city was being read from -- and it was previously being parsed and
# then thrown away by the six-tag cap below.
REGION_CATEGORIES = {
    "peninsula": "Peninsula",
    "south-bay": "South Bay",
    "east-bay": "East Bay",
    "north-bay": "North Bay",
    "sf": "San Francisco",
    "san-francisco": "San Francisco",
}

# Multi-day events are posted once, on their first day, and Funcheap frequently
# sets data-event-date-end to that same first day. The real span survives only
# in the title: "... (Sept. 5-7)". Without this a two-day festival vanishes from
# the site halfway through, which is how the Scottish Highland Games disappeared
# on the Sunday they were running.
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_TITLE_RANGE_RE = re.compile(
    r"\(?\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
    r"(\d{1,2})\s*(?:-|–|—|to|&|and)\s*"
    r"(?:(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+)?(\d{1,2})\b", re.I)

# Funcheap's taxonomy mapped to ours. Anything unmapped is kept as a tag.
CATEGORY_MAP = {
    "kids-families": "family", "museums": "arts", "art-museums": "arts",
    "theater-performance": "arts", "music": "music", "concerts": "music",
    "comedy": "arts", "film": "film", "sports-outdoors": "sports",
    "fairs-festivals": "community", "food": "community",
}
# Structural or commercial tags that say nothing about the event itself.
SKIP_TAGS = {"select-one-location", "in-person", "sponsored", "top-pick", "online",
             "discount-tix-promo-codes", "uncategorized"}


def _parse_dt(s):
    """'2026-08-16 10:00' -> ('2026-08-16T10:00:00', had_time)."""
    s = (s or "").strip()
    if not s:
        return None, False
    for fmt, had_time in (("%Y-%m-%d %H:%M", True), ("%Y-%m-%d", False)):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:00"), had_time
        except ValueError:
            continue
    return None, False


def _parse_cost(raw):
    """Funcheap prints cost as 'FREE', '$25', '$10-$20', or leaves it blank."""
    s = clean_text(raw, 60) or ""
    if not s:
        return None, False
    if re.search(r"\bfree\b", s, re.I):
        return 0.0, True
    nums = [float(n) for n in _PRICE_NUM_RE.findall(s.replace(",", ""))]
    if nums:
        return min(nums), False
    return None, False


# SF is written as "SF" far more often than "San Francisco", and many listings
# name only a landmark. Without these, roughly half of Funcheap's San Francisco
# events end up with no location and therefore no drive time.
SF_HINTS = re.compile(
    r"\bSF\b|\bS\.F\.|san francisco|golden gate park|yerba buena|presidio|"
    r"embarcadero|fisherman'?s wharf|chase center|oracle park|civic center|"
    r"mission district|dolores park|the castro|haight|north beach|ocean beach|"
    r"great highway|union square|z space|fort mason|ferry building", re.I)


def _title_end_date(title, start_local):
    """Read '(Sept. 5-7)' off the end of a title and return the last day.

    Only trusted when it agrees with the start date we already have: the day
    must match, and the whole range must sit within about three weeks of it.
    Anything else is a coincidence in the text, not a date range.
    """
    m = _TITLE_RANGE_RE.search(title or "")
    if not m or not start_local:
        return None
    try:
        start = datetime.strptime(start_local[:10], "%Y-%m-%d").date()
    except ValueError:
        return None

    first_month = _MONTHS[m.group(1)[:3].lower()]
    first_day = int(m.group(2))
    last_month = _MONTHS[m.group(3)[:3].lower()] if m.group(3) else first_month
    last_day = int(m.group(4))

    if (first_month, first_day) != (start.month, start.day):
        return None
    year = start.year + (1 if last_month < first_month else 0)
    try:
        end = datetime(year, last_month, last_day).date()
    except ValueError:
        return None
    if not (start < end <= start + timedelta(days=21)):
        return None
    return end.strftime("%Y-%m-%dT23:59:00")


def _detect_city(title, venue=None):
    """Funcheap titles usually end with a location, e.g. '... (Palo Alto)'.

    The venue is checked too, now that we parse it: a listing headed "Kings
    Mountain Fire Station" names no city anywhere in its title.
    """
    m = _PAREN_RE.search(title or "")
    if m and geo.coords_for_city(m.group(1)):
        return m.group(1).strip()
    if venue:
        known = geo.city_for_venue(venue)
        if known:
            return known.title()
    if SF_HINTS.search(title or ""):
        return "San Francisco"
    low = (title or "").lower()
    best = None
    for city in geo.CITY_COORDS:
        if len(city) < 5:
            continue
        if re.search(r"\b%s\b" % re.escape(city), low):
            if best is None or len(city) > len(best):
                best = city
    return best.title() if best else None


# Funcheap monetizes with sponsored deal posts that sit in the listing exactly like
# events ("$35 for Locally-Run Independent Internet Service", "50% Off Resale
# Finds"). They are advertisements, not things to go do, and they are the single
# biggest source of junk on that site. Drop them.
_DEAL_TITLE = re.compile(
    r"^\s*\$\d+\s+for\b|"           # "$35 for ..."
    r"^\s*\d+%\s*off\b|"            # "50% Off ..."
    r"\bpromo code\b|\bcoupon\b|"
    r"\b(save|deal[s]?)\s+(up\s+to\s+)?\d+%|"
    r"^\s*free\s+(shipping|trial)\b", re.I)
_DEAL_CATEGORIES = {"sponsored", "discount-tix-promo-codes"}


def _parse_post(block, day):
    tm = _TITLE_RE.search(block)
    if not tm:
        return None
    url = tm.group(1)
    title = clean_text(tm.group(2), 200)
    if not title or not url:
        return None

    cats_raw = set(_CATEGORY_RE.findall(block))
    # A sponsored post with no real event category is an ad. Note that some
    # genuine events are cross-listed under promo codes, so "sponsored" alone
    # is the disqualifier, not the discount tag by itself.
    if "sponsored" in cats_raw or _DEAL_TITLE.search(title):
        return None

    start_raw = _DATE_RE.search(block)
    start_local, had_time = _parse_dt(start_raw.group(1) if start_raw else None)
    if not start_local:
        # No structured date on the block; fall back to the archive day itself.
        start_local, had_time = day.isoformat() + "T00:00:00", False

    end_raw = _DATE_END_RE.search(block)
    end_local, _ = _parse_dt(end_raw.group(1) if end_raw else None)
    # data-event-date-end is usually just the closing time on the opening day,
    # so a run spanning several days has to come out of the title.
    spanned = _title_end_date(title, start_local)
    if spanned and (not end_local or spanned > end_local):
        end_local = spanned

    cost_m = _COST_RE.search(block)
    price_min, is_free = _parse_cost(cost_m.group(1) if cost_m else None)

    cats = set(_CATEGORY_RE.findall(block))
    category = "community"
    for c in cats:
        if c in CATEGORY_MAP:
            category = CATEGORY_MAP[c]
            break

    # Pull the region out before the tag list is capped. It used to survive only
    # as a tag, sorted alphabetically and then truncated to six, so "peninsula"
    # was silently dropped from exactly the listings that needed it most.
    region = next((REGION_CATEGORIES[c] for c in sorted(cats) if c in REGION_CATEGORIES), None)

    tags = sorted(c.replace("-", " ") for c in cats
                  if c not in SKIP_TAGS and c not in REGION_CATEGORIES)[:6]
    if "top-pick" in cats:
        tags.insert(0, "funcheap top pick")

    venue_m = _VENUE_RE.search(block)
    venue = clean_text(venue_m.group(1), 120) if venue_m else None

    return make_event(
        source="funcheap",
        title=title,
        start_local=start_local,
        end_local=end_local,
        all_day=not had_time,
        url=url,
        venue=venue,
        city=_detect_city(title, venue),
        region=region,
        category=category,
        tags=tags,
        price_min=price_min,
        is_free=is_free,
    )


def _parse_day(body, day):
    out = []
    for block in _POST_RE.findall(body):
        ev = _parse_post(block, day)
        if ev:
            out.append(ev)
    return out


def fetch(window_start, window_end, max_days=60):
    """One request per day. Capped since the APIs already cover the long tail."""
    out = []
    day = window_start
    last = min(window_end, window_start + timedelta(days=max_days))
    failures = 0
    while day <= last:
        try:
            body = http_get(day.strftime(BASE), retries=2)
            out.extend(_parse_day(body, day))
        except Exception:
            # One missing day is normal; a run of them means the site changed.
            failures += 1
            if failures > 8:
                raise
        day += timedelta(days=1)
        time.sleep(0.2)
    return out
