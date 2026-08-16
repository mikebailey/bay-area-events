"""Project-wide configuration: where home is, how wide we cast the net."""
from pathlib import Path

# Project root, derived rather than hardcoded so this works on Mac, PC, and CI.
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SITE = ROOT / "site"
DB_PATH = DATA / "events.db"

# Home base: Menlo Park, CA. Everything is ranked by travel time from here.
HOME = (37.4530, -122.1817)
HOME_LABEL = "Menlo Park, CA"

# How far we look. Local is the everyday list; day trips must clear a higher bar.
LOCAL_DRIVE_MAX_MIN = 60      # beyond this, an event is a "day trip"
                              # (60 not 75, so Gilroy at ~71min qualifies)
DAY_TRIP_DRIVE_MAX_MIN = 165  # ~2h45m; past this we do not bother
SEARCH_RADIUS_MILES = 120     # what we ask upstream sources for

# How far ahead to look.
HORIZON_DAYS = 120

# Hiller and similar venues publish an "event" for every open day, which is
# operating hours, not something to go do. Anything matching these is dropped.
NOISE_TITLE_PATTERNS = [
    r"^today'?s schedule",
    r"^museum (open|hours)",
    r"^(open|closed) (daily|today)",
    r"^general admission$",
    r"^daily (schedule|hours)",
]

# The Events Calendar (WordPress plugin) venues, verified live 2026-08-15.
# Adding a venue here is the whole integration; the parser is shared.
TRIBE_VENUES = [
    {"key": "curiodyssey",   "domain": "curiodyssey.org",      "name": "CuriOdyssey",                  "city": "San Mateo"},
    {"key": "filoli",        "domain": "filoli.org",           "name": "Filoli",                       "city": "Woodside"},
    {"key": "hiller",        "domain": "hiller.org",           "name": "Hiller Aviation Museum",       "city": "San Carlos"},
    {"key": "chabot",        "domain": "www.chabotspace.org",  "name": "Chabot Space & Science Center","city": "Oakland"},
    {"key": "oaklandmuseum", "domain": "museumca.org",         "name": "Oakland Museum of California", "city": "Oakland"},
]

# Funcheap. Scraped from day archives, not RSS: the feed is capped at 10 items
# and every pagination form returns identical cached content (verified 2026-08-15).
# Capped to a nearer horizon than the APIs since it costs one request per day.
FUNCHEAP_MAX_DAYS = 60

USER_AGENT = "Mozilla/5.0 (compatible; BayAreaEvents/0.1; +https://bayarea.michaelbailey.org)"
HTTP_TIMEOUT = 30
