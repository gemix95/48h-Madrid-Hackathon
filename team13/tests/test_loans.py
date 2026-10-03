"""Loan desk, offline: every risk rule rejects, a loan goes offered -> active -> repaid (or defaulted) even when the
settlement lands a tick after the offer disappears, and the guard leaves the repayment offer alone.

    python3 tests/test_loans.py
"""
import json, os, sys, tempfile
from collections import Counter
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import strategy  # noqa: E402
import loans  # noqa: E402
from guard import Guard  # noqa: E402


class Vals:
    cards = {"LAV-09": {}, "SAL-02": {}}
    worth = {"LAV-09": 80.0, "SAL-02": 30.0}

    def __init__(self):
        self.assets = [{"id": 5, "ref": "SAL-02", "kind": "card"}]
        self.held = Counter(a["ref"] for a in self.assets)

    def give(self, asset):
        self.assets.append(asset)
        self.held = Counter(a["ref"] for a in self.assets)

    def take(self, asset_id):
        self.assets = [a for a in self.assets if a["id"] != asset_id]
        self.held = Counter(a["ref"] for a in self.assets)

    def gain_of_adding(self, refs):
        return sum(self.worth[r] for r in refs)

    def loss_of_removing(self, refs):
        return sum(self.worth[r] for r in refs)


class Ctx:
    def __init__(self):
        self.me = {"id": "t13", "cash": 150, "level": 3}
        self.state, self.S, self.clock = {"venue": "v03"}, strategy.defaults(), {"tick": 100}
        self.values, self.my_offers, self.venues, self.leaderboard = Vals(), [], [], []
        self.logs, self.posted, self.cancelled, self.next_id = [], [], [], 900
        self.api = self.raw = self

    def log(self, module, action, **k):
        self.logs.append((action, k))

    def reserve(self):
        return 40

    def is_untrusted(self, who):
        return who == "t66"

    def list_offer(self, give, want, venue=None, to=None, expires_in_ticks=40):
        self.next_id += 1
        o = {"id": self.next_id, "maker": "t13", "give": give, "want": want, "venue": venue, "to": to, "status": "open"}
        self.my_offers.append(o)
        self.posted.append(o)
        return o

    def cancel(self, i):
        self.cancelled.append(i)


def actions(ctx):
    return [a for a, _ in ctx.logs]


def run():
    tmp = tempfile.mkdtemp()
    loans.REQUESTS = os.path.join(tmp, "req.jsonl")

    def request(**r):
        with open(loans.REQUESTS, "a") as f:
            f.write(json.dumps(r) + "\n")

    ctx = Ctx()
    desk = loans.LoanDesk(ctx)
    bad = [dict(team="t07", ref="SAL-02", principal=30, repay=40),    # collateral worth 30 < 30 x 1.1
           dict(team="t07", ref="LAV-09", principal=60, repay=63),    # interest below 10%
           dict(team="t07", ref="LAV-09", principal=80, repay=90),    # over the cap and the collateral margin
           dict(team="t13", ref="LAV-09", principal=50, repay=60),    # ourselves
           dict(team="t66", ref="LAV-09", principal=50, repay=60),    # untrusted
           dict(team="t07", ref="XXX-01", principal=10, repay=20)]    # unknown card
    for r in bad:
        request(**r)
    desk.step()
    assert actions(ctx).count("rejected") == len(bad) and not ctx.posted, ctx.logs
    ctx.me["cash"] = 80
    request(team="t07", ref="LAV-09", principal=50, repay=60)          # cash 80 - 50 < reserve 40
    desk.step()
    assert actions(ctx)[-1] == "rejected" and "reserve" in ctx.logs[-1][1]["why"]
    ctx.me["cash"] = 150

    # a good loan: offered, accepted (offer gone), card arrives a tick later -> repayment offer posted
    request(team="t07", ref="LAV-09", principal=50, repay=56, term=120)
    desk.step()
    issue = ctx.posted[-1]
    assert issue["give"] == {"cash": 50} and issue["want"] == {"cards": ["LAV-09"]} and issue["to"] == "t07"
    assert issue["venue"] == "rastro"
    desk.step()  # same tick: nothing changes
    ctx.my_offers.remove(issue)
    for t in (101, 102, 103):  # accepted, settlement not yet seen: must not give up
        ctx.clock["tick"] = t
        desk.step()
    assert ctx.state["loans"][str(issue["id"])]["status"] == "offered"
    ctx.values.give({"id": 77, "ref": "LAV-09", "kind": "card"})
    ctx.clock["tick"] = 104
    desk.step()
    L = ctx.state["loans"][str(issue["id"])]
    repay = ctx.posted[-1]
    assert L["status"] == "active" and repay["give"] == {"assets": [77]} and repay["want"] == {"cash": 56}
    assert repay["to"] == "t07"

    # the guard must leave the repayment alone (the card is worth 80 to us, the repayment 56)
    ctx.raw = type("R", (), {"my_offers": lambda _s: {"offers": ctx.my_offers}})()
    ctx.my_offers.append({"id": 555, "maker": "t13", "give": {"assets": [77]}, "want": {"cash": 10}, "status": "open"})
    Guard(ctx).step()
    assert ctx.cancelled == [555], ctx.cancelled  # an ordinary losing offer is cancelled, the repayment is not
    ctx.my_offers.pop()

    # repaid: the offer is accepted, the card leaves a tick later
    ctx.my_offers.remove(repay)
    ctx.clock["tick"] = 105
    desk.step()
    assert L["status"] == "active"
    ctx.values.take(77)
    ctx.clock["tick"] = 106
    desk.step()
    assert L["status"] == "repaid" and actions(ctx)[-1] == "repaid"

    # a second loan that defaults: the repayment offer expires and we keep the card
    request(team="t07", ref="LAV-09", principal=50, repay=56, term=20)
    desk.step()
    issue2 = ctx.posted[-1]
    ctx.my_offers.remove(issue2)
    ctx.values.give({"id": 88, "ref": "LAV-09", "kind": "card"})
    ctx.clock["tick"] = 107
    desk.step()
    L2 = ctx.state["loans"][str(issue2["id"])]
    assert L2["status"] == "active"
    ctx.my_offers.remove(ctx.posted[-1])
    for t in range(108, 113):
        ctx.clock["tick"] = t
        desk.step()
    assert L2["status"] == "defaulted" and any(a["id"] == 88 for a in ctx.values.assets)

    # never taken: the issue offer expires and no card comes
    request(team="t09", ref="LAV-09", principal=40, repay=46)
    desk.step()
    issue3 = ctx.posted[-1]
    ctx.my_offers.remove(issue3)
    for t in range(113, 118):
        ctx.clock["tick"] = t
        desk.step()
    assert ctx.state["loans"][str(issue3["id"])]["status"] == "not_taken"
    print("loans ok:", len(bad) + 1, "rejections, repaid, defaulted, not taken; guard spares the repayment")


if __name__ == "__main__":
    run()
