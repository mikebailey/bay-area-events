"""The Thursday digest: one email, the weekend in front of you.

Deliberately short. A digest that runs to sixty events gets skimmed once and
ignored forever, so this carries the handful worth acting on and links back to
the page for everything else.

Sending uses Gmail SMTP with an app password, so there is no API and no third
party. Locally the password comes from .env; in GitHub Actions it comes from a
repo secret of the same name.

    python code/digest.py                  # write a preview, send nothing
    python code/digest.py --open           # preview and open it in a browser
    python code/digest.py --send           # actually send it
    python code/digest.py --send --to me@example.com
"""
import argparse
import html
import json
import os
import smtplib
import ssl
import sys
import webbrowser
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import holidays
from config import HOME_LABEL, ROOT, SITE
from fetch import load_env

SITE_URL = "https://bayarea.michaelbailey.org"
PREVIEW_PATH = ROOT / "outputs" / "digest-preview.html"


def day_weight(ev):
    """Straight from holidays.py, which is also what build.py bakes into the
    page, so the email and the site rank days identically.

    This used to be a local copy of the page's table, and it was quietly wrong:
    the page indexes by JavaScript's getDay() (Sunday 0) and the copy was keyed
    on Python's weekday() (Monday 0), so every weight sat one day off. Saturday
    was being ranked at Friday's 0.70 and Friday at Thursday's 0.45. Importing
    the one implementation is the fix, and the reason there is now only one.
    """
    return holidays.day_weight(ev["start"])


def proximity(ev):
    dr = ev.get("drive")
    if dr is None:
        return 0.9
    return 1.30 if dr <= 20 else 1.10 if dr <= 35 else 1.00 if dr <= 60 else 0.85


def novelty(ev):
    n = ev.get("seriesSize") or 1
    return 0.72 if n >= 8 else 0.86 if n >= 4 else 0.94 if n >= 3 else 1.0


def rank(ev):
    return ev["score"] * day_weight(ev) * proximity(ev) * novelty(ev)


def load_events():
    data = json.loads((SITE / "events.json").read_text(encoding="utf-8"))
    return data, data["events"]


def upcoming(events, days=9):
    """Events between now and roughly the end of next weekend."""
    today = date.today()
    end = today + timedelta(days=days)
    out = []
    for e in events:
        try:
            d = datetime.fromisoformat(e["start"]).date()
        except ValueError:
            continue
        if today <= d <= end:
            out.append(e)
    return out


def pick(events, n, key=rank, where=None, exclude=()):
    seen = {e["id"] for e in exclude}
    pool = [e for e in events if e["id"] not in seen and (where is None or where(e))]
    return sorted(pool, key=key, reverse=True)[:n]


def fmt_when(ev):
    d = datetime.fromisoformat(ev["start"])
    day = d.strftime("%a %b %-d") if os.name != "nt" else d.strftime("%a %b %d").replace(" 0", " ")
    return day if ev["allDay"] else "%s, %s" % (day, d.strftime("%-I:%M%p").lower()
                                                if os.name != "nt"
                                                else d.strftime("%I:%M%p").lstrip("0").lower())


def fmt_where(ev):
    bits = [b for b in (ev.get("venue"), ev.get("city")) if b]
    where = ", ".join(bits) if bits else "location not listed"
    if ev.get("drive") is not None:
        where += " · %d min" % ev["drive"]
    if ev.get("free"):
        where += " · free"
    elif ev.get("price") and ev["price"] != "?":
        where += " · " + ev["price"]
    return where


def shorten(text, limit):
    """Trim a title for the subject line at a word boundary.

    A hard slice cut "... in the Redwoods (Sept. 5-7)" to "... (Sept", which
    reads as a broken template rather than a long title.
    """
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,-–—(\"'")
    return (cut or text[:limit].rstrip()) + "…"


def on_day(events, day):
    return [e for e in events if e["start"][:10] == day.isoformat()]


def in_span(events, first, last):
    return [e for e in events
            if first.isoformat() <= e["start"][:10] <= last.isoformat()]


