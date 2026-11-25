"""Render the README images headless with Playwright (no browser window opens).

  python scripts/render_media.py REVIEW_HTML MOCKED_TXT LIVE_TXT

1. media/review-queue.png  approval page for the sample leads (real Claude drafts)
2. media/mocked-run.png    the whole flow against Apollo and Smartlead fakes (addresses masked)
3. media/live-checks.png   the live parts: Claude drafting and the SPF/DKIM/DMARC check
"""
import html
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

MEDIA = Path(__file__).resolve().parent.parent / "media"
MONO = "'SF Mono', Menlo, Consolas, monospace"


def terminal_page(text, title):
    lines = []
    for line in html.escape(text).splitlines():
        if line.startswith("$ "):
            line = f"<span style='color:#7ee787'>$</span> <b style='color:#fff'>{line[2:]}</b>"
        elif line.startswith("# "):
            line = f"<span style='color:#8b949e'>{line}</span>"
        for word, colour in (("OK  ", "#3fb950"), ("WARN", "#d29922"), ("FAIL", "#f85149"),
                             ("FLAGGED", "#d29922"), ("STOPPED", "#f85149")):
            line = line.replace(word, f"<span style='color:{colour}'>{word}</span>")
        lines.append(line)
    return f"""<html><body style="margin:0;background:#fff">
<div id=wrap style="display:inline-block;padding:24px">
<div style="background:#0d1117;color:#c9d1d9;border-radius:10px;padding:18px 22px;font:14px/1.55 {MONO};
 box-shadow:0 4px 18px rgba(0,0,0,.18);width:1100px">
<div style="margin-bottom:10px"><span style="color:#f85149">&#9679;</span> <span style="color:#d29922">&#9679;</span>
 <span style="color:#3fb950">&#9679;</span> <span style="color:#8b949e;margin-left:8px">{html.escape(title)}</span></div>
<pre style="margin:0;white-space:pre-wrap;word-break:break-word;font:inherit">{chr(10).join(lines)}</pre></div></div>
</body></html>"""


def main(review_html, mocked_txt, live_txt):
    MEDIA.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1000, "height": 900}, device_scale_factor=2)
        page.goto(Path(review_html).resolve().as_uri())
        page.screenshot(path=str(MEDIA / "review-queue.png"), full_page=True)
        for src, name, title in ((mocked_txt, "mocked-run.png", "python -m outreach (Apollo and Smartlead mocked)"),
                                 (live_txt, "live-checks.png", "python -m outreach (live: Claude + DNS)")):
            page.set_content(terminal_page(Path(src).read_text(), title))
            page.locator("#wrap").screenshot(path=str(MEDIA / name))
        browser.close()


if __name__ == "__main__":
    main(*sys.argv[1:4])
