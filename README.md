# Bay Area Things To Do

> A daily-refreshed dashboard of Bay Area events, ranked for a family based in
> Menlo Park with kids aged 9 to 14. Deliberately wide: sports, art, museums,
> festivals, music, culture, and adults-only things too.

The point is to stop missing events we would have enjoyed. A one-day reptile expo
in San Mateo is exactly the kind of thing no algorithm surfaces and no newsletter
mentions until it is over.

## Where this project lives

- **Local:** `~/Projects/personal/family/bay-area-events/`
- **GitHub:** `github.com/mikebailey/bay-area-events` (public)
- **Hosting:** GitHub Pages, planned at `bayarea.michaelbailey.org`
- **Mirror:** personal Google Drive, `Projects/personal/family/bay-area-events/`
  (`data/` and `outputs/` only)

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
| CuriOdyssey, Hiller, Chabot, Oakland Museum | API | All run The Events Calendar WordPress plugin, so one parser serves all four. Adding a venue is one line in `config.py`. |
| Filoli | Scrape | Was on the shared parser until they replatformed. Own module now, see below. |
| Funcheap | Scrape | Day archives. The long tail of small, local, free events. |

### Filoli replatformed, and the shared parser died with it (2026-09-07)

Filoli ran The Events Calendar like the other venues until some point before
2026-09-07, when they moved to Umbraco with Blackbaud ticketing. The whole
WordPress REST API went with it: `/wp-json/` itself 404s, not just the events
endpoint. Every daily run recorded a Filoli failure and nobody noticed, which is
exactly the failure mode the source-health footer exists to catch.

They are worth a bespoke scraper rather than dropping. Filoli is eleven minutes
away, the same pocket as the Kings Mountain Art Fair, and it runs the sort of
one-off seasonal thing (Nightfall, Holiday Lights, wreath parties) that no
ticketing feed carries.

`sources/filoli.py` scrapes `/whats-on/events/?p=N`. What matters about it:

- The listing is **server-rendered**, five per page, four pages. A page past the
  end returns valid HTML with zero listing items, which is the loop's stop
  signal.
- There is a filter form taking `period`, `from`, `to` and `category`, but
  `from`/`to` want **dd/mm/yyyy** (the site is built en-GB) and the whole
  calendar is only four pages. Fetching all of it and filtering by date in
  Python is fewer requests and one less thing to get wrong.
- The date line comes in four shapes and all of them appear in practice:
  `Sep 9th - Sep 18th 2026` (range, year only at the end), `Nov 14th 2026 - Jan
  10th 2027` (range across a year boundary), `Oct 27th 2026` (one day, no time),
  and `Sep 16th 2026: 10:30am - 11:30am More dates ...` (repeats, sometimes
  several on the same day). Ranges become one event with an end date; repeats
  become one event per day, keeping the earliest session.
- **No price anywhere in the listing.** It is on the detail page, which would
  cost one request per event. Left alone, so these arrive with price unknown
  rather than wrongly marked free.

The 23 WordPress-era rows still in the store were purged when this landed: three
of the four still inside the horizon had dead links, and a plausible event with
a dead link is worse than no event.

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
- **Funcheap tags every post with its region** (`category-peninsula`,
  `category-east-bay`, ...) and this is the most reliable location signal on the
  page — better than the title, which is where the city used to be read from.
  It was being parsed and then thrown away: regions arrived as ordinary tags,
  the tag list is sorted alphabetically and capped at six, and `peninsula`
  sorted last. Pulled out before the cap now.
- **The Funcheap meta line carries a venue** after the cost
  (`<span class="cost">Cost: FREE</span> | <span>Kings Mountain Fire
  Station</span>`), which for a lot of listings is the only location present at
  all. It used to be discarded outright.
- **Multi-day events are posted once, on their opening day**, and
  `data-event-date-end` is usually just that day's closing time. The real span
  lives in the title: "(Sept. 5-7)". Parsed as a fallback end date, because
  without it a two-day festival vanishes from the site halfway through — which
  is how the 160th Scottish Highland Gathering disappeared on the Sunday it was
  running.
- **Some venues are named after a place they are not in.** The Alameda County
  Fairgrounds is in Pleasanton, 25 miles from Alameda, and `coords_for_city`
  falls back to substring matching. `geo.VENUE_CITY` corrects the handful that
  matter; only add an entry when the name is actively misleading.
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

## Holidays: the days that actually matter

The page used to weight a day purely by where it sat in the week — Saturday
1.00, Monday 0.35. That is a fine proxy and it fails on precisely the days worth
planning around. Labor Day 2026 was a Monday, so every event on the one day the
whole family was off got divided by three and buried under a Tuesday concert.

So `code/holidays.py` replaces "is it Saturday" with "how free are we", from two
calendars:

