"""Radio Rastro watcher: print every new item from GET /api/news (public, no key), market-moving ones marked.

Some items are true and the market moves as they say, some are rumours that never happen, some are just Madrid
(level 'radio'). A MARKET line names a dealer, a set, a card or a price: worth one probe deal to check.

    python3 agent/news.py [poll_seconds]
"""
import json
import re
import sys
import time
import urllib.request

URL = "https://bazaar.causaprima.ai/api/news"
MARKET = re.compile(r"abuela|chato|pilar|carmen|vault|pack|sobre|legendar|epic|rare|uncommon|common|price|pays?|"
                    r"sell|buy|primas?|\b(LAV|MAL|LAT|SAL|RET|CHA)\b|lavapi|malasa|latina|salamanca|retiro|chamber|"
                    r"market|fee|bench|duel", re.I)
POLL = float(sys.argv[1]) if len(sys.argv) > 1 else 30
seen = None
while True:
    try:
        with urllib.request.urlopen(URL, timeout=15) as r:
            items = json.load(r).get("news", [])
        if seen is None:
            seen = {n["id"] for n in items}
            print(f"NEWS watcher on: {len(items)} items so far, latest #{max(seen) if seen else '-'}", flush=True)
        for n in sorted((n for n in items if n["id"] not in seen), key=lambda n: n["id"]):
            seen.add(n["id"])
            text = f"{n.get('headline', '')} {n.get('body', '')}"
            tag = "MARKET" if MARKET.search(text) else "news"
            print(f"{tag} #{n['id']} t{n.get('tick')} [{n.get('source_name')}] {n.get('headline')}"
                  f"{' | ' + n['body'] if n.get('body') else ''}", flush=True)
    except Exception as e:  # keep listening through network errors
        print(f"NEWS-ERROR {type(e).__name__}: {e}", flush=True)
    time.sleep(POLL)
