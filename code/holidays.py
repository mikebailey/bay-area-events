"""Which days the family is actually free, and what that does to ranking.

The page used to weight a day purely by its position in the week: Saturday 1.00,
Monday 0.35. That is a decent proxy, and it is wrong in exactly the case that
matters most. Labor Day 2026 fell on a Monday, so every event on the one day the
whole family was off got divided by three and buried under a Tuesday concert.

What the weight is really trying to say is not "is it Saturday", it is "how free
are we". So that is what this computes, from two calendars:

    MIT Institute Holidays          -> Mike is off
    MPCSD instructional calendar    -> the kids are off

Neither alone is the answer, and the mismatches are the whole point. Patriots'
Day is an MIT holiday and an ordinary school day in California, so it is a
date-day, not a family day. Spring break is the reverse: the kids are home and
Mike is working. Only the overlap earns a Saturday-grade weight.

This module is the single source of truth for day weighting. digest.py imports
it directly; build.py bakes the resulting weights into events.json so the page
reads them rather than carrying a second copy of the rules that can drift.

    python code/holidays.py             # print the next year of free days
"""
from datetime import date, timedelta

# --------------------------------------------------------------------------
# MIT Institute Holidays, from hr.mit.edu/holidays. Transcribed rather than
# computed: MIT observes Patriots' Day, which no generic US holiday library
# carries, and it shifts Independence Day and Christmas by its own rules.
#
# REFRESH ONCE A YEAR. MIT publishes the next year each autumn; when this list
# runs out, status() falls back to the base day-of-week weight and nothing
# breaks, it just stops knowing about holidays.
# --------------------------------------------------------------------------
MIT_HOLIDAYS = {
    "2026-01-01": "New Year's Day",
    "2026-01-02": "MIT winter closing",
    "2026-01-19": "Martin Luther King, Jr. Day",
    "2026-02-16": "Presidents Day",
    "2026-04-20": "Patriots' Day",
    "2026-05-25": "Memorial Day",
    "2026-06-19": "Juneteenth",
    "2026-07-03": "Independence Day",
    "2026-09-07": "Labor Day",
    "2026-10-12": "Indigenous Peoples' Day",
    "2026-11-11": "Veterans Day",
    "2026-11-26": "Thanksgiving",
    "2026-11-27": "Day after Thanksgiving",
    "2026-12-25": "Christmas Day",
    "2026-12-26": "MIT winter closing",
    "2026-12-27": "MIT winter closing",
    "2026-12-28": "MIT winter closing",
    "2026-12-29": "MIT winter closing",
    "2026-12-30": "MIT winter closing",
    "2026-12-31": "MIT winter closing",
    "2027-01-01": "New Year's Day",
    "2027-01-18": "Martin Luther King, Jr. Day",
    "2027-02-15": "Presidents Day",
    "2027-04-19": "Patriots' Day",
    "2027-05-31": "Memorial Day",
    "2027-06-18": "Juneteenth",
    "2027-07-05": "Independence Day",
    "2027-09-06": "Labor Day",
    "2027-10-11": "Indigenous Peoples' Day",
    "2027-11-11": "Veterans Day",
    "2027-11-25": "Thanksgiving",
    "2027-11-26": "Day after Thanksgiving",
    "2027-12-24": "Christmas Day",
}

# --------------------------------------------------------------------------
# Menlo Park City School District, 2026-27 instructional calendar (board
# approved 2026-03-05). Spans are inclusive at both ends.
#
# Staff development and teacher workdays are in here deliberately: from the
# family's point of view a "District Staff Development" day is simply a day the
# kids are home, which is all this calendar is being asked.
#
# REFRESH ONCE A YEAR from district.mpcsd.org/calendar.
# --------------------------------------------------------------------------
SCHOOL_CLOSURES = [
    ("2026-08-18", "2026-08-19", "no school"),          # PD day + teacher workday
    ("2026-09-07", "2026-09-07", "Labor Day"),
    ("2026-10-12", "2026-10-12", "no school"),          # district staff development
    ("2026-11-11", "2026-11-11", "Veterans Day"),
    ("2026-11-23", "2026-11-27", "Thanksgiving break"),
    ("2026-12-21", "2027-01-01", "winter break"),
    ("2027-01-04", "2027-01-04", "no school"),          # teacher workday
    ("2027-01-18", "2027-01-18", "Martin Luther King, Jr. Day"),
    ("2027-02-15", "2027-02-19", "mid-winter break"),
    ("2027-03-26", "2027-03-26", "no school"),          # district staff development
    ("2027-04-12", "2027-04-16", "spring break"),
    ("2027-05-31", "2027-05-31", "Memorial Day"),
]

