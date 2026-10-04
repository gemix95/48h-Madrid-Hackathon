"""Arbitrage, offline: the score formula and price cap follow the organisers' slide; a pair is picked, haggled,
bought only with the right card under the cap and while the bid lives, then sold into the bid. The ladder slack
only ever relaxes the bar at a dealer whose best three still has an empty slot, and the guard leaves the pair's
dealer bid alone (it is above our value on purpose).

    python3 tests/test_arbitrage.py
"""
import os, sys, tempfile
from collections import Counter
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import strategy, arbitrage  # noqa: E402
from arbitrage import score, max_price  # noqa: E402

assert score(35, 55, 67) == -20 + 32 and max_price(35, 67, 8) == 35 + 32 - 8   # LAT-10 at Pícaros vs t18's 72 on El Rastro
assert max_price(85, 195, 8) == 85 + 50 - 8 and score(85, 140, 195) == -5        # the 50 cap kills the epic
assert max_price(35, 40, 8) is None                                              # bid too close to our value

arbitrage.FEED_STORE = os.path.join(tempfile.mkdtemp(), "feed.jsonl"); open(arbitrage.FEED_STORE, "w").write("")


class V:
    def __init__(s):
        s.cards = {"LAT-10": {"rarity": "rare", "set": "LAT"}}; s.assets = []; s.held = Counter()
    def gain_of_adding(s, refs): return 35.0


class Api:
    def __init__(s, ctx): s.ctx, s.said, s.accepted, s.thread_state = ctx, [], [], {"status": "open", "standing_offers": []}
    def open_thread(s, who, topic=None): s.opened = (who, topic); return {"id": 900}
    def say(s, tid, text, price=None): s.said.append(price)
    def thread(s, tid): return s.thread_state
    def accept(s, oid, assets=None): s.accepted.append((oid, assets))
    def close_thread(s, tid): s.closed = tid


class Ctx:
    def __init__(s):
        s.S, s.state, s.logs = {**strategy.defaults(), "enable_arbitrage": 1}, {}, []
        s.clock, s.me, s.values, s.threads = {"tick": 1300}, {"id": "t13", "cash": 400, "unlocked": ["picaros"]}, V(), []
        s.dealers = [{"id": "picaros", "status": "active", "menu": {"sells": [{"rarity": "rare", "sets": "released", "list_price": 63}]}}]
        s.api = Api(s); s.bid_alive = True
    def log(s, *a, **k): s.logs.append((a, k))
    def reserve(s): return 40
    def day_key(s): return "sat"
    def take_accept(s): return True
    def public_get(s, path):
        if path == "/api/venues": return {"venues": [{"venue": "rastro", "status": "open", "fee_bps": 500, "fee_per_card": 1, "owner": "world"}]}
        return {"offers": [{"id": 18535, "give": {"cash": 72}, "want": {"types": ["card:LAT-10"]}, "expires_tick": 1384}] if s.bid_alive else []}


def _args(ctx):
    """The venues/boards/makers/dealers candidates() reads, from a context's fake API."""
    venues = ctx.public_get("/api/venues")["venues"]
    return venues, {v["venue"]: ctx.public_get("/offers")["offers"] for v in venues}, {}, ctx.dealers


c = Ctx(); arb = arbitrage.Arbitrage(c)
arb.step()
a = c.state["arb"]["active"]
assert a and a["dealer"] == "picaros" and a["cap"] == 59 and c.api.opened == ("picaros", {"buy": {"card": "LAT-10"}}), a
# the dealer offers a switched card: ignored; then LAT-10 at 58: bought
c.clock["tick"] = 1301; c.api.thread_state["standing_offers"] = [{"id": 1, "maker": "picaros", "status": "open", "give": {"types": ["card:LAT-07"]}, "want": {"cash": 50}}]
arb.step(); assert not c.api.accepted
c.clock["tick"] = 1302; c.api.thread_state["standing_offers"] = [{"id": 2, "maker": "picaros", "status": "open", "give": {"types": ["card:LAT-10"]}, "want": {"cash": 58}}]
arb.step(); assert c.api.accepted == [(2, None)] and c.state["arb"]["active"]["phase"] == "wait_card"
# the card arrives: sold into the bid
c.values.assets = [{"id": 555, "ref": "LAT-10"}]; c.clock["tick"] = 1303
arb.step(); assert c.api.accepted[-1] == (18535, [555]) and c.state["arb"]["active"] is None and c.state["arb"]["done"]["sat"] == 1
# a new pair whose bid vanishes before we buy: we walk away without buying
c2 = Ctx(); arb2 = arbitrage.Arbitrage(c2); arb2.step()
c2.bid_alive = False; c2.clock["tick"] = 1301
c2.api.thread_state["standing_offers"] = [{"id": 3, "maker": "picaros", "status": "open", "give": {"types": ["card:LAT-10"]}, "want": {"cash": 50}}]
arb2.step(); assert not c2.api.accepted and c2.state["arb"]["active"] is None

