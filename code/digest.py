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

from config import HOME_LABEL, ROOT, SITE
from fetch import load_env

SITE_URL = "https://bayarea.michaelbailey.org"
PREVIEW_PATH = ROOT / "outputs" / "digest-preview.html"

# Same weighting the page uses, so the email agrees with what you see there.
DAY_WEIGHT = {6: 1.00, 5: 0.70, 4: 0.45, 0: 0.35, 1: 0.35, 2: 0.35, 3: 0.35}


def day_weight(ev):
    # Sunday is worth nearly as much as Saturday for a family weekend.
    d = datetime.fromisoformat(ev["start"]).date()
    return 0.85 if d.weekday() == 6 else DAY_WEIGHT.get(d.weekday(), 0.35)


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


def build_sections(events):
    """The digest is four short lists, not one long one."""
    week = upcoming(events)
    weekend = [e for e in week
               if datetime.fromisoformat(e["start"]).date().weekday() in (5, 6)]

    top = pick(weekend or week, 6)
    backyard = pick(week, 4,
                    where=lambda e: e.get("drive") is not None and e["drive"] <= 20,
                    exclude=top)
    adults = pick(week, 3, key=lambda e: e.get("adultScore") or 0,
                  where=lambda e: e.get("dateNight"), exclude=top + backyard)
    # Anything the pipeline only discovered in the last week is worth calling
    # out: it is precisely what you would not have seen otherwise.
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    fresh = pick(week, 4,
                 where=lambda e: (e.get("firstSeen") or "") >= cutoff and e["score"] >= 60,
                 exclude=top + backyard + adults)

    return [
        ("This weekend", top),
        ("In your backyard", backyard),
        ("For the two of you", adults),
        ("Newly found this week", fresh),
    ]


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
        "<h1 style='font-size:21px;margin:0 0 2px'>Bay Area, the week ahead</h1>"
        "<div style='color:#777;font-size:13px'>from %s · %s</div>"
        "%s"
        "<div style='margin-top:30px;padding-top:14px;border-top:1px solid #ddd;"
        "color:#999;font-size:12px'>"
        "<a href='%s' style='color:#2f6f4f'>See everything on the site</a> · "
        "%d events tracked · drive times are estimates"
        "</div></div></body></html>"
        % (esc(HOME_LABEL), esc(date.today().strftime("%B %d, %Y")),
           "".join(blocks), SITE_URL, meta["total"]))


def render_text(sections, meta):
    lines = ["Bay Area, the week ahead", "from %s" % HOME_LABEL, ""]
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


def send(subject, html_body, text_body, to_addr):
    user = os.environ.get("GMAIL_USER") or os.environ.get("DIGEST_TO")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    if not user or not password:
        raise SystemExit(
            "Missing GMAIL_USER and/or GMAIL_APP_PASSWORD.\n"
            "Add them to .env locally, or as repo secrets in GitHub Actions.\n"
            "The app password is generated at https://myaccount.google.com/apppasswords\n"
            "(requires 2FA on the account; it is 16 characters, spaces optional).")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr or user
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as s:
        s.starttls(context=ssl.create_default_context())
        # Gmail shows app passwords in groups of four; the spaces are cosmetic.
        s.login(user, password.replace(" ", ""))
        s.send_message(msg)
    return msg["To"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true", help="actually send the email")
    ap.add_argument("--to", help="override the recipient")
    ap.add_argument("--open", action="store_true", help="open the preview in a browser")
    args = ap.parse_args()

    load_env()
    data, events = load_events()
    sections = build_sections(events)
    meta = {"total": len(events)}

    kept = sum(len(v) for _, v in sections)
    if not kept:
        print("Nothing to send: no events in the next nine days.")
        return

    html_body = render_html(sections, meta)
    text_body = render_text(sections, meta)
    top = next((v for t, v in sections if v), [])
    lead = top[0]["title"] if top else "the week ahead"
    subject = "This weekend: %s%s" % (
        lead[:52], " and %d more" % (kept - 1) if kept > 1 else "")

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
        print("Sent to %s" % to)
    else:
        print("(not sent - add --send)")


if __name__ == "__main__":
    main()
