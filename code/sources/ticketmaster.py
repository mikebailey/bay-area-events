"""Ticketmaster Discovery API: concerts, pro sports, big theater.

The single highest-yield source. One geo query around Menlo Park covers Chase
Center, Levi's, Oracle Park, SAP Center, Shoreline, Frost, the Fox, and most
ticketed venues in the region.

Note the API's hard paging cap: page * size may not exceed 1000 results per
query, and there are far more than 1000 events in our window. So we slice the
window into two-week chunks and page within each, which keeps every chunk under
the ceiling. Without this you silently get only the first 1000 events, ordered by
date, and lose everything past about six weeks out.
"""
import os
import time
import urllib.parse
from datetime import timedelta

from config import HOME, SEARCH_RADIUS_MILES
from sources.base import http_get_json, is_noise, make_event

API = "https://app.ticketmaster.com/discovery/v2/events.json"
PAGE_SIZE = 199          # API rejects 200 on some endpoints; 199 is safe
MAX_PAGES = 5            # 5 * 199 = 995, just under the 1000 cap
CHUNK_DAYS = 14

# Ticketmaster's segment names mapped to our categories.
SEGMENT_MAP = {
    "Music": "music",
    "Sports": "sports",
    "Arts & Theatre": "arts",
    "Film": "film",
    "Miscellaneous": "other",
}


def _api_key():
    key = os.environ.get("TICKETMASTER_API_KEY", "").strip()
    if not key or "PASTE" in key:
        raise RuntimeError("TICKETMASTER_API_KEY missing from environment/.env")
    return key


def _parse_one(e):
    dates = e.get("dates", {}).get("start", {}) or {}
    local_date = dates.get("localDate")
    if not local_date:
        return None
    local_time = dates.get("localTime")
    all_day = local_time is None
    start_local = "%sT%s" % (local_date, local_time or "00:00:00")

    venues = (e.get("_embedded") or {}).get("venues") or [{}]
    v = venues[0] or {}
    loc = v.get("location") or {}
    lat = float(loc["latitude"]) if loc.get("latitude") else None
    lon = float(loc["longitude"]) if loc.get("longitude") else None

    # Price ranges are frequently absent; that is fine, it becomes "?" not free.
    price_min = price_max = None
    for pr in e.get("priceRanges") or []:
        lo, hi = pr.get("min"), pr.get("max")
        if lo is not None:
            price_min = lo if price_min is None else min(price_min, lo)
        if hi is not None:
            price_max = hi if price_max is None else max(price_max, hi)

    classifications = e.get("classifications") or [{}]
    seg = ((classifications[0].get("segment") or {}).get("name")) or ""
    genre = ((classifications[0].get("genre") or {}).get("name")) or ""

    images = e.get("images") or []
    image = None
    if images:
        # Prefer a wide, reasonably large image for the card layout.
        wide = [i for i in images if i.get("ratio") == "16_9" and (i.get("width") or 0) >= 640]
        image = (wide or images)[0].get("url")

    tags = [t for t in [genre.lower()] if t]
    if (e.get("dates") or {}).get("status", {}).get("code") == "cancelled":
        return None

    return make_event(
        source="ticketmaster",
        title=e.get("name"),
        start_local=start_local,
        end_local=None,
        all_day=all_day,
        url=e.get("url"),
        venue=v.get("name"),
        city=(v.get("city") or {}).get("name"),
        lat=lat, lon=lon,
        category=SEGMENT_MAP.get(seg, "other"),
        tags=tags,
        price_min=price_min,
        price_max=price_max,
        image=image,
        description=(e.get("info") or e.get("pleaseNote")),
    )


def fetch(window_start, window_end):
    key = _api_key()
    out, chunk_start = [], window_start

    while chunk_start <= window_end:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), window_end)
        for page in range(MAX_PAGES):
            params = {
                "apikey": key,
                "latlong": "%s,%s" % HOME,
                "radius": SEARCH_RADIUS_MILES,
                "unit": "miles",
                "size": PAGE_SIZE,
                "page": page,
                "sort": "date,asc",
                "startDateTime": chunk_start.strftime("%Y-%m-%dT00:00:00Z"),
                "endDateTime": chunk_end.strftime("%Y-%m-%dT23:59:59Z"),
            }
            data = http_get_json(API + "?" + urllib.parse.urlencode(params))
            events = (data.get("_embedded") or {}).get("events") or []
            for e in events:
                if is_noise(e.get("name")):
                    continue
                ev = _parse_one(e)
                if ev:
                    out.append(ev)

            page_info = data.get("page") or {}
            if page + 1 >= (page_info.get("totalPages") or 0):
                break
            time.sleep(0.25)  # stay well inside the rate limit

        chunk_start = chunk_end + timedelta(days=1)

    return out
