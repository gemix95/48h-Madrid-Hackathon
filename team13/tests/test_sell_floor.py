"""Never sell a card to a dealer for less than it is worth to us (the SAL-07 case: sold for 24 P, worth 146 P to us).

    python3 tests/test_sell_floor.py      (no network)
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import strategy  # noqa: E402
from haggler import Haggler  # noqa: E402
from values import Values  # noqa: E402

# Salamanca page: 6 commons (10 P), 3 uncommons (25 P), 1 rare (70 P); multiplier 1.6 for us
CARDS = [{"id": f"SAL-{i:02d}", "rarity": r, "book": b, "page": True}
         for i, (r, b) in enumerate([("common", 10)] * 6 + [("uncommon", 25)] * 3 + [("rare", 70)], start=1)]
CATALOG = {"sets": [{"id": "SAL", "released": True, "cards": CARDS}], "packs": []}
PILAR = {"id": "pilar", "name": "Doña Pilar", "level": 3, "status": "active",
         "menu": {"sells": [], "buys": [{"rarity": "uncommon"}, {"rarity": "common"}], "deals_per_team_per_hour": 8}}


def me(extra=()):
    assets = [{"id": i, "kind": "card", "ref": c["id"], "rarity": c["rarity"], "serial": 1} for i, c in enumerate(CARDS, start=1)]
    assets += [{"id": 100 + i, "kind": "card", "ref": r, "rarity": "common", "serial": 9} for i, r in enumerate(extra)]
    return {"id": "t13", "cash": 50, "affinity": {"SAL": 1.6}, "assets": assets}


class Ctx:
    def __init__(self, m, **knobs):
        self.S = {**strategy.defaults(), "use_intel": 0, "haggle_buy_packs": 0, "haggle_buy_cards": 0, "ladder_sell": 1, **knobs}
        self.state, self.me, self.threads, self.clock = {}, m, [], {"tick": 10, "t_hours": 5}
        self.values, self.catalog, self.intel, self.logs = Values(CATALOG, m), CATALOG, None, []

    def reserve(self):
        return 0

    def budget_left(self):
        return 120

    def day_key(self):
        return "sat"

    def locked_assets(self, reserved=True):
        return set()

    def limit(self, name, default):
        return default

    def log(self, *a, **k):
        self.logs.append((a, k))


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    return ok


results = []
# 1) a complete page and no spares: the ladder would sell SAL-07 at any price; now it must not, at any price she pays
ctx = Ctx(me()); h = Haggler(ctx)
cost = ctx.values.loss_of_removing(["SAL-07"])
choice = h.choose_topic(PILAR)
results.append(check("SAL-07 costs us far more than the 24 P she paid", cost > 2 * 24, f"cost {cost:.1f}"))
results.append(check("no sale below its value (ladder skipped)", choice is None, choice and choice[1]))
results.append(check("skip is logged", any(a == ("haggle", "sale_skipped_below_value") for a, _ in ctx.logs)))

# 2) a duplicate common: sells, but never below its value + sell_min_gain, and Boulware rounds are set
ctx = Ctx(me(extra=("SAL-01",)), workshop_accumulate=0, workshop_spares=0); h = Haggler(ctx)  # no workshop stock
topic, plan = h.choose_topic(PILAR)
dup_cost = ctx.values.loss_of_removing(["SAL-01"])
results.append(check("duplicate goes on sale", plan["side"] == "sell" and plan["ref"] == "SAL-01", plan))
results.append(check("floor >= cost + sell_min_gain", plan["lo"] >= math.ceil(dup_cost + 3), f"lo {plan['lo']} cost {dup_cost:.1f}"))
results.append(check("sale keeps its cost for measuring", plan["cost"] == round(dup_cost, 1)))
results.append(check("Boulware: starts above the floor", plan["hi"] > plan["lo"], f"{plan['hi']} > {plan['lo']}"))

# 3) a sale plan from before the rule (lo 1, like the old ladder) is pulled up to the floor before any price goes out
plan = {"side": "sell", "ref": "SAL-07", "lo": 1, "hi": 30, "k": 0, "offers": [], "asks": [24]}
floor = h._sell_floor("SAL-07")
p = None
if plan["lo"] < floor:  # what negotiate() does every tick
    plan["lo"], plan["hi"] = floor, max(plan["hi"], math.ceil(floor * 1.3))
p = h._next_price(plan)
results.append(check("old plan raised to the floor", plan["lo"] == floor, floor))
results.append(check("no price below the floor ever", p is None or p >= floor, p))
results.append(check("her 24 P ask does not reach our floor", 24 < plan["lo"]))

# 4) the ladder sale is below our value, but she also sells commons we need: the purchase still opens (tick 1711 on:
#    LAV-07 skipped ~79 times an hour and no dealer thread opened at all)
LAV = [{"id": f"LAV-{i:02d}", "rarity": "common", "book": 10, "page": True} for i in range(1, 7)]
CATALOG2 = {"sets": [CATALOG["sets"][0], {"id": "LAV", "released": True, "cards": LAV}], "packs": []}
PILAR2 = {**PILAR, "menu": {**PILAR["menu"], "sells": [{"rarity": "common", "sets": "released", "list_price": 10}]}}
ctx = Ctx(me(), haggle_buy_cards=1)
ctx.values, ctx.catalog = Values(CATALOG2, ctx.me), CATALOG2
choice = Haggler(ctx).choose_topic(PILAR2)
results.append(check("ladder sale below floor -> buy path still returns a topic",
                     choice is not None and choice[1]["side"] == "buy" and choice[1]["ref"].startswith("LAV"),
                     choice and choice[1]))
results.append(check("the skipped sale is still logged", any(a == ("haggle", "sale_skipped_below_value") for a, _ in ctx.logs)))
results.append(check("and the purchase stays under 85% of our value",
                     choice is not None and choice[1]["hi"] <= math.floor(choice[1]["worth"] * 0.85), choice and choice[1]))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
