# Bay Area Things To Do

> A daily-refreshed dashboard of Bay Area events, ranked for a family based in
> Menlo Park with kids aged 9 to 14. Deliberately wide: sports, art, museums,
> festivals, music, culture, and adults-only things too.

The point is to stop missing events we would have enjoyed. A one-day reptile expo
in San Mateo is exactly the kind of thing no algorithm surfaces and no newsletter
mentions until it is over.

## Where this project lives

- **Local:** `~/Projects/personal/bay-area-events/`
- **GitHub:** `github.com/mikebailey/bay-area-events` (public)
- **Hosting:** GitHub Pages, planned at `bayarea.michaelbailey.org`
- **Mirror:** personal Google Drive (`data/` and `outputs/` only)

Public repo, but deliberately unlinked from michaelbailey.org in both directions,
`noindex`, and excluded from search engines. It is a listing of public events, and
it must stay that way: **do not add "we are going to this" markers**, because that
publishes the family's schedule. If that feature is ever wanted, the page moves
behind Cloudflare Access like the `travel` project.

## Running it

No dependencies. Standard library only, Python 3.9+.

```bash
python code/fetch.py            # hit every source, dedupe, write to SQLite
python code/build.py            # read SQLite, write site/events.json
cd site && python -m http.server 8765     # then open http://127.0.0.1:8765
```

Useful flags:

```bash
python code/fetch.py --dry-run          # fetch and report, write nothing
python code/fetch.py --only funcheap    # one source
python code/fetch.py --days 30          # shorter window
BAE_DEBUG=1 python code/fetch.py        # full tracebacks on source failure
```

## How it fits together

```
sources/*.py  ->  fetch.py  ->  data/events.db  ->  build.py  ->  site/events.json
                                                                        |
                                                                  site/index.html
```

Each source module implements `fetch(window_start, window_end)` and returns
normalized dicts. `fetch.py` runs each one inside a try/except, records the
outcome in the `source_runs` table, and merges duplicates. The page reads one JSON
file and does all filtering in the browser.

**Failures are visible, not silent.** Every run records per-source status and the
page footer shows "N/7 healthy". The expected failure mode for a project like this
is a scraper breaking in November and nobody noticing until February, so degraded
coverage is surfaced rather than hidden.

## Sources

| Source | Kind | Notes |
|---|---|---|
| Ticketmaster Discovery | API | Concerts, pro sports, big theater. Highest yield by far. |
| DoTheBay | JSON API | Curated aggregator. Carries its own popularity signal and a slice of Eventbrite inventory. |
| CuriOdyssey, Filoli, Hiller, Chabot, Oakland Museum | API | All run The Events Calendar WordPress plugin, so one parser serves all five. Adding a venue is one line in `config.py`. |
| Funcheap | Scrape | Day archives. The long tail of small, local, free events. |

### Quirks discovered while building (2026-08-15)

Recorded because each one cost real debugging time and none is documented anywhere.

- **Funcheap RSS is unusable.** Hard-capped at 10 items and aggressively cached.
  `?paged=N`, `/page/N/feed/`, and `?posts_per_rss=N` all return byte-identical
  content. The `/city/<region>/feed/` URLs are not region-filtered either. The day
  archives are the only real access, and they are keyed to the **event** date, so
  we make one request per day.
- **Do not scrape every event link on a Funcheap page.** The sidebar carries
  "most popular" widgets linking to events on other dates. Parsing those stamped
  unrelated events with the archive day's date and produced the same festival four
  times over. Parse `<div id="post-N">` blocks only, which also yields exact start
  and end times via `data-event-date`.
- **Ticketmaster caps paging at 1000 results per query** (`page * size <= 1000`).
  There are far more than 1000 events in a 120-day window, so the query is sliced
  into two-week chunks. Without that you silently get only the first six weeks.
- **The Events Calendar's `cost_details.values` is not reliably numeric.** Some
  venues put the string `"free"` in it, which crashes a naive `float()`.
- **Smart punctuation breaks naive filters.** Venue sites write `Today’s Schedule`
  with U+2019, so a regex using an ASCII apostrophe matches nothing and 114
  operating-hours rows sail through. `base.normalize_punct` handles this.
- **DoTheBay 403s any bot User-Agent.** It looked unavailable until retried with
  a browser UA, after which `/events.json?page=N` serves clean paged JSON. Its
  `begin_time` field is not a clock string despite the name, it is a full ISO
  datetime, and its offsets are `-05:00` rather than Pacific; the `tz_adjusted_*`
  fields carry the correct time. Venue name lives under `venue.title`, not `name`.
- **Funcheap sells sponsored posts that sit in the listing like events**
  ("$35 for Locally-Run Independent Internet Service"). Filtered on the
  `sponsored` category and deal-shaped titles.