def build_sections(events, days=9, focus=None):
    """The digest is four short lists, not one long one.

    When a family holiday falls inside the window the shape changes: the lead
    section becomes the long weekend rather than just Saturday and Sunday, and
    the holiday itself gets a section of its own. Labor Day is why. It landed on
    a Monday, so it sat outside the weekend filter entirely and the one day the
    whole family was off never appeared in the email at all.

    `focus` narrows the whole digest to one (first, last) span. The early
    holiday edition goes out a fortnight ahead, so without it every section
    fills up with this coming Saturday -- perfectly good events, and not what an
    email about Thanksgiving is for.
    """
    today = date.today()
    if focus:
        week = in_span(events, *focus)
        # Look for the holiday inside the span, not from today. Searching from
        # today finds whichever holiday is soonest, which on a holiday is the
        # one you are currently standing in rather than the one being previewed.
        search_from, horizon = focus
    else:
        week = upcoming(events, days=days)
        search_from, horizon = today, today + timedelta(days=days)

    holiday = next(iter(holidays.family_days_between(search_from, horizon)), None)

    if holiday:
        hday, hname = holiday
        span = holidays.span_for(hday)
        first, last = span if span else (hday, hday)
        first = max(first, today)          # a span that already started is history

        # The holiday itself first and on its own, so it cannot be crowded out
        # by a Saturday that simply has more listings.
        holiday_evs = pick(on_day(week, hday), 5)

        # The rest of the long weekend, when there is one left. Once the weekend
        # has been and gone -- reading this on the holiday itself -- repeating
        # the same day under a second heading says nothing, so the section
        # widens instead: to the week in the ordinary digest, or to more of the
        # same day in a holiday edition that is only ever about that one day.
        if first != last:
            lead_title = "%s weekend" % hname
            lead_pool = in_span(week, first, last)
        else:
            lead_title = "More on the day" if focus else "Also this week"
            lead_pool = week

        top = pick(lead_pool, 6, exclude=holiday_evs)
        sections = [("On %s" % hname, holiday_evs), (lead_title, top)]
        claimed = holiday_evs + top
    else:
        weekend = [e for e in week
                   if datetime.fromisoformat(e["start"]).date().weekday() in (5, 6)]
        top = pick(weekend or week, 6)
        sections = [("This weekend", top)]
        claimed = top

    backyard = pick(week, 4,
                    where=lambda e: e.get("drive") is not None and e["drive"] <= 20,
                    exclude=claimed)
    adults = pick(week, 3, key=lambda e: e.get("adultScore") or 0,
                  where=lambda e: e.get("dateNight"), exclude=claimed + backyard)
    tail = [("In your backyard", backyard), ("For the two of you", adults)]

    # Anything the pipeline only discovered in the last week is worth calling
    # out: it is precisely what you would not have seen otherwise. Meaningless
    # in the holiday edition, which is about one weekend rather than about what
    # has changed since the last email.
    if not focus:
        cutoff = (date.today() - timedelta(days=7)).isoformat()
        fresh = pick(week, 4,
                     where=lambda e: (e.get("firstSeen") or "") >= cutoff and e["score"] >= 60,
                     exclude=claimed + backyard + adults)
        tail.append(("Newly found this week", fresh))

    return sections + tail


def render_html(sections, meta):
    def esc(s):
        return html.escape(str(s or ""))

    blocks = []
    for title, evs in sections:
        if not evs:
            continue
        rows = []
        for e in evs:
            blurb = ("<div style='color:#555;font-size:13px;margin-top:2px'>%s</div>"
                     % esc(e["blurb"])) if e.get("blurb") else ""
            rows.append(
                "<tr><td style='padding:9px 0;border-bottom:1px solid #eee'>"
                "<div style='font-size:12px;color:#2f6f4f;font-weight:700'>%s</div>"
                "<a href='%s' style='color:#191918;text-decoration:none;font-weight:600;font-size:15px'>%s</a>"
                "<div style='color:#777;font-size:13px;margin-top:1px'>%s</div>%s"
                "</td></tr>"
                % (esc(fmt_when(e)), esc(e["url"]), esc(e["title"]), esc(fmt_where(e)), blurb))
        blocks.append(
            "<h2 style='font-size:12px;text-transform:uppercase;letter-spacing:.09em;"
            "color:#999;margin:26px 0 4px'>%s</h2>"
            "<table width='100%%' cellpadding='0' cellspacing='0'>%s</table>"
            % (esc(title), "".join(rows)))

    return (
        "<html><body style='margin:0;background:#f7f7f5'>"
        "<div style='max-width:600px;margin:0 auto;padding:24px 18px;"
        "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#191918'>"
        "<h1 style='font-size:21px;margin:0 0 2px'>%s</h1>"
        "<div style='color:#777;font-size:13px'>from %s · %s</div>"
        "%s"
        "<div style='margin-top:30px;padding-top:14px;border-top:1px solid #ddd;"
        "color:#999;font-size:12px'>"
        "<a href='%s' style='color:#2f6f4f'>See everything on the site</a> · "
        "%d events tracked · drive times are estimates"
        "</div></div></body></html>"
        % (esc(meta["heading"]), esc(HOME_LABEL), esc(date.today().strftime("%B %d, %Y")),
           "".join(blocks), SITE_URL, meta["total"]))


