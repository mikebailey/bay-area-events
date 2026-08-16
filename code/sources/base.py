"""Shared plumbing for every source module.

Each source implements one function:

    def fetch(window_start: date, window_end: date) -> list[dict]

returning event dicts built by make_event(). Sources may raise freely; the runner
in fetch.py isolates failures so one broken feed cannot take down the others.
"""
import hashlib
import html
import json
import re
import ssl
import time
import urllib.error
import urllib.request

from config import HOME, HTTP_TIMEOUT, NOISE_TITLE_PATTERNS, USER_AGENT
import geo

_SSL_CTX = ssl.create_default_context()
_NOISE_RE = [re.compile(p, re.I) for p in NOISE_TITLE_PATTERNS]


def http_get(url, timeout=HTTP_TIMEOUT, retries=3):
    """GET with a real User-Agent and polite exponential backoff."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            # 4xx other than rate limiting will not fix themselves; fail fast.
            if e.code in (400, 401, 403, 404):
                raise
            last = e
        except Exception as e:
            last = e
        time.sleep(1.5 * (2 ** attempt))
    raise last


def http_get_json(url, **kw):
    return json.loads(http_get(url, **kw))


def clean_text(s, limit=400):
    """Strip HTML, decode entities, collapse whitespace, truncate."""
    if not s:
        return None
    s = re.sub(r"<[^>]+>", " ", str(s))
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit] or None


# Sites use curly punctuation, so "Today's Schedule" carries U+2019 rather than an
# ASCII apostrophe. Matching without normalizing silently lets all of it through.
_SMART = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                        "–": "-", "—": "-", " ": " "})


def normalize_punct(s):
    return (s or "").translate(_SMART)


def is_noise(title):
    """True for operating-hours pseudo-events like "Today's Schedule 9-5"."""
    if not title:
        return True
    t = normalize_punct(title)
    return any(rx.search(t) for rx in _NOISE_RE)


def make_id(title, start_local, venue):
    """Stable dedupe key: normalized title + date + venue token.

    Deliberately uses the DATE not the timestamp, so the same show listed at 7:00pm
    by one feed and 7:30pm by another collapses into one event.
    """
    t = re.sub(r"[^a-z0-9]+", "", (title or "").lower())[:40]
    d = (start_local or "")[:10]
    v = re.sub(r"[^a-z0-9]+", "", (venue or "").lower())[:16]
    return hashlib.sha1(("%s|%s|%s" % (t, d, v)).encode("utf-8")).hexdigest()[:16]


def price_band(price_min, is_free):
    if is_free or (price_min is not None and price_min <= 0):
        return "Free"
    if price_min is None:
        return "?"
    if price_min < 20:
        return "$"
    if price_min < 50:
        return "$$"
    return "$$$"


def make_event(*, source, title, start_local, url, end_local=None, venue=None, city=None,
               lat=None, lon=None, category=None, tags=None, price_min=None, price_max=None,
               is_free=False, image=None, description=None, all_day=False):
    """Normalize one raw record into the shared event shape.

    Fills in coordinates from the city table when the source gave none, then
    derives region and drive time from whatever location we ended up with.
    """
    title = clean_text(title, 200)
    if not title or not start_local:
        return None

    if lat is None or lon is None:
        c = geo.coords_for_city(city or venue)
        if c:
            lat, lon = c

    drive = geo.drive_minutes(HOME, (lat, lon)) if lat is not None and lon is not None else None
    region = geo.region_for_city(city) if city else (
        geo.region_for_city(venue) if venue else "Unknown")

    if is_free or (price_min is not None and price_min <= 0):
        is_free = True
        price_min = 0.0

    return {
        "id": make_id(title, start_local, venue),
        "title": title,
        "start_local": start_local,
        "end_local": end_local,
        "all_day": int(bool(all_day)),
        "venue": clean_text(venue, 120),
        "city": clean_text(city, 60),
        "region": region,
        "lat": lat,
        "lon": lon,
        "drive_minutes": drive,
        "category": category,
        "tags": tags or [],
        "price_min": price_min,
        "price_max": price_max,
        "is_free": int(bool(is_free)),
        "price_band": price_band(price_min, is_free),
        "url": url,
        "image": image,
        "description": clean_text(description, 400),
        "sources": [source],
    }
