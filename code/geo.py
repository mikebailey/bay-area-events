"""Distance and drive-time estimation, with no geocoding API required.

Ticketmaster hands us coordinates directly. Everything else gives a city name at
best, so we look the city up in a static table. That keeps the daily job free of
API keys, rate limits, and one more thing that can break at 6am.

Drive times are ESTIMATES from straight-line distance, not routed times. Bay Area
geography makes this imperfect (a bridge crossing is slower than the crow flies),
so the multiplier is deliberately pessimistic. Good enough to sort and filter by,
not something to plan an arrival time around.
"""
import math
import re

# Bay Area cities and towns, roughly ordered outward from the Peninsula.
CITY_COORDS = {
    # Peninsula
    "menlo park": (37.4530, -122.1817), "palo alto": (37.4419, -122.1430),
    "east palo alto": (37.4688, -122.1411), "atherton": (37.4613, -122.1974),
    "redwood city": (37.4852, -122.2364), "san carlos": (37.5072, -122.2605),
    "belmont": (37.5202, -122.2758), "san mateo": (37.5630, -122.3255),
    "foster city": (37.5585, -122.2711), "burlingame": (37.5779, -122.3480),
    "millbrae": (37.5985, -122.3872), "hillsborough": (37.5741, -122.3794),
    "woodside": (37.4299, -122.2539), "portola valley": (37.3841, -122.2352),
    "half moon bay": (37.4636, -122.4286), "pacifica": (37.6138, -122.4869),
    "south san francisco": (37.6547, -122.4077), "san bruno": (37.6305, -122.4111),
    "daly city": (37.6879, -122.4702), "brisbane": (37.6808, -122.3999),
    # San Francisco
    "san francisco": (37.7749, -122.4194), "sf": (37.7749, -122.4194),
    # South Bay
    "mountain view": (37.3861, -122.0839), "los altos": (37.3852, -122.1141),
    "sunnyvale": (37.3688, -122.0363), "santa clara": (37.3541, -121.9552),
    "san jose": (37.3382, -121.8863), "cupertino": (37.3230, -122.0322),
    "campbell": (37.2872, -121.9500), "saratoga": (37.2638, -122.0230),
    "los gatos": (37.2358, -121.9624), "milpitas": (37.4323, -121.8996),
    "morgan hill": (37.1305, -121.6544), "gilroy": (37.0058, -121.5683),
    "stanford": (37.4275, -122.1697),
    # East Bay
    "oakland": (37.8044, -122.2712), "berkeley": (37.8715, -122.2730),
    "emeryville": (37.8313, -122.2852), "alameda": (37.7652, -122.2416),
    "fremont": (37.5485, -121.9886), "hayward": (37.6688, -122.0808),
    "san leandro": (37.7249, -122.1561), "union city": (37.5934, -122.0438),
    "newark": (37.5297, -122.0402), "richmond": (37.9358, -122.3477),
    "walnut creek": (37.9101, -122.0652), "concord": (37.9780, -122.0311),
    "pleasanton": (37.6624, -121.8747), "livermore": (37.6819, -121.7680),
    "dublin": (37.7022, -121.9358), "danville": (37.8216, -121.9999),
    "orinda": (37.8771, -122.1797), "lafayette": (37.8858, -122.1180),
    "san ramon": (37.7799, -121.9780), "castro valley": (37.6941, -122.0863),
    "martinez": (38.0194, -122.1341), "antioch": (38.0049, -121.8058),
    # North Bay
    "sausalito": (37.8591, -122.4853), "mill valley": (37.9060, -122.5450),
    "san rafael": (37.9735, -122.5311), "novato": (38.1074, -122.5697),
    "larkspur": (37.9341, -122.5353), "corte madera": (37.9255, -122.5272),
    "petaluma": (38.2324, -122.6367), "santa rosa": (38.4404, -122.7141),
    "sonoma": (38.2919, -122.4580), "napa": (38.2975, -122.2869),
    "st helena": (38.5052, -122.4703), "calistoga": (38.5788, -122.5797),
    "vallejo": (38.1041, -122.2566), "benicia": (38.0494, -122.1586),
    # Small towns the editorial sweep turns up and the ticketing feeds never do.
    # Added 2026-09-07 after a Labor Day sweep returned Roaring Camp at Felton,
    # a Bolinas BBQ and a Nicasio concert, all of which arrived with no drive
    # time at all and were ranked as though their location were unknown.
    "felton": (37.0513, -122.0736), "ben lomond": (37.0891, -122.0864),
    "boulder creek": (37.1258, -122.1219), "scotts valley": (37.0511, -122.0147),
    "aptos": (36.9772, -121.8994), "soquel": (36.9880, -121.9566),
    "la honda": (37.3188, -122.2739), "pescadero": (37.2552, -122.3833),
    "san gregorio": (37.3266, -122.3803), "loma mar": (37.2666, -122.3086),
    "moss beach": (37.5233, -122.5133), "el granada": (37.5027, -122.4692),
    "montara": (37.5422, -122.5061),
    "bolinas": (37.9091, -122.6864), "nicasio": (38.0596, -122.6983),
    "point reyes station": (38.0669, -122.8069), "olema": (38.0399, -122.7869),
    "stinson beach": (37.9007, -122.6442), "fairfax": (37.9871, -122.5889),
    "san anselmo": (37.9746, -122.5616), "ross": (37.9624, -122.5550),
    "kentfield": (37.9527, -122.5572), "tiburon": (37.8735, -122.4566),
    "briones": (37.9327, -122.1441), "pleasant hill": (37.9480, -122.0608),
    "moraga": (37.8349, -122.1297), "piedmont": (37.8244, -122.2316),
    "albany": (37.8869, -122.2977), "el cerrito": (37.9161, -122.3108),
    # Day-trip range
    "santa cruz": (36.9741, -122.0308), "capitola": (36.9752, -121.9533),
    "watsonville": (36.9102, -121.7569), "monterey": (36.6002, -121.8947),
    "carmel": (36.5552, -121.9233), "pacific grove": (36.6177, -121.9166),
    "sacramento": (38.5816, -121.4944), "davis": (38.5449, -121.7405),
    "stockton": (37.9577, -121.2908), "modesto": (37.6391, -120.9969),
    "vacaville": (38.3566, -121.9877), "fairfield": (38.2494, -122.0400),
    "bodega bay": (38.3332, -123.0480), "point reyes": (38.0697, -122.8067),
    "guerneville": (38.5016, -122.9958),
}

