"""wtb.py on the real public feed and our real values (cash set to 300): asks go to holders that do not collect the
set, within caps and cash, one at a time. Reads /api/me, /api/catalog, /api/venues and data/feed.jsonl only.

    source ../bazaar.env && python3 tests/test_wtb.py
"""
import json, os, sys, urllib.request
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from values import Values  # noqa: E402
from wtb import Asker  # noqa: E402

URL, KEY = os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai"), os.environ["BAZAAR_KEY"]
get = lambda p: json.load(urllib.request.urlopen(urllib.request.Request(URL + p, headers={"X-Team-Key": KEY})))
feed = [json.loads(l) for l in open(os.path.join(HERE, "..", "..", "data", "feed.jsonl")) if l.strip()]
me, cat = {**get("/api/me"), "cash": 300}, get("/api/catalog")


class Ctx:
    pass


ctx = Ctx()
ctx.me, ctx.values, ctx.S = me, Values(cat, me), {"trade_min_gain": 3, "wtb_price_share": 0.9, "wtb_max_open": 3}
ctx.clock = {"tick": max(e["tick"] for e in feed)}
ctx.intel = type("I", (), {"events": {e["id"]: e for e in feed}})()
ctx.state, ctx.my_offers, ctx.reserve = {}, [], (lambda: 40)
ctx.venues = [v for v in get("/api/venues")["venues"] if v["status"] == "open"]
ctx.leaderboard = json.load(urllib.request.urlopen(URL + "/api/leaderboard"))["teams"]
calls = []
ctx.api = type("A", (), {"list_offer": lambda self, g, w, venue=None, to=None, expires_in_ticks=40:
                         calls.append((w["cards"][0], to, g["cash"], venue)) or {"id": 1}})()
ctx.log = lambda *a, **k: None

plan = Asker(ctx).plan()
for ref, team, price in plan[:8]:
    print(f"  ask {team} for {ref} at {price}")
assert plan, "expected some asks"
collectors = {"t17"}  # collects Malasaña and Salamanca on Friday
assert not any(t in collectors and r[:3] in ("MAL", "SAL") for r, t, _ in plan)
assert all(p <= 300 - 40 for _, _, p in plan)
assert ("MAL-10", "t09") in {(r, t) for r, t, _ in plan}, "t09's rarest card is MAL-10 and it dumps Malasaña"
assert all(p >= 0.6 * ctx.values.book(r) for r, _, p in plan)
Asker(ctx).step()
assert len(calls) == 1 and calls[0][3] != ctx.state.get("venue"), calls
print("posted:", calls[0])
print("OK")
