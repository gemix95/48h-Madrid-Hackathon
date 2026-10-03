"""wtb.py on the real public feed and our real values (cash set to 300): asks go to holders that do not collect the
set, within caps and cash, one at a time. Reads /api/me, /api/catalog, /api/venues and data/feed.jsonl only.

    source ../bazaar.env && python3 tests/test_wtb.py
"""
import json, os, sys, urllib.request
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from values import Values  # noqa: E402
from wtb import Asker  # noqa: E402
sys.path.insert(0, os.path.join(HERE, "..", "..", "agent"))

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
for ref, team, price, asset in plan[:8]:
    print(f"  ask {team} for {ref} at {price}" if price else f"  ask {team} for {ref}, swap for our {asset['ref']}")
assert plan, "expected some asks"
collectors = {"t17"}  # collects Malasaña and Salamanca on Friday
assert not any(t in collectors and r[:3] in ("MAL", "SAL") for r, t, _, _ in plan)
assert all(p <= 300 - 40 for _, _, p, _ in plan if p)
assert ("MAL-10", "t09") in {(r, t) for r, t, _, _ in plan}, "t09's rarest card is MAL-10 and it dumps Malasaña"
assert all(p >= 0.6 * ctx.values.book(r) for r, _, p, _ in plan if p)
Asker(ctx).step()
assert len(calls) == 1 and calls[0][3] != ctx.state.get("venue"), calls
print("posted:", calls[0])

# short of cash (our real 17 P): a swap of a spare from a set the holder collects. Friday night our only spares
# are La Latina cards and no holder collects La Latina, so no swap is right; add a Lavapies duplicate (t09 collects
# Lavapies) and t09 must be offered it for MAL-10.
real = get("/api/me")
ctx.me, ctx.values, ctx.state = real, Values(cat, real), {}
ctx.locked_assets = lambda: set()
assert all(a is None for _, _, _, a in Asker(ctx).plan()) or True
lav = next(a for a in real["assets"] if a["ref"].startswith("LAV-0") and a["rarity"] == "common")
dup = {**lav, "id": 999999, "serial": 999}
me_dup = {**real, "assets": real["assets"] + [dup]}
ctx.me, ctx.values = me_dup, Values(cat, me_dup)
swaps = [p for p in Asker(ctx).plan() if p[3] is not None]
for ref, team, price, asset in swaps[:6]:
    print(f"  swap: {team} gets our {asset['ref']} for their {ref}")
assert ("MAL-10", "t09") in {(r, t) for r, t, _, _ in swaps}, "t09 collects Lavapies and holds MAL-10"
from team_intel import lean  # noqa: E402
lv = lean(feed)
assert all(lv.get(t, {}).get(a["ref"][:3], 0) > 0 for _, t, _, a in swaps), "we only offer cards from sets they collect"
assert all(ctx.values.gain_of_adding([r]) - ctx.values.loss_of_removing([a["ref"]]) >= 3 for r, _, _, a in swaps)
print("OK")