def render_text(sections, meta):
    lines = [meta["heading"], "from %s" % HOME_LABEL, ""]
    for title, evs in sections:
        if not evs:
            continue
        lines.append(title.upper())
        for e in evs:
            lines.append("  %s" % fmt_when(e))
            lines.append("  %s" % e["title"])
            lines.append("  %s" % fmt_where(e))
            if e.get("blurb"):
                lines.append("  %s" % e["blurb"])
            lines.append("  %s" % e["url"])
            lines.append("")
        lines.append("")
    lines.append("Everything: %s" % SITE_URL)
    lines.append("%d events tracked. Drive times are estimates." % meta["total"])
    return "\n".join(lines)


def recipients(override=None):
    """Who gets it: --to wins, then DIGEST_TO, then the sending account.

    DIGEST_TO takes a comma-separated list, so the digest can go to both of us
    without maintaining a second copy of anything.
    """
    raw = override or os.environ.get("DIGEST_TO") or os.environ.get("GMAIL_USER") or ""
    addrs = [a.strip() for a in raw.split(",") if a.strip()]
    bad = [a for a in addrs if "@" not in a or a.startswith("@") or a.endswith("@")]
    if bad:
        raise SystemExit("These do not look like email addresses: %s" % ", ".join(bad))
    if not addrs:
        raise SystemExit("No recipients. Set DIGEST_TO in .env or pass --to.")
    return addrs


def send(subject, html_body, text_body, to_addr):
    user = os.environ.get("GMAIL_USER")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not password:
        raise SystemExit(
            "Missing GMAIL_USER and/or GMAIL_APP_PASSWORD.\n"
            "Add them to .env locally, or as repo secrets in GitHub Actions.\n"
            "The app password is generated at https://myaccount.google.com/apppasswords\n"
            "(requires 2FA on the account; it is 16 characters, spaces optional).")

    to = recipients(to_addr)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(to)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as s:
        s.starttls(context=ssl.create_default_context())
        # Gmail shows app passwords in groups of four; the spaces are cosmetic.
        s.login(user, password.replace(" ", ""))
        s.send_message(msg)
    return to


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="actually send the email")
    ap.add_argument("--to", help="override the recipient")
    ap.add_argument("--open", action="store_true", help="open the preview in a browser")
    ap.add_argument("--holiday", action="store_true",
                    help="early holiday edition; does nothing unless a family holiday "
                         "is exactly --lead-days away, so it can be run daily")
    ap.add_argument("--lead-days", type=int, default=12,
                    help="how far ahead the holiday edition goes out (default 12)")
    args = ap.parse_args()

    load_env()
    data, events = load_events()

    today = date.today()
    window = 9
    focus = None
    heading = "Bay Area, the week ahead"
    label = "This weekend"

    if args.holiday:
        # Fires on exactly one day per holiday, which is what lets the daily job
        # call this unconditionally without sending the same email a fortnight
        # running. Far enough out that tickets are still buyable.
        target = today + timedelta(days=args.lead_days)
        if not holidays.is_family_day(target):
            print("No family holiday on %s (%d days out). Nothing to send."
                  % (target, args.lead_days))
            return
        name = holidays.status(target)["name"] or "the holiday"
        span = holidays.span_for(target) or (target, target)
        focus = span
        heading = "%s is coming up" % name
        label = name
        print("Holiday edition: %s, %s to %s, %d days out"
              % (name, span[0], span[1], args.lead_days))

    sections = build_sections(events, days=window, focus=focus)
    meta = {"total": len(events), "heading": heading}

    kept = sum(len(v) for _, v in sections)
    if not kept:
        print("Nothing to send: no events in the next %d days." % window)
        return

    # Without a holiday, the lead section is still "This weekend". With one, the
    # subject should say which holiday, since that is the reason to open it.
    if not args.holiday:
        upcoming_holiday = next(
            iter(holidays.family_days_between(today, today + timedelta(days=window))), None)
        if upcoming_holiday:
            label = upcoming_holiday[1]

    html_body = render_html(sections, meta)
    text_body = render_text(sections, meta)
    top = next((v for t, v in sections if v), [])
    lead = shorten(top[0]["title"], 52) if top else "the week ahead"
    subject = "%s: %s%s" % (
        label, lead, " and %d more" % (kept - 1) if kept > 1 else "")

    PREVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREVIEW_PATH.write_text(html_body, encoding="utf-8")

    for title, evs in sections:
        print("%-22s %d" % (title, len(evs)))
        for e in evs:
            print("    %-26s %s" % (fmt_when(e), e["title"][:52]))
    print("\nSubject: %s" % subject)
    print("Preview: %s" % PREVIEW_PATH)

    if args.open:
        webbrowser.open(PREVIEW_PATH.as_uri())
    if args.send:
        to = send(subject, html_body, text_body, args.to)
        print("Sent to %s" % ", ".join(to))
    else:
        print("(not sent - add --send)")


if __name__ == "__main__":
    main()
