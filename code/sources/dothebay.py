"""DoTheBay: a curated Bay Area events aggregator with a clean JSON API.

Worth having for three reasons beyond raw coverage:

  1. It carries its own `popularity` score, which is real human signal about what
     people actually care about, unlike our keyword heuristics.
  2. Many of its listings originate on Eventbrite (`is_eventbrite`,
     `eventbrite_id`). Eventbrite killed public event search in its own API and
     blocks scraping, so this is a legitimate route to some of that inventory.
  3. It flags `is_ongoing`, which feeds the exhibits-and-runs section directly.

The endpoint is the page's own JSON backing (/events.json?page=N). It requires a
browser User-Agent; a bot UA gets a 403, which is what made this look unavailable
on first inspection.
"""
import re
import time

from sources.base import BROWSER_UA, clean_text, http_get_json, is_noise, make_event

BASE = "https://dothebay.com/events.json"
MAX_PAGES = 24        # 25 per page; generous ceiling, loop exits when pages run out

# DoTheBay's own categories mapped to ours.
CATEGORY_MAP = {
    "music": "music", "concert": "music", "concerts": "music",
    "arts": "arts", "art": "arts", "theater": "arts", "theatre": "arts",
    "comedy": "arts", "film": "film", "museums": "arts",
    "sports": "sports", "outdoors": "sports", "fitness": "sports",
    "family": "family", "kids": "family",
    "food": "community", "festivals": "community", "community": "community",
}


def _when(event, prefix):
    """Resolve a start or end timestamp.

    Two traps here. First, `begin_time` is not a clock string as the name
    suggests, it is a full ISO datetime, so naive parsing silently yields
    midnight for everything. Second, those timestamps carry a -05:00 offset,
    which is not Pacific; the `tz_adjusted_*` fields carry the correct -07:00.
    So prefer tz_adjusted, and fall back to the plain date.

    Returns (iso_local_naive, had_real_time).
    """
    tz = event.get("tz_adjusted_%s_date" % prefix)
    raw = event.get("%s_time" % prefix) or tz
    if tz:
        raw = tz
    if raw and "T" in str(raw):
        s = str(raw)[:19]
        # Midnight is what DoTheBay uses for "no time given", not a real 12am start.
        return s, not s.endswith("T00:00:00")
    day = event.get("%s_date" % prefix)
    if not day:
        return None, False
    return str(day)[:10] + "T00:00:00", False


def _price_from_ticket_info(info, is_free):
    """`ticket_info` reads like '$40-$85, 21+' or 'Free, All Ages' or ''."""
    s = (info or "").strip()
    if is_free or re.search(r"\bfree\b", s, re.I):
        return 0.0, True
    nums = [float(n) for n in re.findall(r"\$\s?(\d+(?:\.\d{2})?)", s.replace(",", ""))]
    if nums:
        return min(nums), False
    return None, False


def _parse_one(e):
    title = clean_text(e.get("title"), 200)
    if not title or is_noise(title):
        return None
    start_local, had_time = _when(e, "begin")
    if not start_local:
        return None
    end_local, _ = _when(e, "end")

    venue = e.get("venue") or {}
    lat, lon = venue.get("latitude"), venue.get("longitude")
    try:
        lat = float(lat) if lat else None
        lon = float(lon) if lon else None
    except (TypeError, ValueError):
        lat = lon = None

    raw_cat = (e.get("category") or e.get("category_param") or "").lower()
    category = "other"
    for token, mapped in CATEGORY_MAP.items():
        if token in raw_cat:
            category = mapped
            break

    tags = []
    if e.get("is_ongoing"):
        tags.append("ongoing")
    if e.get("sold_out"):
        tags.append("sold out")
    for a in (e.get("artists") or [])[:2]:
        name = clean_text(a.get("name") if isinstance(a, dict) else a, 40)
        if name:
            tags.append(name.lower())

    url = e.get("permalink") or e.get("buy_url")
    if url and url.startswith("/"):
        url = "https://dothebay.com" + url

    price_min, is_free = _price_from_ticket_info(e.get("ticket_info"), e.get("is_free"))

    imagery = e.get("imagery") or {}
    image = None
    if isinstance(imagery, dict):
        image = imagery.get("thumb_url") or imagery.get("url")

    return make_event(
        source="dothebay",
        title=title,
        start_local=start_local,
        end_local=end_local,
        all_day=not had_time,
        url=url,
        venue=clean_text(venue.get("title") or venue.get("name"), 120),
        city=clean_text(venue.get("city"), 60),
        lat=lat, lon=lon,
        category=category,
        tags=tags[:5],
        is_free=is_free,
        price_min=price_min,
        image=image,
        description=e.get("excerpt"),
    )


def fetch(window_start, window_end):
    out = []
    for page in range(1, MAX_PAGES + 1):
        data = http_get_json("%s?page=%d" % (BASE, page), ua=BROWSER_UA)
        events = data.get("events") or []
        for e in events:
            ev = _parse_one(e)
            if not ev:
                continue
            day = ev["start_local"][:10]
            if str(window_start) <= day <= str(window_end):
                out.append(ev)

        paging = data.get("paging") or {}
        if page >= (paging.get("total_pages") or 1):
            break
        time.sleep(0.3)
    return out
