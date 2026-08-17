"""Score events with a local model. No API, no key, no per-event cost.

Runs against Ollama on this machine. The daily GitHub Actions job does NOT run
this: a cloud runner has no GPU and no Claude session, so it builds the page with
the heuristic score from build.py instead. Whenever this machine is on, this
script upgrades whatever is unscored and writes the result to a small tracked
cache file that Actions reads on its next build.

That split is deliberate. Missing a day of scoring costs nothing here — the page
still lists every event, just ranked by keyword heuristics until the real score
lands.

Two things make a local model reliable enough for this:

  1. `format` takes a JSON schema, so Ollama constrains generation to a valid
     shape. Without it, a meaningful share of responses need repair.
  2. Batching. The rubric is the expensive part of each prompt, so sending 12
     events per call amortizes it and cuts wall-clock time several-fold.

    python code/enrich.py                # score everything unscored
    python code/enrich.py --limit 50     # try a small batch first
    python code/enrich.py --rescore      # throw away cached scores and redo
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import CACHE_PATH, OLLAMA_MODEL, OLLAMA_URL
import store

BATCH_SIZE = 12
REQUEST_TIMEOUT = 600     # a cold model load on first call is slow

# The rubric lives here rather than in code so it can be tuned without touching
# the pipeline. Everything about the family's situation is stated explicitly;
# the model has no other context.
RUBRIC = """\
You are scoring Bay Area events for one specific family so they can decide what \
to do this weekend. Score each event on its own merits.

THE FAMILY
- Two adults and kids aged 9 to 14. Based in Menlo Park on the Peninsula.
- The kids are past the toddler stage entirely. Anything pitched at babies, \
toddlers, or preschoolers is a poor fit no matter how well run it is.
- Interests run wide: science and museums, sports, art, music, festivals, food, \
the outdoors, animals, technology, making things.
- The adults also go out without the kids. An adults-only concert or a bar event \
is a perfectly good event; it is simply not a family one.

FIELDS

TWO SEPARATE SCORES. Every event gets both, and they are independent. A late \
comedy show can be family_fit 5 and adult_interest 85. A children's museum \
morning can be family_fit 80 and adult_interest 10. A great street festival can \
be high on both. Never let one score drag the other along.

family_fit: 0-100. How much would THIS family enjoy going together, with the kids?
  80-100  Strongly appealing to kids 9-14 and worth planning around.
  60-79   Solid family outing.
  40-59   Fine, unremarkable, would do it if nearby and free.
  20-39   Weak fit. Aimed at much younger children, or dull for kids.
  0-19    Not a family event at all (21+, adults-only, or purely grown-up interest).
  Judge the event, not its price. Free does not mean good.

FUN COUNTS AS MUCH AS EDUCATION. This age group likes loud, spectacular, \
silly things: monster trucks, motorsports, arcades, laser tag, comic and anime \
conventions, video games, mini golf, water parks, haunted houses, eating \
competitions. Do not score an event down for being unserious, and do not \
reserve high scores for museums and science. A demolition derby can outrank a \
lecture.

adult_interest: 0-100. How much would the TWO ADULTS enjoy this on a night out \
on their own, with the kids at home? Judge it as a date or an evening with \
friends, independent of family_fit.
  80-100  They would book a sitter for this: a great band at a real venue, a \
strong comedy bill, a notable food or drink event, an opening worth showing up to.
  60-79   A good night out.
  40-59   Pleasant but unremarkable.
  20-39   Little adult appeal on its own.
  0-19    Squarely a children's activity, or nothing an adult would choose \
without a kid in tow.
  Daytime does not disqualify an event, but evening events, ticketed music, \
comedy, theater, food and drink, and 21+ venues are where the high scores live.

SCALE AND VARIETY RAISE A SCORE. An event where there is a lot going on in one place -- a big street festival or parade with food stalls, music stages, performances, vendors and crowds -- is worth more of a family's day than a single scheduled activity, because everyone finds something and you can stay as long as you like. Judge how much there is to do once you arrive, not just the headline.