# the ladder: the bar drops only at a dealer whose best three still has an empty slot, and only with the knob on
c3 = Ctx()
assert arbitrage.candidates(c3, *_args(c3), 8) and not arbitrage.candidates(c3, *_args(c3), 30)
(sc, plan), = arbitrage.candidates(c3, *_args(c3), 8)
assert plan["ladder_slot"] and plan["dealer_deals"] == 0 and plan["bar"] == 8   # slack is 0 by default: nothing moves
assert arbitrage.candidates(c3, *_args(c3), 30, ladder_slack=10)   # a 20 P bar still fits over the 45 P opening
assert not arbitrage.candidates(c3, *_args(c3), 30, ladder_slack=1)
c3.threads = [{"kind": "persona", "with": "picaros", "status": "deal"}] * 3     # best three already full
assert not arbitrage.candidates(c3, *_args(c3), 30, ladder_slack=10)

# the guard leaves the pair's dealer bid alone: it is above our value because the card is resold at once
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from guard import Guard  # noqa: E402
g = Ctx(); g.state = {"arb": {"active": {"thread": 900}}}
g.values.loss_of_removing = lambda refs: 0.0
g.raw = type("Raw", (), {"my_offers": lambda s: {"offers": [
    {"id": 7, "maker": "t13", "status": "open", "thread": 900, "give": {"cash": 58}, "want": {"types": ["card:LAT-10"]}},
    {"id": 8, "maker": "t13", "status": "open", "thread": 901, "give": {"cash": 58}, "want": {"types": ["card:LAT-10"]}}]}})()
g.my_offers, g.cancelled = [], []
g.api.cancel = lambda oid: g.cancelled.append(oid)
Guard(g).step()
assert g.cancelled == [8], g.cancelled   # 58 P for a card worth 35 is cancelled, unless it is the arbitrage pair

# the accept slot is busy (another of our agents took it): retry, never drop the card while the bid lives
c4 = Ctx(); a4 = arbitrage.Arbitrage(c4); a4.step()
c4.clock["tick"] = 1301; c4.api.thread_state = {"status": "open", "standing_offers": [{"id": 9, "maker": "picaros", "status": "open", "give": {"types": ["card:LAT-10"]}, "want": {"cash": 55}}]}
a4.step(); c4.values.assets = [{"id": 777, "ref": "LAT-10"}]
fails = {"n": 2}
real_accept = c4.api.accept
def flaky(oid, assets=None):
    if oid == 18535 and fails["n"] > 0:
        fails["n"] -= 1
        raise arbitrage.BazaarError("rate_limited: one accept per tick")
    return real_accept(oid, assets)
c4.api.accept = flaky
for t in (1302, 1303, 1304):
    c4.clock["tick"] = t; a4.step()
assert c4.api.accepted[-1] == (18535, [777]) and c4.state["arb"]["active"] is None, c4.api.accepted
# a bar far below the dealer's list is skipped when min_reach is set
c5 = Ctx(); c5.values.gain_of_adding = lambda refs: 10.0
assert not arbitrage.candidates(c5, *_args(c5), 8, min_reach=0.85) or all(p["cap"] >= 0.85 * p["list"] for _, p in arbitrage.candidates(c5, *_args(c5), 8, min_reach=0.85))

import json  # noqa: E402
# a bid addressed to us (in no public book) is a candidate from the feed; its cancellation stops the pair
feed = [{"id": 1, "tick": 1290, "type": "offer.listed", "payload": {"offer": {"id": 21142, "maker": "t05", "to": "t13",
         "venue": "rastro", "give": {"cash": 72}, "want": {"types": ["card:LAT-10"]}, "expires_tick": 1400}}}]