# Region labels for grouping in the UI.
REGIONS = {
    "Peninsula": ["menlo park", "palo alto", "east palo alto", "atherton", "redwood city",
                  "san carlos", "belmont", "san mateo", "foster city", "burlingame", "millbrae",
                  "hillsborough", "woodside", "portola valley", "half moon bay", "pacifica",
                  "south san francisco", "san bruno", "daly city", "brisbane", "stanford",
                  "la honda", "pescadero", "san gregorio", "loma mar", "moss beach",
                  "el granada", "montara"],
    "San Francisco": ["san francisco", "sf"],
    "South Bay": ["mountain view", "los altos", "sunnyvale", "santa clara", "san jose",
                  "cupertino", "campbell", "saratoga", "los gatos", "milpitas", "morgan hill",
                  "gilroy"],
    "East Bay": ["oakland", "berkeley", "emeryville", "alameda", "fremont", "hayward",
                 "san leandro", "union city", "newark", "richmond", "walnut creek", "concord",
                 "pleasanton", "livermore", "dublin", "danville", "orinda", "lafayette",
                 "san ramon", "castro valley", "martinez", "antioch",
                 "briones", "pleasant hill", "moraga", "piedmont", "albany",
                 "el cerrito"],
    "North Bay": ["sausalito", "mill valley", "san rafael", "novato", "larkspur", "corte madera",
                  "petaluma", "santa rosa", "sonoma", "napa", "st helena", "calistoga", "vallejo",
                  "benicia", "bodega bay", "point reyes", "guerneville",
                  "bolinas", "nicasio", "point reyes station", "olema",
                  "stinson beach", "fairfax", "san anselmo", "ross",
                  "kentfield", "tiburon"],
}
_CITY_TO_REGION = {c: r for r, cities in REGIONS.items() for c in cities}