| Calendar | Says | Source |
|---|---|---|
| MIT Institute Holidays | Mike is off | [hr.mit.edu/holidays](https://hr.mit.edu/holidays) |
| MPCSD instructional calendar | the kids are off | [district.mpcsd.org/calendar](https://district.mpcsd.org/calendar) |

**Neither one alone is the answer, and the mismatches are the point.** Patriots'
Day is an MIT holiday and an ordinary school day in California: Mike is free,
the kids are not, so it is a date-day and scores 0.55. Spring break is the
reverse and scores 0.50. Only the overlap gets a Saturday-grade 1.00.

Two smaller rules do real work. **The evening before a family day** is worth far
more than its weekday slot suggests, because what makes a Sunday night bad is
the Monday morning after it — so the Sunday of Labor Day weekend goes to 0.95.
And **a holiday never demotes a day**: the weight is a floor over the
day-of-week baseline, never a replacement, so a holiday landing on a Saturday
changes nothing.

Both calendars are transcribed rather than computed. No generic US-holiday
library knows that MIT observes Patriots' Day, and none of them knows when
MPCSD schedules its staff development days. **Refresh both once a year.**

Going stale degrades in two stages, deliberately. Past the end of the school
calendar, the holidays *every* California district closes for — Labor Day,
Thanksgiving, Christmas and the rest — are still treated as family days, so the
common cases keep working for a year; Veterans Day and Indigenous Peoples' Day
are not on that list, because MPCSD closes for them and plenty of districts do
not, and guessing wrong there is the exact error this module exists to avoid.
Past the end of the MIT calendar too, every day falls back to its plain
day-of-week weight.

The interesting failure here was the first attempt at that fallback: "outside
the published school year" was open-ended, so every date after June 2027 was
classified as summer forever. That lifted every weekday in 2028 from 0.35 to
0.42 and labelled Christmas 2028 a summer holiday. A calendar has to know the
range it covers, not just its contents.

`build.py` bakes the resulting weights into `events.json` as a `days` table and
the page reads them from there. The alternative — a second copy of the holiday
calendar in JavaScript — is the kind of duplication that agrees on the day it is
written and quietly diverges afterwards. That is not hypothetical here: the
digest was carrying its own copy of the day weights, keyed on Python's
`weekday()` while the page indexed by JavaScript's `getDay()`, so every weight
sat one day off and Saturday was being ranked at Friday's number. There is one
implementation now.

What a holiday changes, once it is known:

- **The page** gives it its own heading rather than collapsing it into
  "Weekdays", the cream weekend background, and a banner naming the long
  weekend and how many events fall on the day itself.
- **The digest** leads with "On Labor Day" as a section of its own, so the
  holiday cannot be crowded out by a Saturday that simply has more listings,
  and the subject line names it.
- **An early edition** goes out twelve days ahead (`digest.py --holiday`),
  which is the lead time the Thursday-before digest cannot give you for
  anything that sells out. It is a no-op unless a family holiday falls exactly
  twelve days out, so the daily job can just run it every day.
- **The sweep** gets a holiday-specific prompt (`sweep.py --holiday`) aimed at
  annual traditions rather than weekend roundups.

```bash
python code/holidays.py                    # print the next year of free days
python code/digest.py --holiday            # preview the early edition
python code/sweep.py --holiday --dry-run   # what the holiday sweep would add
```

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

## Running it in the cloud

`.github/workflows/daily.yml` runs at 13:00 UTC: fetch, build, commit
`site/events.json`, deploy to GitHub Pages, and on Thursdays send the digest.
`workflow_dispatch` takes a `send_digest` input for testing out of cycle.

Four repo secrets: `TICKETMASTER_API_KEY`, `GMAIL_USER`, `GMAIL_APP_PASSWORD`,
`DIGEST_TO` (comma-separated).

**Two sources block GitHub's datacenter IPs.** Chabot returns 403 and DoTheBay
returns an empty list, though both serve a home connection fine. So the cloud
build sees ~200 fewer events than a local run. `carry_over_degraded()` in
build.py keeps events alive across that gap, but only while every source that
knew about an event is degraded -- if a working source stops listing something,
it really has been cancelled and should disappear.

`data/` is cached between runs rather than committed. It is gitignored, so CI
would otherwise start empty each day and reset every `first_seen`, silently
breaking the digest's "newly found this week" section.

## On the phone

The page is installable. Open it, then **Share → Add to Home Screen**, and it
gets a bridge icon that opens without browser chrome. iOS reads the `apple-*`
tags and ignores the manifest entirely; Android reads the manifest. Both are in
`site/index.html`.

The artwork is `site/icon.svg`, drawn by hand in the site's own palette — the
ink ground and the sports orange that already carries one of the three event
types. To change it, edit the SVG and re-render:

```bash
python code/make_icons.py
```

That finds Chrome or Edge on any of the three platforms. Two things that look
like bugs and are not: each size is rendered at its own CSS size, because
scaling one 512px render down with `--force-device-scale-factor` silently
produces a blank PNG; and the page background matches the SVG ground, so a
rounding seam at the canvas edge is invisible rather than white.

## Status

**Phases 1-3 complete, plus holiday mode and the home-screen icon.** Eight
sources, roughly 2,200 events in a 120-day window. The page is a day-grouped
agenda: sticky day headers, weekend and holiday emphasis, aligned time and price
rails, colored type bars, and text badges. Filters for drive time, type, and
attributes. Installable to the phone home screen.

Live at `bayarea.michaelbailey.org` (Cloudflare CNAME -> mikebailey.github.io,
DNS-only/grey cloud -- proxying breaks GitHub's certificate validation).

Still open:
- **The Peninsula and Tri-Valley coverage gap.** There are zero Pleasanton
  events in the store at all, which is how the 160th Scottish Highland
  Gathering — 160 years old, at the Alameda County Fairgrounds, on every local
  news site over Labor Day weekend — was invisible. The holiday sweep is the
  route to this class of event; more feeds are not.
- **More museum calendars.** Exploratorium, Cal Academy, and The Tech publish no
  machine-readable feed at all; SFMoMA exposes `wp-json` exhibitions but hides
  their dates in unexposed ACF fields. All need HTML scraping.
- **Add-to-calendar links.**
- **Refresh both holiday calendars** when MIT and MPCSD publish 2027-28.