open(arbitrage.FEED_STORE, "w").write("\n".join(json.dumps(e) for e in feed) + "\n")
c6 = Ctx(); c6.bid_alive = False; a6 = arbitrage.Arbitrage(c6); a6.step()
p6 = c6.state["arb"]["active"]
assert p6 and p6["bid"] == 21142 and p6["addressed"], p6
assert a6._bid_open(p6)
open(arbitrage.FEED_STORE, "a").write(json.dumps({"id": 2, "tick": 1301, "type": "offer.cancelled", "payload": {"offer": 21142, "venue": "rastro"}}) + "\n")
assert not a6._bid_open(p6)
# the haggle opens near the dealer's recent sale price (two steps to the cap)
feed.append({"id": 3, "tick": 1250, "type": "settlement", "payload": {"persona": "picaros", "price": 57, "items": [
    {"kind": "card", "ref": "LAT-09", "rarity": "rare", "frm": "picaros", "to": "t02"}]}})
open(arbitrage.FEED_STORE, "w").write("\n".join(json.dumps(e) for e in feed) + "\n")
c7 = Ctx(); a7 = arbitrage.Arbitrage(c7); a7.step()
assert c7.state["arb"]["active"]["price"] == 55, c7.state["arb"]["active"]   # 57 - 2, not 45
open(arbitrage.FEED_STORE, "w").write("")

# the other way round: a team's ask below our value is bought, then offered to a dealer at our value or more
class V2(V):
    def __init__(s):
        super().__init__(); s.cards["RET-09"] = {"rarity": "rare", "set": "RET"}
    def gain_of_adding(s, refs): return 49.0 if refs == ["RET-09"] else 35.0
    def loss_of_removing(s, refs): return 49.0
c8 = Ctx(); c8.values = V2(); c8.bid_alive = False
c8.dealers = [{"id": "pilar", "status": "active", "menu": {"buys": [{"rarity": "rare", "sets": ["SAL", "RET"]}]}}]
c8.me["unlocked"] = ["pilar"]
c8.public_get = book = lambda path: ({"venues": [{"venue": "v02", "status": "open", "fee_bps": 0, "owner": "t12"}]} if path == "/api/venues"
                              else {"offers": [{"id": 4001, "give": {"assets": [{"id": 91, "ref": "RET-09"}]}, "want": {"cash": 30}}]})
a8 = arbitrage.Arbitrage(c8); a8.step()
r = c8.state["arb"]["resale"]
assert c8.api.accepted == [(4001, None)] and r and r["cost"] == 30 and r["value"] == 49.0, (c8.api.accepted, r)
empty = lambda path: ({"venues": [{"venue": "v02", "status": "open", "fee_bps": 0, "owner": "t12"}]} if path == "/api/venues" else {"offers": []})
c8.public_get = empty  # the ask we took is gone from the book
c8.values.assets = [{"id": 91, "ref": "RET-09"}]; c8.clock["tick"] = 1301
c8.api.opened = None; a8.step()
r = c8.state["arb"]["resale"]
assert c8.api.opened == ("pilar", {"sell": {"assets": [91]}}) and r["floor"] == 50 and r["price"] >= 60, (c8.api.opened, r)
# Pilar offers 52 for exactly our card: taken (at our value or more)
c8.clock["tick"] = 1302
c8.api.thread_state = {"status": "open", "standing_offers": [{"id": 5, "maker": "pilar", "status": "open", "give": {"cash": 52}, "want": {"assets": [91]}}]}
a8.step()
assert c8.api.accepted[-1] == (5, None) and c8.state["arb"]["resale"] is None
# a dealer that offers below our value: never taken; after the rounds we keep the card
c9 = Ctx(); c9.values = V2(); c9.bid_alive = False; c9.dealers = c8.dealers; c9.me["unlocked"] = ["pilar"]; c9.public_get = book
a9 = arbitrage.Arbitrage(c9); a9.step(); c9.values.assets = [{"id": 91, "ref": "RET-09"}]; c9.public_get = empty
for t in range(1301, 1310):
    c9.clock["tick"] = t
    c9.api.thread_state = {"status": "open", "standing_offers": [{"id": 6, "maker": "pilar", "status": "open", "give": {"cash": 45}, "want": {"assets": [91]}}]}
    a9.step()
assert (6, None) not in c9.api.accepted and c9.state["arb"].get("resale") is None and (4001, None) in c9.api.accepted and any(x[0][1] == "kept" for x in c9.logs), c9.logs[-3:]
print("arbitrage ok: pick, haggle, right card only, bid re-checked, sold into the bid, ladder slack, guard exemption,"
      " addressed bids, recent dealer price, value buy and resale at our value or more")