# Venues whose name contains a place they are not in. coords_for_city() falls
# back to substring matching, so without this the Alameda County Fairgrounds
# geocodes to Alameda rather than Pleasanton, 25 miles away. Only worth an entry
# when the name is actively misleading, not merely unrecognised.
VENUE_CITY = {
    "alameda county fairgrounds": "pleasanton",
    "san mateo county event center": "san mateo",
    "santa clara county fairgrounds": "san jose",
    "sonoma county fairgrounds": "santa rosa",
    "marin center": "san rafael",
    "cow palace": "daly city",
    "shoreline amphitheatre": "mountain view",
    "kings mountain": "woodside",
    "stanford shopping center": "palo alto",
    "moffett field": "mountain view",
    "great america": "santa clara",
}

# A typical drive to somewhere in each region, for the case where a source names
# the region but not the town. Used for RANKING ONLY and never displayed: a
# Peninsula event with no address is genuinely close and should not be shrugged
# off with the same penalty as an event whose location we know nothing about.
# Displaying these would be claiming a precision we do not have.
REGION_DRIVE_HINT = {
    "Peninsula": 25,
    "South Bay": 30,
    "San Francisco": 50,
    "East Bay": 50,
    "North Bay": 65,
}


def normalize_city(name):
    """Lowercase and strip state suffixes and punctuation, so "San Jose, CA" matches."""
    if not name:
        return None
    s = str(name).lower().strip()
    s = re.sub(r",?\s*(ca|california)\s*\d*$", "", s)
    s = re.sub(r"[^a-z\s]", "", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s or None


def city_for_venue(name):
    """The town a well-known venue is actually in, or None."""
    c = normalize_city(name)
    if not c:
        return None
    for venue, city in VENUE_CITY.items():
        if venue in c:
            return city
    return None


def coords_for_city(name):
    """Look up a city's coordinates, tolerating "Downtown San Jose" style noise."""
    c = normalize_city(name)
    if not c:
        return None
    if c in CITY_COORDS:
        return CITY_COORDS[c]
    # A venue named after somewhere it is not must be resolved before the
    # substring pass below gets hold of it.
    venue_city = city_for_venue(c)
    if venue_city:
        return CITY_COORDS[venue_city]
    # Substring match, longest city name first so "san jose" wins over "san".
    for city in sorted(CITY_COORDS, key=len, reverse=True):
        if city in c:
            return CITY_COORDS[city]
    return None


def region_for_city(name):
    c = normalize_city(name)
    if not c:
        return "Unknown"
    if c in _CITY_TO_REGION:
        return _CITY_TO_REGION[c]
    venue_city = city_for_venue(c)
    if venue_city:
        return _CITY_TO_REGION.get(venue_city, "Farther Afield")
    for city in sorted(_CITY_TO_REGION, key=len, reverse=True):
        if city in c:
            return _CITY_TO_REGION[city]
    return "Farther Afield"


def haversine_miles(a, b):
    """Great-circle distance in miles between two (lat, lon) pairs."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def drive_minutes(origin, dest):
    """Estimate driving minutes from straight-line distance.

    Short hops are dominated by surface streets and lights, so they get a worse
    effective speed than long freeway runs. The 1.25 factor corrects straight-line
    distance to approximate road distance.
    """
    if not dest:
        return None
    miles = haversine_miles(origin, dest) * 1.25
    if miles < 5:
        mph = 20
    elif miles < 15:
        mph = 28
    elif miles < 40:
        mph = 38
    else:
        mph = 48
    return int(round(miles / mph * 60))
