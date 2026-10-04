"""Refresh the card art the board shows (cards.json): render the game's own /cards page in headless Chrome and keep
each card's markup, one per ref. Run it on a laptop with Google Chrome when a new set comes out (Chamberí, Sunday).

    python3 dashboard/cards/refresh.py           # writes dashboard/cards/cards.json, prints the cards per set

It never shrinks the file: a render that finds fewer cards than we already have is refused.
"""
from __future__ import annotations

import collections
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "cards.json"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PAGE = "https://bazaar.causaprima.ai/cards"


def render(limit: int = 120) -> str:
    """The rendered page. Chrome prints the DOM but may not exit afterwards (the page keeps polling): read its output
    until the document ends, then stop it."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "dom.html"
        with open(out, "w") as f:
            proc = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={tmp}/prof",
                                     "--virtual-time-budget=15000", "--window-size=1400,4000", "--dump-dom", PAGE],
                                    stdout=f, stderr=subprocess.DEVNULL)
            try:
                for _ in range(limit):
                    if proc.poll() is not None or out.read_text(errors="ignore").rstrip().endswith("</html>"):
                        break
                    time.sleep(1)
            finally:
                proc.kill()
                proc.wait()
        return out.read_text(errors="ignore")


def extract(h: str) -> dict:
    out = {}
    for m in re.finditer(r'<div class="cromo cromo--[^"]*"[^>]*aria-label="([A-Z]{3}-\d{2}) ([^"]*)"', h):
        ref = m.group(1)
        if ref in out:
            continue
        i, depth, j = m.start(), 0, m.start()
        for t in re.finditer(r'<(/?)div\b[^>]*>', h[i:]):
            depth += -1 if t.group(1) else 1
            if depth == 0:
                j = i + t.end()
                break
        el = h[i:j]
        el = re.sub(r' (role|tabindex|data-clickable)="[^"]*"', '', el)
        el = el.replace('transform: perspective(900px);', '')
        out[ref] = re.sub(r'cromo--md', 'cromo--sm', el, count=1)
    return out


def main():
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    new = extract(render())
    if len(new) < len(old):
        sys.exit(f"refused: the page gave {len(new)} cards, we have {len(old)}")
    OUT.write_text(json.dumps(new, separators=(",", ":")))
    sets = collections.Counter(r[:3] for r in new)
    added = sorted(set(new) - set(old))
    print(f"{len(new)} cards ({len(added)} new: {', '.join(added) or 'none'}); per set: {dict(sorted(sets.items()))}")


if __name__ == "__main__":
    main()
