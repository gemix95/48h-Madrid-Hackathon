"""El Club cashback: every trade between two teams on OUR market pays both sides back; caps, the paid/expired book, the
profit check every 4 P and the announcement. No network.

    python3 tests/test_cashback.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import market  # noqa: E402
import strategy  # noqa: E402

notes = []
market.council_note = lambda topic, text, evidence=None, tick=None: notes.append(text)


class Api:
    def __init__(self):
        self.offers, self.threads, self.said, self.n = [], [], [], 100

    def list_offer(self, give, want, venue=None, to=None, expires_in_ticks=40):
        self.n += 1
        self.offers.append({"id": self.n, "give": give, "want": want, "venue": venue, "to": to})
        return {"id": self.n}

    def open_thread(self, team, venue=None):
        self.threads.append(team)
        return {"id": 900 + len(self.threads)}

    def say(self, tid, text):
        self.said.append(text)


class Intel:
    def __init__(self):
        self.events = {}

    def trade(self, sid, tick, a, b, venue="v03", price=10, cards=True):
        items = [{"kind": "card", "ref": "LAT-02", "frm": a, "to": b}] if cards else []
        self.events[sid] = {"id": sid, "type": "settlement", "tick": tick, "payload": {
            "settlement": sid, "tick": tick, "kind": "trade", "parties": [a, b], "venue": venue, "price": price, "items": items}}


class Ctx:
    def __init__(self, free_market=True):
        self.S = {**strategy.defaults()}
        self.state = {"venue": "v03"}
        self.clock = {"tick": 100, "today": "sat"}
        self.me = {"id": "t13", "level": 3, "score": {"score": 25, "mm_points": 0.0}}
        self.api, self.intel, self.threads, self.my_offers = Api(), Intel(), [], []
        self.leaderboard = [{"team": "t13", "score": 25}, {"team": "t12", "score": 30}, {"team": "t07", "score": 10}]
        self.venues = [{"venue": "v02", "owner": "t12", "fee_bps": 0, "fee_per_card": 0, "status": "open"}]
        if free_market:
            self.venues.append({"venue": "v11", "owner": "t07", "fee_bps": 0, "fee_per_card": 0, "status": "open"})

    def log(self, *a, **k):
        pass

    def limit(self, name, default):
        return default


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    return bool(ok)


r = []
ctx = Ctx(); m = market.Market(ctx)
m._cb()  # the promo starts at tick 100
ctx.intel.trade(1, 90, "t02", "t05")  # before the promo: not paid
ctx.intel.trade(2, 101, "t02", "t05")  # on our market
ctx.intel.trade(3, 101, "t02", "t04", venue="rastro")  # elsewhere: not ours
ctx.clock["tick"] = 102
m.cashback(102)
o = ctx.api.offers
r.append(check("both sides of a trade on our market get paid", sorted(x["to"] for x in o) == ["t02", "t05"], o))
r.append(check("trades before the promo or on other markets are not paid", len(o) == 2))
r.append(check("pays 1 P for nothing in return", all(x["give"] == {"cash": 1} and x["want"] == {} for x in o), o[0]))
r.append(check("paid on a free market that is not a close rival's (t12 leads us)", all(x["venue"] == "v11" for x in o), o[0]["venue"]))
r.append(check("each team gets a thread message saying how to accept", ctx.api.threads == ["t02", "t05"] and "/accept" in ctx.api.said[0]))

ctx.intel.trade(4, 103, "t02", "t05"); ctx.intel.trade(5, 103, "t02", "t09"); ctx.clock["tick"] = 104
ctx.my_offers = [{"id": x["id"]} for x in o]  # still open
m.cashback(104)
r.append(check("at most 2 payouts per team per day", sum(1 for x in o if x["to"] == "t02") == 2, [x["to"] for x in o]))

# t02 and t05 accept their first ones: the offers leave our book and cash-only settlements follow
for oid, team in ((101, "t02"), (102, "t05")):
    ctx.intel.events[f"s{oid}"] = {"id": f"s{oid}", "type": "settlement", "tick": 104, "payload": {
        "parties": ["t13", team], "price": 1, "items": [], "venue": "v11"}}
ctx.my_offers = [{"id": x["id"]} for x in o if x["id"] not in (101, 102)]
ctx.clock["tick"] = 105
m.cashback(105)
cb = ctx.state["cashback"]
r.append(check("accepted cashback counts as spent", cb["spent"] == 2, cb["spent"]))

# 2 more accepted with no market-making gain: the check at 4 P pauses today's promo
for oid, team in ((103, "t02"), (104, "t05")):
    ctx.intel.events[f"s{oid}"] = {"id": f"s{oid}", "type": "settlement", "tick": 106, "payload": {
        "parties": ["t13", team], "price": 1, "items": [], "venue": "v11"}}
ctx.my_offers = [{"id": x["id"]} for x in o if x["id"] > 104]
ctx.clock["tick"] = 107
m.cashback(107)
r.append(check("every 4 P paid, the profit check runs", len(cb["checks"]) == 1 and notes, cb["checks"]))
r.append(check("no market-making gain: promo paused for today", bool(cb["paused"]) and not m.cashback_active()))

# same with a gain: keeps going
ctx2 = Ctx(); m2 = market.Market(ctx2); cb2 = m2._cb()
cb2["spent"] = 4; ctx2.me["score"]["mm_points"] = 0.05
m2.cashback(101)
r.append(check("market-making grew: promo keeps going", cb2["checks"][-1]["keep"] and m2.cashback_active(), cb2["checks"]))

# no free market for us: El Rastro, grossed up so they still net 1 P after its 5% fee (ceil)
ctx3 = Ctx(free_market=False); m3 = market.Market(ctx3); m3._cb()
ctx3.intel.trade(9, 101, "t03", "t08"); ctx3.clock["tick"] = 102
m3.cashback(102)
r.append(check("no free safe market: El Rastro, 2 P so they net 1 P", ctx3.api.offers and all(x["venue"] == "rastro" and x["give"] == {"cash": 2} for x in ctx3.api.offers), ctx3.api.offers[:1]))

# day budget
ctx4 = Ctx(); ctx4.S["cashback_day_cap"] = 3; m4 = market.Market(ctx4); m4._cb()
for i in range(5):
    ctx4.intel.trade(20 + i, 101, f"t{30 + 2 * i}", f"t{31 + 2 * i}")
ctx4.clock["tick"] = 102
m4.cashback(102)
r.append(check("never more than the day budget in offers", sum(x["give"]["cash"] for x in ctx4.api.offers) == 3, len(ctx4.api.offers)))

ann = Ctx(); ma = market.Market(ann); ma._cb()
text = ma.cashback_announcement()
r.append(check("announcement fits the big screen and says CASHBACK", len(text) <= market.ANNOUNCE_MAX and "CASHBACK" in text and "El Club" in text, len(text)))

print(f"\n{sum(r)}/{len(r)} passed")
sys.exit(0 if all(r) else 1)