RECURRING PROMOTIONS ARE NOT OCCASIONS. Some listings are a franchise or brand running the same promotion on a schedule, in many cities at once: mobile-game community days, chain-store events, weekly club nights, standing bar trivia. They can be perfectly pleasant, but they are not a reason to plan a weekend around, because the same thing runs again next month somewhere nearby. Score these in the middle at best -- never above 70 -- and reserve high scores for things happening once.

audience: exactly one of "kids", "teens", "adults", "all".

adults_only: this means a REAL AGE RESTRICTION — the venue or event bars \
minors. Think 21+ bars, nightclubs, wine and beer tastings, casinos, \
late-night shows.
  It is NOT a judgment about subject matter. Public civic events are open to \
everyone and families attend them in large numbers: parades of every kind \
(including Pride parades and drag parades), street festivals, protests and \
marches, cultural and heritage celebrations, and free public gatherings are \
`adults_only: false` and should be scored on how much the family would enjoy \
going, like any other event.
  If you are unsure whether minors are barred, the answer is false.

SCORING PARADES AND STREET FESTIVALS CONSISTENTLY. Judge these on what the day \
would actually be like: the scale of the event, and whether there are \
surrounding activities — food, music, stalls, performances, things to watch. A \
big parade with a festival attached beats a small one with nothing around it. \
What the event celebrates — a nation, a harvest, a holiday, a community, a \
cause — must not change the score. Two comparable parades get comparable \
scores.

epic: true ONLY for the rare thing worth building a whole day around and \
driving over an hour for — a major annual festival, a large fair, a \
once-a-year spectacle. A good local street fair is NOT epic. Be strict; \
fewer than one in twenty events should qualify.

blurb: one plain sentence, at most 20 words, in your own words, saying what \
the event actually is. Do not copy the title back. Do not use marketing \
language. If the title is all you have and it is opaque, say so plainly.

