#!/usr/bin/env python3
"""
Render GUIDE.md to a printable PDF.

    python build_guide_pdf.py            -> BlueICE_Dashboard_Guide.pdf

GUIDE.md stays the single source. This produces a presentation copy of it, so
there is nothing to keep in step by hand -- re-run after editing the markdown.

Print styling deliberately departs from the product's dark theme: a dark PDF is
useless on paper and wasteful on a printer. The type and the accent colours are
the product's; the ground is not.
"""

import html
import os
import re
import subprocess
import sys
import tempfile

try:
    import mistune
except ImportError:
    raise SystemExit("mistune is required:\n    python -m pip install mistune")

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "GUIDE.md")
OUT = os.path.join(HERE, "BlueICE_Dashboard_Guide.pdf")

BROWSER = None
for candidate in (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
):
    if os.path.exists(candidate):
        BROWSER = candidate
        break
if BROWSER is None:
    raise SystemExit("No Chrome or Edge found to render the PDF with.")

CSS = """
@page { size: A4; margin: 18mm 16mm 16mm; }

:root {
  --ink:#15181b; --ink-2:#3b444b; --muted:#69737b;
  --rule:#ccd3d8; --rule-2:#aab4bc; --panel:#f4f6f8; --recess:#eceff2;
  --working:#17559f; --idling:#a63c13; --caution:#7d5a06; --carried:#0b6849;
  --sans:"IBM Plex Sans Condensed","Segoe UI",system-ui,sans-serif;
  --serif:"IBM Plex Serif",Georgia,"Times New Roman",serif;
  --mono:"IBM Plex Mono",Consolas,"Courier New",monospace;
}

* { box-sizing: border-box; }
body {
  font-family: var(--serif);
  font-size: 10.4pt;
  line-height: 1.55;
  color: var(--ink);
  background: #fff;
  margin: 0;
  -webkit-print-color-adjust: exact;
  print-color-adjust: exact;
}

h1 {
  font-family: var(--sans); font-weight: 700; font-size: 25pt; line-height: 1.05;
  letter-spacing: -.01em; margin: 0 0 .5rem;
}
h2 {
  font-family: var(--sans); font-weight: 700; font-size: 15pt; line-height: 1.15;
  margin: 1.6rem 0 .6rem; padding-top: .55rem; border-top: 1px solid var(--rule);
  break-after: avoid; page-break-after: avoid;
}
h3 {
  font-family: var(--sans); font-weight: 600; font-size: 11.6pt;
  margin: 1.1rem 0 .35rem;
  break-after: avoid; page-break-after: avoid;
}
p { margin: 0 0 .62rem; }
strong { font-weight: 600; }
ul, ol { padding-left: 1.05rem; margin: 0 0 .7rem; }
li { margin-bottom: .22rem; }
a { color: var(--working); text-decoration: none; word-break: break-word; }

hr { border: 0; border-top: 1px solid var(--rule); margin: 1.3rem 0; }

code {
  font-family: var(--mono); font-size: .86em;
  background: var(--recess); border: 1px solid var(--rule);
  border-radius: 3px; padding: .04em .28em;
}
pre {
  font-family: var(--mono); font-size: 7.6pt; line-height: 1.32;
  background: var(--recess); border: 1px solid var(--rule); border-radius: 5px;
  padding: .6rem .7rem; margin: 0 0 .8rem;
  white-space: pre; overflow: hidden;
  break-inside: avoid; page-break-inside: avoid;
}
pre code { background: none; border: 0; padding: 0; font-size: inherit; }

table {
  border-collapse: collapse; width: 100%;
  font-family: var(--mono); font-size: 8.2pt; line-height: 1.35;
  margin: 0 0 .9rem;
  break-inside: avoid; page-break-inside: avoid;
}
th, td {
  text-align: left; padding: .3rem .45rem;
  border-bottom: 1px solid var(--rule); vertical-align: top;
}
thead th {
  font-family: var(--sans); font-size: 7.4pt; font-weight: 600;
  letter-spacing: .08em; text-transform: uppercase; color: var(--muted);
  border-bottom-color: var(--rule-2);
}
/* The second column of most tables is prose, not data. */
td:last-child { font-family: var(--serif); font-size: 9.2pt; }
table:only-child td:last-child { font-family: var(--mono); }

blockquote {
  margin: .8rem 0; padding: .55rem .8rem .05rem;
  border-left: 2px solid var(--caution); background: #fdf6e6;
  break-inside: avoid; page-break-inside: avoid;
}
blockquote p { margin-bottom: .5rem; font-size: 9.8pt; }

.masthead {
  border-bottom: 1px solid var(--rule-2); padding-bottom: .8rem; margin-bottom: 1.2rem;
}
.masthead .eyebrow {
  font-family: var(--mono); font-size: 8pt; letter-spacing: .18em;
  text-transform: uppercase; color: var(--working); margin: 0 0 .5rem;
}
.masthead .meta {
  font-family: var(--mono); font-size: 8pt; color: var(--muted); margin: .7rem 0 0;
}
"""

MASTHEAD = """<div class="masthead">
<p class="eyebrow">BlueICE Fleet Telemetry &middot; Operating guide</p>
<h1>Reading the Vehicle Dashboard</h1>
<p class="meta">Hamza &middot; September 2026 &middot; dashboard/vehicle.html &middot;
generated from GUIDE.md</p>
</div>"""


def main():
    md = open(SRC, encoding="utf-8").read()

    # The masthead replaces the markdown's own H1 and its lede, so the PDF does
    # not open with a duplicated title.
    md = re.sub(r"\A# .*?\n", "", md, count=1)

    render = mistune.create_markdown(plugins=["table", "strikethrough", "url"])
    body = render(md)

    page = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>Reading the Vehicle Dashboard</title>"
            "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?"
            "family=IBM+Plex+Mono:wght@400;500;600&"
            "family=IBM+Plex+Sans+Condensed:wght@600;700&"
            "family=IBM+Plex+Serif:ital,wght@0,400;0,600;1,400&display=swap\">"
            "<style>" + CSS + "</style></head><body>"
            + MASTHEAD + body + "</body></html>")

    tmp = tempfile.mkdtemp(prefix="guide-pdf-")
    src = os.path.join(tmp, "guide.html")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(page)

    subprocess.run([
        BROWSER, "--headless=new", "--disable-gpu", "--no-first-run",
        "--user-data-dir=" + os.path.join(tmp, "profile"),
        "--no-pdf-header-footer",
        "--print-to-pdf=" + OUT,
        "file:///" + src.replace("\\", "/"),
    ], check=True, timeout=180,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if not os.path.exists(OUT):
        raise SystemExit("Chrome produced no PDF.")
    print("wrote %s  (%s bytes) from GUIDE.md"
          % (OUT, format(os.path.getsize(OUT), ",")))


if __name__ == "__main__":
    sys.exit(main())
