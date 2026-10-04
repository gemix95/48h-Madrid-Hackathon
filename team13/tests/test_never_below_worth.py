"""No sale below our value in any mode (the Sunday 13:00 case: ladder_mode sold MAL-06 for 12 P, worth 67 to us,
by accepting Los Pícaros' offer, and broke a complete page).

    python3 tests/test_never_below_worth.py      (no network)
"""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from guard import Guard, below_worth  # noqa: E402
from haggler import Haggler  # noqa: E402
import strategy  # noqa: E402
from values import Values  # noqa: E402

# a complete Salamanca page (6 commons, 3 uncommons, 1 rare), no spares; multiplier 1.6, as in test_sell_floor.py
CARDS = [{"id": f"SAL-{i:02d}", "rarity": r, "book": b, "page": True}
         for i, (r, b) in enumerate([("common", 10)] * 6 + [("uncommon", 25)] * 3 + [("rare", 70)], start=1)]
CATALOG = {"sets": [{"id": "SAL", "released": True, "cards": CARDS}], "packs": []}


def me():
    return {"id": "t13", "cash": 50, "affinity": {"SAL": 1.6},
            "assets": [{"id": i, "kind": "card", "ref": c["id"], "rarity": c["rarity"], "serial": 1} for i, c in enumerate(CARDS, start=1)]}


class Ctx:
    def __init__(self, m, **knobs):
        self.S = {**strategy.defaults(), "use_intel": 0, **knobs}
        self.state, self.me, self.threads, self.clock = {}, m, [], {"tick": 10, "t_hours": 5}
        self.values, self.catalog, self.intel, self.logs = Values(CATALOG, m), CATALOG, None, []

    def reserve(self):
        return 0

    def record_spend(self, *a):
        pass

    def log(self, *a, **k):
        self.logs.append((a, k))

ok = True


def check(name, cond, detail=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name + (f"  ({detail})" if detail else ""))


ctx = Ctx(me(), ladder_mode=1)
worth = ctx.values.loss_of_removing(["SAL-07"])
check("SAL-07 breaks the page", worth > 50, f"worth {worth:.1f}")
check("below_worth flags a 12 P sale", below_worth(ctx, ["SAL-07"], 12))
check("below_worth lets a fair sale through", below_worth(ctx, ["SAL-07"], worth + 1) is None)

# 1) the haggler never accepts a dealer's offer below worth, even in ladder_mode
ctx.api = MagicMock()
h = Haggler(ctx)
offer = {"id": 7, "give": {"cash": 12}, "want": {"assets": [7]}}            # asset 7 = SAL-07
th = {"id": 3, "with": "picaros", "topic": {"sell": {"assets": [7]}}}
plan = {"side": "sell", "ref": "SAL-07", "ladder": True, "asks": [12], "key": "picaros:sell:rare"}
h._accept(offer, th, plan, reason="matches the best price any team got (12)")
check("accept refused", not ctx.api.accept.called)
check("refusal logged", any(a == ("haggle", "below_worth_refused") for a, _ in ctx.logs))
ctx.api.accept.reset_mock()
h._accept({**offer, "give": {"cash": int(worth) + 2}}, th, plan, reason="fair")
check("a sale at worth still goes through", ctx.api.accept.called)

# 2) the guard cancels our own ladder sale offer in a dealer thread
ctx.api = MagicMock()
ctx.me["id"] = "t13"
ctx.raw = MagicMock()
ctx.raw.my_offers.return_value = {"offers": [{"id": 9, "maker": "t13", "status": "open", "thread": 3,
                                              "give": {"assets": [7]}, "want": {"cash": 11}}]}
ctx.my_offers, ctx.threads = [], [{"id": 3, "kind": "persona", "topic": {"sell": {"assets": [7]}}}]
ctx.state["plans"] = {"3": plan}
Guard(ctx).step()
check("guard cancels the ladder sale", ctx.api.cancel.called and ctx.api.cancel.call_args[0][0] == 9)

print("never below worth:", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