Return one object per event, in the same order, with the same id."""

SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "family_fit": {"type": "integer", "minimum": 0, "maximum": 100},
                    "adult_interest": {"type": "integer", "minimum": 0, "maximum": 100},
                    "audience": {"type": "string", "enum": ["kids", "teens", "adults", "all"]},
                    "adults_only": {"type": "boolean"},
                    "epic": {"type": "boolean"},
                    "blurb": {"type": "string"},
                },
                "required": ["id", "family_fit", "adult_interest", "audience",
                             "adults_only", "epic", "blurb"],
            },
        }
    },
    "required": ["events"],
}


def load_cache():
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Sorted keys so the committed file has a stable diff between runs.
    CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )


def describe(ev):
    """The compact view of an event the model scores. Keep this tight — it is
    repeated for every event in the batch and dominates prompt size."""
    bits = ["id: %s" % ev["id"], "title: %s" % ev["title"]]
    where = ", ".join(x for x in (ev["venue"], ev["city"]) if x)
    if where:
        bits.append("where: %s" % where)
    if ev["drive_minutes"] is not None:
        bits.append("drive: %d min" % ev["drive_minutes"])
    if ev["price_band"] and ev["price_band"] != "?":
        bits.append("price: %s" % ev["price_band"])
    tags = json.loads(ev["tags"] or "[]")
    if tags:
        bits.append("tags: %s" % ", ".join(tags[:4]))
    if ev["description"]:
        bits.append("about: %s" % ev["description"][:180])
    return " | ".join(bits)


def score_batch(events, model):
    """One Ollama call for a batch. Raises on transport or parse failure."""
    listing = "\n".join("- " + describe(e) for e in events)
    payload = {
        "model": model,
        "prompt": "%s\n\nEVENTS TO SCORE (%d):\n%s" % (RUBRIC, len(events), listing),
        "stream": False,
        "format": SCHEMA,          # constrains generation to valid JSON
        # Qwen3 is a reasoning model, and with thinking left on Ollama routes the
        # ENTIRE output into the `thinking` field and returns `response` as an
        # empty string — every batch then fails to parse. Thinking buys nothing
        # for rubric-based classification, so turn it off.
        "think": False,
        "options": {
            "temperature": 0.2,    # classification, not creative writing
            "num_ctx": 8192,
        },
    }
    req = urllib.request.Request(
        OLLAMA_URL + "/api/generate",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        body = json.load(r)

    # Belt and braces: if a future model or Ollama version puts the payload back
    # in `thinking`, use it rather than failing the batch.
    raw = (body.get("response") or "").strip() or (body.get("thinking") or "").strip()
    parsed = json.loads(raw)
    by_id = {}
    for row in parsed.get("events", []):
        rid = str(row.get("id", "")).strip()
        if rid:
            by_id[rid] = row
    return by_id


def clean(row):
    """Coerce one model row into the shape the page expects."""
    fit = row.get("family_fit")
    try:
        fit = max(0, min(100, int(fit)))
    except (TypeError, ValueError):
        return None
    blurb = (row.get("blurb") or "").strip()
    if len(blurb) > 160:
        blurb = blurb[:157].rsplit(" ", 1)[0] + "…"
    audience = row.get("audience")
    if audience not in ("kids", "teens", "adults", "all"):
        audience = "all"
    adult = row.get("adult_interest")
    try:
        adult = max(0, min(100, int(adult)))
    except (TypeError, ValueError):
        adult = None      # missing is recoverable; build.py falls back

    return {
        "family_fit": fit,
        "adult_interest": adult,
        "audience": audience,
        "adults_only": bool(row.get("adults_only")),
        "epic": bool(row.get("epic")),
        "blurb": blurb,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, help="only score this many events")
    ap.add_argument("--rescore", action="store_true", help="ignore the existing cache")
    ap.add_argument("--model", default=OLLAMA_MODEL)
    ap.add_argument("--batch", type=int, default=BATCH_SIZE)
    args = ap.parse_args()

    conn = store.connect()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM events WHERE date(start_local) >= date('now') ORDER BY start_local"
    ).fetchall()]

    cache = {} if args.rescore else load_cache()
    todo = [r for r in rows if r["id"] not in cache]
    if args.limit:
        todo = todo[:args.limit]

    print("%d upcoming events, %d already scored, %d to score"
          % (len(rows), len(rows) - len([r for r in rows if r["id"] not in cache]), len(todo)))
    if not todo:
        print("nothing to do")
        return

    t0 = time.time()
    done = failed = 0
    for i in range(0, len(todo), args.batch):
        batch = todo[i:i + args.batch]
        try:
            scored = score_batch(batch, args.model)
        except (urllib.error.URLError, TimeoutError) as e:
            print("  batch %d: transport error, stopping — %s" % (i // args.batch, e))
            break
        except (json.JSONDecodeError, KeyError) as e:
            print("  batch %d: unparseable response, skipping — %s" % (i // args.batch, e))
            failed += len(batch)
            continue

        for ev in batch:
            row = scored.get(ev["id"])
            cleaned = clean(row) if row else None
            if cleaned:
                cache[ev["id"]] = cleaned
                done += 1
            else:
                # The model dropped or mangled this one; leave it unscored so a
                # later run retries rather than caching a bad value.
                failed += 1

        save_cache(cache)   # checkpoint every batch; interrupting is safe
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed else 0
        remaining = (len(todo) - (i + len(batch))) / rate if rate else 0
        print("  %4d/%d scored  %5.1f/min  ~%.0f min left"
              % (done, len(todo), rate * 60, remaining / 60))

    print("\nDone: %d scored, %d failed, %.1f min total"
          % (done, failed, (time.time() - t0) / 60))
    print("Cache: %s (%d events)" % (CACHE_PATH, len(cache)))


if __name__ == "__main__":
    main()
