"""Filoli's own events calendar, scraped.

Filoli used to run The Events Calendar and was served by the shared tribe.py
parser along with four other venues. Some time before 2026-09-07 they
replatformed onto Umbraco with Blackbaud ticketing, and the whole WordPress REST
API went with it -- `/wp-json/` itself 404s now, not just the events endpoint.
So this venue needs its own scraper where a line in config.py used to do.

Worth the effort rather than dropping: Filoli is eleven minutes from home, the
same pocket as the Kings Mountain Art Fair, and it runs exactly the kind of
one-off seasonal thing (Nightfall, Holiday Lights, wreath parties) that no
ticketing feed carries.

What the new site gives us, established 2026-09-07:

  * `/whats-on/events/?p=N` is server-rendered HTML, five events per page, and
    the page count is small (four pages, sixteen events). Page N past the end
    returns a valid page with zero listing items, which is the loop's stop
    signal.
  * Each event is a `<li class="listing-item">` with the title and URL in an
    `<h4><a>` and the dates in an icon list beside a `fa-calendar-alt`.
  * There is a filter form taking `period`, `from`, `to` and `category`, but
    `from`/`to` want dd/mm/yyyy (the site is built en-GB) and the whole calendar
    is only four pages. Pulling all of it and filtering by date here is fewer
    requests and one less thing to get wrong.
  * No price anywhere in the listing. It lives on the detail page, which would
    cost one request per event; left alone, so these arrive with price unknown
    rather than wrongly marked free.

The date line comes in four shapes, all of which this handles:

    Sep 9th - Sep 18th 2026                     range, year only at the end
    Nov 14th 2026 - Jan 10th 2027               range across a year boundary
    Oct 27th 2026                               one day, no time
    Sep 16th 2026: 10:30am - 11:30am
        More dates Sep 16th 2026: 1:00pm ...    repeats, sometimes same day
"""
import re
from datetime import datetime

from sources.base import BROWSER_UA, clean_text, http_get, make_event

BASE = "https://filoli.org/whats-on/events/"
MAX_PAGES = 12          # four in practice; the cap is for a listing that grows

VENUE = "Filoli"
CITY = "Woodside"

_ITEM_RE = re.compile(
    r'<li class="listing-item">(.*?)</li>\s*(?=<li class="listing-item">|</ol>)', re.S)
_TITLE_RE = re.compile(r'<h4[^>]*>\s*<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_DATE_RE = re.compile(
    r'<li>\s*<i class="[^"]*calendar[^"]*"[^>]*></i>(.*?)</li>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}

# "Sep 16th 2026" / "Sep 9th". The year is optional because the first half of a
# range routinely omits it.
_DAY_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+"
    r"(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{4}))?", re.I)

# "10:30am", "9am", "12pm"
_TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.I)


def _text(s):
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", s or "")).replace("&amp;", "&").strip()


def _to_24h(hour, minute, meridiem):
    hour = int(hour) % 12
    if meridiem.lower() == "pm":
        hour += 12
    return hour, int(minute or 0)


def _parse_dates(text):
    """Turn one calendar line into [(date, time_or_None), ...] plus an end date.

    Returns (occurrences, range_end). A range yields a single occurrence at its
    first day and a range_end at its last; discrete dates yield one occurrence
    each and no range_end.
    """
    text = _text(text)
    if not text:
        return [], None

    # A range is two date tokens joined by a dash with no time and no "More
    # dates" anywhere. Anything carrying times is a list of occurrences, even
    # when it also contains a dash (between start and end times).
    days = list(_DAY_RE.finditer(text))
    if not days:
        return [], None

    has_times = bool(_TIME_RE.search(text))
    is_range = (not has_times and len(days) == 2
                and re.search(r"\d(?:st|nd|rd|th)?\s*(?:\d{4}\s*)?[-–—]\s*(?:jan|feb|mar|apr|"
                              r"may|jun|jul|aug|sep|oct|nov|dec)", text, re.I))

    def build(match, fallback_year):
        month = _MONTHS[match.group(1)[:3].lower()]
        day = int(match.group(2))
        year = int(match.group(3)) if match.group(3) else fallback_year
        if year is None:
            return None
        try:
            return datetime(year, month, day).date()
        except ValueError:
            return None

    # The year, when only one is printed, belongs to the last token.
    trailing_year = next((int(m.group(3)) for m in reversed(days) if m.group(3)), None)

    if is_range:
        first = build(days[0], trailing_year)
        last = build(days[1], trailing_year)
        if not first or not last:
            return [], None
        if last < first:
            # Not a shape seen in the wild; bail rather than emit a backwards
            # span that would classify strangely downstream.
            return [], None
        return [(first, None)], last

    # Discrete occurrences. Pair each date with the first time that follows it,
    # so "Sep 16th 2026: 10:30am - 11:30am" takes 10:30 and ignores the end.
    out = []
    for i, m in enumerate(days):
        d = build(m, trailing_year)
        if not d:
            continue
        stop = days[i + 1].start() if i + 1 < len(days) else len(text)
        tm = _TIME_RE.search(text, m.end(), stop)
        out.append((d, _to_24h(*tm.groups()) if tm else None))

    # The same day can appear several times with different session times. One
    # row per day is what the rest of the pipeline expects, so keep the
    # earliest.
    best = {}
    for d, t in out:
        if d not in best or (t is not None and (best[d] is None or t < best[d])):
            best[d] = t
    return sorted(best.items()), None


def _parse_item(block):
    tm = _TITLE_RE.search(block)
    if not tm:
        return []
    url = tm.group(1)
    title = clean_text(_text(tm.group(2)), 200)
    if not title or not url:
        return []
    if url.startswith("/"):
        url = "https://filoli.org" + url

    dm = _DATE_RE.search(block)
    if not dm:
        return []
    occurrences, range_end = _parse_dates(dm.group(1))
    if not occurrences:
        return []

    out = []
    for day, tod in occurrences:
        if tod is None:
            start = day.strftime("%Y-%m-%dT00:00:00")
            all_day = True
        else:
            start = "%sT%02d:%02d:00" % (day.isoformat(), tod[0], tod[1])
            all_day = False
        end = range_end.strftime("%Y-%m-%dT23:59:00") if range_end else None
        ev = make_event(
            source="filoli",
            title=title,
            start_local=start,
            end_local=end,
            all_day=all_day,
            url=url,
            venue=VENUE,
            city=CITY,
            category="community",
            tags=["filoli"],
        )
        if ev:
            out.append(ev)
    return out


def fetch(window_start, window_end):
    """Every listed Filoli event that falls inside the window."""
    out, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        body = http_get("%s?p=%d" % (BASE, page), ua=BROWSER_UA)
        items = _ITEM_RE.findall(body)
        if not items:
            break                     # a page past the end renders with no items
        for block in items:
            for ev in _parse_item(block):
                day = ev["start_local"][:10]
                if not (window_start.isoformat() <= day <= window_end.isoformat()):
                    continue
                if ev["id"] in seen:
                    continue
                seen.add(ev["id"])
                out.append(ev)
    return out
