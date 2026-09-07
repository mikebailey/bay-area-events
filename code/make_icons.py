"""Render site/icon.svg to the PNGs iOS and Android need.

iOS ignores the web manifest's icon list entirely and reads <link rel=
"apple-touch-icon">, which must be a real PNG. Android reads the manifest. So
the SVG alone is not enough, and these PNGs are committed rather than built on
the fly: there is no build step on the site, and this is a once-a-year job.

    python code/make_icons.py

The travel project does this with a macOS-only bash snippet. This is the same
idea, portable, because the icon now needs re-rendering from the PC as often as
from the Mac.

Two things that look like bugs and are not:

  * Each size is rendered at its own CSS size. Rendering 512 once and scaling
    down with --force-device-scale-factor silently produces a blank PNG.
  * The window must be exactly the icon size with margins zeroed, or Chrome
    screenshots the SVG with a white gutter around it.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import SITE

SIZES = (180, 192, 512)

# The colour behind the artwork while it renders. Matches the SVG ground, so a
# rounding seam at the edge of the canvas is invisible rather than white.
GROUND = "#16181c"

PAGE = (
    "<html><head><meta charset='utf-8'><style>"
    "html,body{{margin:0;padding:0;background:{ground}}}"
    "svg{{display:block;width:{size}px;height:{size}px}}"
    "</style></head><body>{svg}</body></html>"
)

CHROME_CANDIDATES = [
    # Windows
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    # macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    # Linux
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


def find_chrome():
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    raise SystemExit(
        "No Chrome or Edge found. Install one, or render site/icon.svg to\n"
        "site/icon-180.png, icon-192.png and icon-512.png by any other means.")


def main():
    svg_path = SITE / "icon.svg"
    if not svg_path.exists():
        raise SystemExit("Missing %s" % svg_path)
    svg = svg_path.read_text(encoding="utf-8")
    chrome = find_chrome()
    print("Rendering with %s" % chrome)

    with tempfile.TemporaryDirectory() as tmp:
        for size in SIZES:
            page = Path(tmp) / ("icon-%d.html" % size)
            page.write_text(PAGE.format(ground=GROUND, size=size, svg=svg),
                            encoding="utf-8")
            out = SITE / ("icon-%d.png" % size)
            proc = subprocess.run(
                [chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
                 "--force-color-profile=srgb",
                 "--screenshot=%s" % out,
                 "--window-size=%d,%d" % (size, size),
                 page.as_uri()],
                capture_output=True, text=True)
            if not out.exists():
                raise SystemExit("Chrome wrote nothing for %d: %s"
                                 % (size, (proc.stderr or "")[-400:]))
            print("  %-16s %6d bytes" % (out.name, out.stat().st_size))


if __name__ == "__main__":
    main()
