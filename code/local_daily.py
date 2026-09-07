"""The half of the pipeline that needs this machine, on a schedule.

Two jobs cannot run in GitHub Actions: scoring wants the GPU, and the editorial
sweep wants a Claude session. Both were being run by hand, which meant they were
run when someone remembered, which for the sweep meant almost never.

This is the unattended version. It follows the split the project already
documents:

    GitHub Actions (daily)   fetch -> build -> deploy          no model
    this machine (when on)   sweep + score -> cache/*.json     model work

**It commits the cache files and nothing else.** site/events.json is derived,
Actions rebuilds and commits it every day, and having both ends write the same
generated file is how you get a rebase conflict at 6am with nobody watching.
Committing only the inputs makes the two halves incapable of fighting.

Self-gating in the same style as `digest.py --holiday`: run it every day and let
it decide what is due. Nothing to remember, nothing to reschedule per holiday.

    python code/local_daily.py              # do whatever is due today
    python code/local_daily.py --dry-run    # say what it would do, change nothing
    python code/local_daily.py --force-sweep
"""
import argparse
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import CACHE_PATH, ROOT, SWEEP_PATH
import holidays

LOG_PATH = ROOT / "outputs" / "local-daily.log"

# How far ahead to sweep a holiday. The early digest goes out at twelve days
# (digest.py --lead-days), so the sweep has to land before that or the email it
# is meant to fill goes out empty. The window is a range rather than a single
# day so one missed run -- a laptop shut, a reboot -- does not skip the holiday
# altogether.
HOLIDAY_SWEEP_FROM = 13
HOLIDAY_SWEEP_TO = 17

WEEKLY_SWEEP_DAY = 6        # Sunday, in Python's weekday()


def log(msg):
    stamp = "%s  %s" % (date.today().isoformat(), msg)
    print(stamp, flush=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(stamp + "\n")


def run(args, label, timeout=3600):
    """Run one pipeline step. A failure is logged and survived, never raised.

    Any single step can fail for reasons that have nothing to do with the
    others: Ollama not started, no network, Claude not logged in. Losing a day
    of scoring costs nothing, so the right response is to record it and carry
    on with whatever else can still be done.
    """
    log("  -> %s" % label)
    try:
        proc = subprocess.run([sys.executable] + args, cwd=str(ROOT),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        log("     TIMEOUT after %ds" % timeout)
        return False
    tail = [l for l in (proc.stdout or "").strip().splitlines() if l.strip()][-3:]
    for line in tail:
        log("     %s" % line[:160])
    if proc.returncode != 0:
        log("     FAILED rc=%d: %s" % (proc.returncode, (proc.stderr or "")[-300:].strip()))
        return False
    return True


def git(*args, check=False):
    proc = subprocess.run(["git"] + list(args), cwd=str(ROOT),
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if check and proc.returncode != 0:
        log("     git %s failed: %s" % (" ".join(args), (proc.stderr or "").strip()[:200]))
    return proc


def already_swept(span):
    """Has this holiday's span already been swept?

    Answered from cache/sweep.json rather than a separate state file, so there
    is nothing extra to keep in sync and clearing the cache correctly means
    "sweep it again".

    Without this the run fires on every day of the window rather than once:
    five Claude sweeps per holiday, more than fifty a year, each re-finding the
    same events.
    """
    first, last = span[0].isoformat(), span[1].isoformat()
    try:
        import sweep
        return any(first <= str(r.get("date") or "")[:10] <= last
                   for r in sweep.load_cached())
    except Exception:
        return False        # unreadable cache: sweeping again is the safe error


def sweep_due(today):
    """(should_run, args, why). Holiday sweeps outrank the weekly one.

    The window is a range rather than a single day so one missed run -- a shut
    laptop, a reboot -- does not skip the holiday altogether. It fires once
    inside that range and then stands down.
    """
    for days in range(HOLIDAY_SWEEP_FROM, HOLIDAY_SWEEP_TO + 1):
        target = today + timedelta(days=days)
        if not holidays.is_family_day(target):
            continue
        name = holidays.status(target)["name"] or "holiday"
        span = holidays.span_for(target) or (target, target)
        if already_swept(span):
            return False, None, "%s already swept" % name
        # --within-days has to reach the holiday for sweep.py to find it.
        return True, ["code/sweep.py", "--holiday", "--within-days", str(days + 1)], \
               "%s is %d days out" % (name, days)
    if today.weekday() == WEEKLY_SWEEP_DAY:
        return True, ["code/sweep.py"], "weekly editorial sweep"
    return False, None, "no sweep due"


def publish(dry_run):
    """Commit the cache files and push, rebasing onto whatever Actions did.

    Only cache/ is touched. If the rebase hits a conflict the run stops and says
    so rather than guessing: cache/scores.json is a merge of two machines'
    judgments and resolving it blindly could throw one machine's work away.
    """
    paths = [str(p.relative_to(ROOT)) for p in (CACHE_PATH, SWEEP_PATH) if p.exists()]
    if not paths:
        return
    if not git("diff", "--quiet", "--", *paths).returncode:
        log("  cache unchanged; nothing to publish")
        return
    if dry_run:
        log("  would commit and push: %s" % ", ".join(paths))
        return

    git("add", *paths)
    msg = "Local model pass: %s" % date.today().isoformat()
    if git("commit", "-m", msg, check=True).returncode:
        return
    log("  committed %s" % ", ".join(paths))

    # Actions commits site/events.json most days, so we are usually behind.
    pull = git("pull", "--rebase", "origin", "main")
    if pull.returncode:
        git("rebase", "--abort")
        log("  PUSH SKIPPED: rebase conflicted, resolve by hand. Commit is local.")
        return
    if git("push", "origin", "main", check=True).returncode == 0:
        log("  pushed")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-sweep", action="store_true",
                    help="sweep regardless of what the calendar says is due")
    args = ap.parse_args()

    today = date.today()
    log("=== local daily run ===")

    due, sweep_args, why = sweep_due(today)
    if args.force_sweep and not due:
        due, sweep_args, why = True, ["code/sweep.py"], "forced"
    log("sweep: %s (%s)" % ("yes" if due else "no", why))

    if args.dry_run:
        log("dry run: would fetch, score, and publish the cache")
        publish(dry_run=True)
        return

    # Sweep first so its finds are in the store before scoring runs, otherwise
    # they wait a whole day for a score and show a heuristic in the meantime.
    if due:
        run(sweep_args, "sweep", timeout=1800)

    run(["code/fetch.py"], "fetch", timeout=1800)
    run(["code/enrich.py"], "score", timeout=3600)
    publish(dry_run=False)
    log("done")


if __name__ == "__main__":
    main()
