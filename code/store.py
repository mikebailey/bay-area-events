"""SQLite event store.

Why a database rather than a folder of JSON: the pipeline has to remember what it
has already seen. That gives us three things a stateless rebuild cannot:

  1. "New since last Thursday" for the digest.
  2. Enrichment that runs once per event instead of once per day (Phase 2).
  3. Stability. If a feed goes down on Wednesday, the events it gave us on Tuesday
     stay on the page instead of silently vanishing mid-week.
"""
import json
import sqlite3
from datetime import date, timedelta

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id             TEXT PRIMARY KEY,
    title          TEXT NOT NULL,
    start_local    TEXT NOT NULL,   -- ISO 8601, local Bay Area time
    end_local      TEXT,
    all_day        INTEGER DEFAULT 0,
    venue          TEXT,
    city           TEXT,
    region         TEXT,
    lat            REAL,
    lon            REAL,
    drive_minutes  INTEGER,
    category       TEXT,
    tags           TEXT,            -- JSON array
    price_min      REAL,
    price_max      REAL,
    is_free        INTEGER DEFAULT 0,
    price_band     TEXT,
    url            TEXT,
    image          TEXT,
    description    TEXT,
    sources        TEXT,            -- JSON array of source keys
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL,
    -- Phase 2 enrichment columns, written later and null until then.
    family_fit     INTEGER,
    audience       TEXT,
    adults_only    INTEGER,
    epic           INTEGER,
    blurb          TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_start ON events(start_local);
CREATE INDEX IF NOT EXISTS idx_events_drive ON events(drive_minutes);

CREATE TABLE IF NOT EXISTS source_runs (
    run_date    TEXT NOT NULL,
    source      TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    count       INTEGER DEFAULT 0,
    seconds     REAL,
    error       TEXT,
    PRIMARY KEY (run_date, source)
);
"""

# Columns the fetcher is allowed to overwrite on every run. Enrichment columns are
# deliberately excluded: once an event is scored, a later fetch must not wipe it.
UPSERT_FIELDS = [
    "title", "start_local", "end_local", "all_day", "venue", "city", "region",
    "lat", "lon", "drive_minutes", "category", "tags", "price_min", "price_max",
    "is_free", "price_band", "url", "image", "description", "sources", "last_seen",
]


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_events(conn, events):
    """Insert new events, refresh existing ones. Returns (new_count, updated_count)."""
    today = date.today().isoformat()
    new = updated = 0
    for ev in events:
        row = dict(ev)
        row["tags"] = json.dumps(row.get("tags") or [])
        row["sources"] = json.dumps(sorted(set(row.get("sources") or [])))
        row["last_seen"] = today

        existing = conn.execute("SELECT id, sources FROM events WHERE id = ?", (row["id"],)).fetchone()
        if existing is None:
            row["first_seen"] = today
            cols = ["id", "first_seen"] + UPSERT_FIELDS
            conn.execute(
                "INSERT INTO events (%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                [row.get(c) for c in cols],
            )
            new += 1
        else:
            # Merge source lists so a dedupe match records every feed it came from.
            merged = sorted(set(json.loads(existing["sources"] or "[]")) | set(json.loads(row["sources"])))
            row["sources"] = json.dumps(merged)
            conn.execute(
                "UPDATE events SET %s WHERE id = ?" % ",".join("%s = ?" % c for c in UPSERT_FIELDS),
                [row.get(c) for c in UPSERT_FIELDS] + [row["id"]],
            )
            updated += 1
    conn.commit()
    return new, updated


def record_run(conn, source, ok, count=0, seconds=0.0, error=None):
    conn.execute(
        "INSERT OR REPLACE INTO source_runs (run_date, source, ok, count, seconds, error)"
        " VALUES (?,?,?,?,?,?)",
        (date.today().isoformat(), source, int(ok), count, round(seconds, 1), error),
    )
    conn.commit()


def latest_runs(conn):
    return conn.execute(
        "SELECT * FROM source_runs WHERE run_date = (SELECT MAX(run_date) FROM source_runs)"
        " ORDER BY source"
    ).fetchall()


def backfill_geo(conn):
    """Re-resolve rows that have a city but no coordinates.

    The feeds re-derive location on every fetch, but sweep results are written
    once and never refetched, so a row stored before its town existed in
    geo.CITY_COORDS keeps a null drive time forever. That is how the Labor Day
    sweep's Roaring Camp find sat at "location unknown" while Felton was sitting
    in the table. Running this at build time makes additions to the city table
    retroactive, which is what anyone adding one would expect.

    Returns the number of rows updated.
    """
    import geo
    from config import HOME

    rows = conn.execute(
        "SELECT id, city, venue FROM events WHERE lat IS NULL AND"
        " (city IS NOT NULL OR venue IS NOT NULL)"
    ).fetchall()

    fixed = 0
    for r in rows:
        coords = geo.coords_for_city(r["city"] or r["venue"])
        if not coords:
            continue
        region = geo.region_for_city(r["city"] or r["venue"])
        conn.execute(
            "UPDATE events SET lat=?, lon=?, drive_minutes=?, region=? WHERE id=?",
            (coords[0], coords[1], geo.drive_minutes(HOME, coords), region, r["id"]))
        fixed += 1
    if fixed:
        conn.commit()
    return fixed


def upcoming(conn, today_iso, horizon_iso, lookback_days=30):
    """Everything still worth showing: not yet over, and starting before the horizon.

    Selecting on the END date, not the start, is what keeps a multi-day festival
    on the page for its second and third days. Filtering on start_local alone
    made the Scottish Highland Games disappear on the Sunday they were running,
    because Funcheap posts a two-day event once, dated to its opening day.

    The look-back is bounded so a long-running exhibit with a stale end date
    cannot drag the whole archive forward.
    """
    floor = (date.fromisoformat(today_iso[:10]) - timedelta(days=lookback_days)).isoformat()
    return conn.execute(
        "SELECT * FROM events"
        " WHERE date(COALESCE(end_local, start_local)) >= date(?)"
        "   AND date(start_local) >= date(?)"
        "   AND date(start_local) <= date(?)"
        " ORDER BY start_local",
        (today_iso, floor, horizon_iso),
    ).fetchall()
