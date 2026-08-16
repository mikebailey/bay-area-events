"""The Events Calendar (WordPress plugin) REST API.

One parser, many venues. Any Bay Area venue running this plugin exposes
/wp-json/tribe/events/v1/events with clean JSON: real start and end times, venue
addresses, cost strings, categories. Adding a venue is one line in config.py.

Verified live 2026-08-15: CuriOdyssey, Filoli, Hiller Aviation, Chabot Space,
Oakland Museum of California.
"""
import re
import time
import urllib.parse

from sources.base import clean_text, http_get_json, is_noise, make_event

PER_PAGE = 50
MAX_PAGES = 12


def _parse_cost(cost_str, event):
    """Turn the plugin's free-text cost field into numbers.

    Values look like "$15", "Free", "$10 - $25", "" or occasionally prose. We take
    the lowest number present, and treat an explicit "free" as zero.
    """
    if event.get("cost_details"):
        # values[] is nominally numeric but venues put strings like "free" in it,
        # so parse defensively rather than trusting the field's declared type.
        vals = []
        for v in event["cost_details"].get("values", []):
            s = str(v).strip()
            if not s:
                continue
            if re.fullmatch(r"free", s, re.I):
                vals.append(0.0)
                continue
            m = re.search(r"\d+(?:\.\d+)?", s.replace(",", ""))
            if m:
                vals.append(float(m.group()))
        if vals:
            return min(vals), max(vals), min(vals) <= 0
    s = (cost_str or "").strip()
    if not s:
        return None, None, False
    if re.search(r"\bfree\b|\bno charge\b|\$0\b", s, re.I):
        return 0.0, 0.0, True
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", s.replace(",", ""))]
    if nums:
        return min(nums), max(nums), False
    return None, None, False


def _parse_one(e, venue_cfg):
    title = clean_text(e.get("title"), 200)
    if not title or is_noise(title):
        return None

    start = e.get("start_date")   # "2026-08-16 10:00:00"
    if not start:
        return None
    start_local = start.replace(" ", "T")
    end = e.get("end_date")
    end_local = end.replace(" ", "T") if end else None

    v = e.get("venue") or {}
    venue_name = clean_text(v.get("venue"), 120) or venue_cfg["name"]
    city = clean_text(v.get("city"), 60) or venue_cfg["city"]
    lat = v.get("geo_lat") or None
    lon = v.get("geo_lng") or None
    try:
        lat = float(lat) if lat else None
        lon = float(lon) if lon else None
    except (TypeError, ValueError):
        lat = lon = None

    price_min, price_max, is_free = _parse_cost(e.get("cost"), e)
    tags = [clean_text(c.get("name"), 40) for c in (e.get("categories") or [])]
    tags = [t.lower() for t in tags if t]

    image = None
    img = e.get("image")
    if isinstance(img, dict):
        image = img.get("url")
    elif isinstance(img, str):
        image = img or None

    return make_event(
        source=venue_cfg["key"],
        title=title,
        start_local=start_local,
        end_local=end_local,
        all_day=bool(e.get("all_day")),
        url=e.get("url"),
        venue=venue_name,
        city=city,
        lat=lat, lon=lon,
        category="family" if venue_cfg["key"] in ("curiodyssey", "hiller", "chabot") else "arts",
        tags=tags,
        price_min=price_min,
        price_max=price_max,
        is_free=is_free,
        image=image,
        description=e.get("excerpt") or e.get("description"),
    )


def fetch_venue(venue_cfg, window_start, window_end):
    """Fetch one venue's calendar. Raises on failure so the runner can record it."""
    out = []
    base = "https://%s/wp-json/tribe/events/v1/events" % venue_cfg["domain"]
    for page in range(1, MAX_PAGES + 1):
        params = {
            "per_page": PER_PAGE,
            "page": page,
            "start_date": window_start.strftime("%Y-%m-%d 00:00:00"),
            "end_date": window_end.strftime("%Y-%m-%d 23:59:59"),
        }
        try:
            data = http_get_json(base + "?" + urllib.parse.urlencode(params))
        except Exception:
            # The plugin returns 404 rather than an empty list once you page past
            # the end, so a failure on page 2+ means we are simply done.
            if page > 1:
                break
            raise

        events = data.get("events") or []
        for e in events:
            ev = _parse_one(e, venue_cfg)
            if ev:
                out.append(ev)

        if len(events) < PER_PAGE:
            break
        time.sleep(0.25)
    return out