# Summer is handled separately from the closure list. Eleven weeks of "the kids
# are off" is true but says almost nothing about a given Tuesday, so it gets its
# own much smaller weight rather than lifting the whole of July.
SCHOOL_YEAR_FIRST_DAY = "2026-08-20"
SCHOOL_YEAR_LAST_DAY = "2027-06-11"

# How far the school calendar can be trusted: the published year plus the summer
# either side of it. Outside this the district calendar says nothing, and it has
# to say nothing rather than guess. Treating "after the last published day" as
# summer forever quietly lifted every weekday in 2028 from 0.35 to 0.42 and
# labelled Christmas 2028 a summer holiday.
SCHOOL_KNOWN_FROM = "2026-06-15"
SCHOOL_KNOWN_UNTIL = "2027-08-31"

# What every California district closes for, used only once the school calendar
# above has gone stale. Without it, the first Labor Day past the table would be
# scored as an MIT-only day off and demoted to 0.55, which is a worse failure
# than the staleness itself.
#
# Deliberately excludes Patriots' Day, Juneteenth, Veterans Day and Indigenous
# Peoples' Day: MPCSD closes for some of them, plenty of districts do not, and
# guessing wrong there is the exact error this module exists to avoid.
UNIVERSAL_SCHOOL_HOLIDAYS = {
    "New Year's Day", "Martin Luther King, Jr. Day", "Presidents Day",
    "Memorial Day", "Independence Day", "Labor Day", "Thanksgiving",
    "Day after Thanksgiving", "Christmas Day",
}

# --------------------------------------------------------------------------
# Weights. Index is Python's weekday(): Monday 0 .. Sunday 6.
#
# Saturday is the reference point at 1.00 and nothing exceeds it, because a
# holiday should promote a day to weekend-grade, never above it.
# --------------------------------------------------------------------------
BASE_WEIGHT = [0.35, 0.35, 0.35, 0.45, 0.70, 1.00, 0.85]

W_FAMILY = 1.00   # both calendars off: as good as a Saturday, and rarer
W_MIT = 0.55      # Mike off, kids in school. Real, but it is a date-day
W_SCHOOL = 0.50   # kids off, Mike working. A matinee, not a day out
W_SUMMER = 0.42   # kids off for eleven weeks; a Tuesday is still a Tuesday
W_EVE = 0.95      # the night before a family day: no school run in the morning


def _d(x):
    """Accept a date, a datetime, or any ISO string that starts with a date."""
    if isinstance(x, date):
        return x if type(x) is date else x.date()
    return date.fromisoformat(str(x)[:10])


def _school_closure(d):
    iso = d.isoformat()
    for start, end, name in SCHOOL_CLOSURES:
        if start <= iso <= end:
            return name
    return None


def school_calendar_known(d):
    """Whether the district calendar covers this date at all."""
    return SCHOOL_KNOWN_FROM <= _d(d).isoformat() <= SCHOOL_KNOWN_UNTIL


def _is_summer(d):
    """Between school years, within the range the calendar actually covers."""
    if not school_calendar_known(d):
        return False
    iso = d.isoformat()
    return iso < SCHOOL_YEAR_FIRST_DAY or iso > SCHOOL_YEAR_LAST_DAY


def status(d):
    """Who is off on this date, and what to call it.

    Returns a dict, always with the same keys:
        mit / school  bool, whether that calendar is off
        kind          "family" | "mit" | "school" | "summer" |
                      "weekend-holiday" | None
        name          display label, or None
    """
    d = _d(d)
    iso = d.isoformat()
    weekend = d.weekday() >= 5

    mit_name = MIT_HOLIDAYS.get(iso)
    school_name = _school_closure(d)
    summer = _is_summer(d)
    school_off = school_name is not None or summer

    # Past the end of the district calendar, fall back to the holidays every
    # district closes for, so the common cases keep working for a year after
    # the table goes stale rather than silently demoting Thanksgiving.
    if not school_calendar_known(d) and mit_name in UNIVERSAL_SCHOOL_HOLIDAYS:
        school_off = True

    # The two calendars name the same day differently often enough to matter:
    # Oct 12 2026 is "Indigenous Peoples' Day" to MIT and "District Staff
    # Development" to the district. The public name is the useful one.
    name = mit_name or (school_name if school_name != "no school" else None)

    if mit_name and school_off:
        kind = "family"
    elif mit_name:
        kind = "mit"
    elif school_name:
        kind = "school"
    elif summer:
        kind = "summer"
    else:
        kind = None

    # A holiday landing on a weekend is not extra time off, so it keeps its name
    # for display and stops driving the weight.
    if weekend and kind:
        kind = "weekend-holiday" if (mit_name or school_name) else None

    return {"mit": bool(mit_name), "school": bool(school_off),
            "kind": kind, "name": name}


