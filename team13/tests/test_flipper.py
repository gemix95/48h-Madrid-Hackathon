"""Offline test of flipper.py on a synthetic book with our real values (reads /api/me and /api/catalog only).

    source ../bazaar.env && python3 tests/test_flipper.py
"""
import json, os, sys, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from values import Values  # noqa: E402
from flipper import Flipper  # noqa: E402

URL, KEY = os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai"), os.environ["BAZAAR_KEY"]
get = lambda p: json.load(urllib.request.urlopen(urllib.request.Request(URL + p, headers={"X-Team-Key": KEY})))
me, cat = get("/api/me"), get("/api/catalog")
me = {**me, "cash": 300}
card = lambda ref, aid: {"id": aid, "kind": "card", "ref": ref, "rarity": "common", "set": ref[:3], "serial": 99}


class Api:
    def __init__(self): self.calls = []
    def accept(self, oid, assets=None): self.calls.append(("accept", oid, assets)); return {"queued": True}
    def list_offer(self, give, want, venue=None, to=None, expires_in_ticks=40):
        self.calls.append(("list", give, want, venue, to)); return {"id": 999}


class Ctx:
    pass


def make_ctx(boards):
    c = Ctx()
    c.S, c.me, c.state, c.clock, c.boards, c.board = {"flip_min_gain": 4, "flip_max_cash": 120}, me, {}, {"tick": 10}, boards, []
    c.values, c.api, c.intel = Values(cat, me), Api(), None
    c.venue_fee = lambda v: (0, 0) if v == "v02" else (500, 1)
    c.reserve = lambda: 40
    c._acc = 1
    def take():
        if c._acc <= 0: return False
        c._acc -= 1; return True
    c.take_accept = take
    c.log = lambda *a, **k: print("  log", a, {x: k[x] for x in k if x in ("ref", "price", "expected", "profit", "to")})
    return c


ask = lambda oid, ref, p, maker: {"id": oid, "maker": maker, "status": "open", "give": {"assets": [card(ref, 5000 + oid)]}, "want": {"cash": p}}
bid = lambda oid, ref, p, maker: {"id": oid, "maker": maker, "status": "open", "give": {"cash": p}, "want": {"types": [f"card:{ref}"]}}
boards = {"v02": [ask(1, "LAT-03", 8, "t04"), ask(2, "MAL-09", 70, "t09")],
          "rastro": [bid(3, "LAT-03", 18, "t15"), bid(4, "MAL-09", 85, "t17"), ask(5, "LAV-11", 30, "t06"), bid(6, "LAV-11", 60, "t06")]}

ctx = make_ctx(boards)
Flipper(ctx).step()
assert ctx.api.calls == [("accept", 1, None)], ctx.api.calls          # buys LAT-03 at 8, not MAL-09 (worth 91 to us), not t06 -> t06
assert ctx.state["flip"]["target"]["team"] == "t15"
print("tick 10 OK: bought LAT-03 at 8 for t15's bid of 18; skipped MAL-09 (keep) and the same-team pair")

# next tick: the card arrived, t15's bid is still there -> sell into it with that asset
me2 = {**me, "assets": me["assets"] + [card("LAT-03", 7777)]}
ctx.values, ctx.me, ctx.clock, ctx._acc, ctx.api.calls = Values(cat, me2), me2, {"tick": 11}, 1, []
Flipper(ctx).step()
assert ctx.api.calls == [("accept", 3, [7777])], ctx.api.calls
assert ctx.state["flip"]["stage"] == "settling"
print("tick 11 OK: sold LAT-03 into t15's bid at 18 (profit 18 - 2 fee - 8 = 8)")

# a tick later the card is gone -> flip done
ctx.values, ctx.me, ctx.clock = Values(cat, me), me, {"tick": 12}
Flipper(ctx).step()
assert "flip" not in ctx.state
print("tick 12 OK: flip closed")

# bid vanished while holding -> direct offer to the bidder at their price
ctx = make_ctx({"v02": [], "rastro": []})
ctx.state["flip"] = {"ref": "LAT-03", "stage": "holding", "since": 20, "buy": 8, "buy_venue": "v02", "seller": "t04",
                     "target": {"offer": 3, "venue": "rastro", "price": 18, "team": "t15"}, "held_before": 0}
ctx.values, ctx.me, ctx.clock = Values(cat, me2), me2, {"tick": 21}
Flipper(ctx).step()
assert ctx.api.calls and ctx.api.calls[0][0] == "list" and ctx.api.calls[0][4] == "t15", ctx.api.calls
print("holding OK: bid gone, direct offer to t15 at 18")
print("ALL OK")