- **Eventbrite has had no public event-search API since 2020** and blocks
  scraping. DoTheBay is the legitimate route to part of that inventory.
- **The SF Peninsula tourism board API is closed.** It runs Simpleview, whose
  `rest_v2` endpoints return 403 even with a browser UA and a matching Referer.
  Would need HTML scraping.
- **Windows console encoding.** Event titles are full of `™`, `’`, and `–`. Run
  with `PYTHONIOENCODING=utf-8` on the PC or printing crashes. All file writes
  specify `encoding="utf-8"` explicitly for the same reason.

## The color system, and why there are only three

Event **type** carries color; crosscutting **attributes** carry text badges. That
split is forced by measurement rather than taste. Running candidate palettes
through the colorblind validator:

| Palette | Result |
|---|---|
| 6 category colors | FAIL — green vs orange at deltaE 3.2 under protanopia, effectively identical |
| 5 category colors | FAIL — normal-vision floor, magenta vs orange at 12.9 (needs 15) |
| **3 + neutral** | **PASS all checks, both light and dark mode** |

So type is a closed set of four (music blue, arts aqua, sports orange, everything
else neutral) and anything crosscutting is a badge: family, festival, free,
adults, outdoor, top pick, sold out.

It is also the better model. "Family-friendly" is not a peer of "music", it is a
property a concert can have. **Do not add a fourth hue without re-running
`validate_palette.js`.** Every row also names its type in text, so color is never
the sole encoding.

## Ranking: local model, no API

**There is no paid API anywhere in this project.** The only credential is the free
Ticketmaster key. Scoring runs on a local model; the one job that genuinely needs
frontier reasoning runs through the Claude Code subscription.

| Job | Volume | Runs on |
|---|---|---|
| Scoring events (family fit, audience, badges, blurb) | ~100/day | **Ollama**, `qwen3:30b-a3b`, on the 4090 |
| Weekly editorial sweep (SFGate / Chronicle / TimeOut roundups) | ~1/week | **`claude -p`** headless, uses the subscription |
| Dedupe, drive times, sectioning | everything | plain Python |

Scoring is high-volume but low-judgment: a fixed rubric applied to short text with
structured output. That is what a local model is good at. The editorial sweep is
the opposite, small volume but needing live web search and real judgment, so it
goes to Claude.

**Two things make the local model reliable enough.** Ollama's `format` parameter
takes a JSON schema and constrains generation to it, which is the difference
between a clean pipeline and one that spends its time repairing malformed JSON.
And batching twelve events per call amortizes the rubric, which otherwise
dominates prompt size and wall-clock time.

Where no score exists yet, the page falls back to a **transparent keyword
heuristic** (drive time, free vs paid, kid-relevant words, festival scale, how
many sources listed it). Its inputs are preserved per event in the `why` field.

Descriptions are deliberately **not** republished verbatim from sources; the
`blurb` is the model's own one-sentence summary.

## How the two halves stay in sync

The daily job runs in GitHub Actions, which has no GPU and no Claude session. So
the work is split by what needs a model:

```
GitHub Actions (daily)     fetch -> dedupe -> build -> deploy      no model
This machine (when on)     score unscored events -> cache/scores.json -> commit
```

`cache/scores.json` is **tracked in git**, keyed by event ID. Actions reads it at
build time, uses the model's score where one exists, and falls back to the
heuristic where it doesn't. An event found today shows a heuristic score until the
next local run upgrades it.

That also resolves the earlier open question about `data/events.db`: the state
that actually has to survive between runs is a small text file that diffs
cleanly, not a gitignored binary. The database stays local and disposable.

Missing a day of scoring has no consequence here, which is what makes this trade
the right one.

```bash
python code/enrich.py               # score everything unscored
python code/enrich.py --limit 50    # small trial batch
python code/enrich.py --rescore     # discard cached scores and redo
```

## Status

**Phase 1 complete, plus the agenda redesign.** Eight sources, roughly 2,250
events in a 120-day window. The page is a day-grouped agenda: sticky day headers,
a scrollable date strip showing per-day counts, weekend emphasis, aligned time and
price rails, colored type bars, and text badges. Filters for drive time, type, and
attributes.

Still open:
- **AI sweep** for editorial listicles (SFGate, Chronicle, TimeOut weekend
  roundups). This is the real gap against a Google "what's on this weekend"
  search, since no feed carries that content. Blocked on an Anthropic API key.
- **More museum calendars.** Exploratorium, Cal Academy, and The Tech publish no
  machine-readable feed at all; SFMoMA exposes `wp-json` exhibitions but hides
  their dates in unexposed ACF fields. All need HTML scraping.
- **Phase 3**: Thursday digest email, add-to-calendar links, DNS, Actions cron.