def is_family_day(d):
    """A day the whole household is off, and not merely the weekend."""
    return status(d)["kind"] == "family"


def day_weight(d):
    """How much a day is worth to a family looking for something to do.

    Never below the day-of-week baseline: a holiday can promote a Monday, it
    must not demote a Saturday.
    """
    d = _d(d)
    base = BASE_WEIGHT[d.weekday()]
    st = status(d)

    bump = {"family": W_FAMILY, "mit": W_MIT,
            "school": W_SCHOOL, "summer": W_SUMMER}.get(st["kind"], 0.0)

    # The evening before a family day is worth far more than its weekday slot
    # suggests, because what makes a Sunday night bad is the Monday morning
    # after it. The Sunday of Labor Day weekend is the case in point.
    if not is_family_day(d) and is_family_day(d + timedelta(days=1)):
        bump = max(bump, W_EVE)

    return round(max(base, bump), 3)


def span_for(d):
    """The contiguous stretch of free days containing this one.

    Used to headline a long weekend as a unit. Walks outward over days that are
    either a weekend or a family holiday, so Labor Day Monday returns the
    Saturday through Monday around it and Thanksgiving returns Thursday through
    Sunday. Returns (first, last), or None if the day is not free at all.
    """
    d = _d(d)

    def free(x):
        return x.weekday() >= 5 or is_family_day(x)

    if not free(d):
        return None
    first = last = d
    while free(first - timedelta(days=1)):
        first -= timedelta(days=1)
    while free(last + timedelta(days=1)):
        last += timedelta(days=1)
    return first, last


def next_family_day(start, within_days=400):
    """The next family holiday on or after `start`, as (date, name)."""
    start = _d(start)
    for i in range(within_days):
        d = start + timedelta(days=i)
        if is_family_day(d):
            return d, status(d)["name"] or "holiday"
    return None, None


def family_days_between(start, end):
    """Every family holiday in an inclusive date range, as (date, name)."""
    start, end = _d(start), _d(end)
    out, d = [], start
    while d <= end:
        if is_family_day(d):
            out.append((d, status(d)["name"] or "holiday"))
        d += timedelta(days=1)
    return out


def calendar_for(start, end):
    """The day table baked into events.json for the page to read.

    Only days that deviate from the plain day-of-week baseline are emitted, plus
    any named holiday, which keeps this to a few dozen entries rather than 120.
    Each entry is {w: weight, n: name, k: kind}.
    """
    start, end = _d(start), _d(end)
    out = {}
    d = start
    while d <= end:
        st = status(d)
        w = day_weight(d)
        if abs(w - BASE_WEIGHT[d.weekday()]) > 1e-9 or st["name"]:
            entry = {"w": w}
            if st["name"]:
                entry["n"] = st["name"]
            if st["kind"]:
                entry["k"] = st["kind"]
            out[d.isoformat()] = entry
        d += timedelta(days=1)
    return out


def _main():
    today = date.today()
    print("Day weights and free days from %s\n" % today)
    print("%-12s %-4s %6s  %-16s %s" % ("date", "dow", "weight", "kind", "name"))
    d, shown = today, 0
    while shown < 80 and d < today + timedelta(days=400):
        st = status(d)
        w = day_weight(d)
        if st["kind"] or w != BASE_WEIGHT[d.weekday()]:
            print("%-12s %-4s %6.2f  %-16s %s"
                  % (d, d.strftime("%a"), w, st["kind"] or "", st["name"] or ""))
            shown += 1
        d += timedelta(days=1)


if __name__ == "__main__":
    _main()
