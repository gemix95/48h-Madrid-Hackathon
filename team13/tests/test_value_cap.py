"""Never pay a dealer more than the item is worth to us: the value band (open buy_open_margin under our value, cap at
(1 - buy_value_margin) of it), the skip when no team ever got a pack that cheap, and the Boulware curve reaching the
cap at the round this dealer usually names its final (mean over every team's conversations with it).

    python3 tests/test_value_cap.py      (no network)
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import strategy  # noqa: E402
from haggler import Haggler  # noqa: E402

PACK = {"id": "sobre_barrio", "slots": [{"common": 1.0}, {"common": 1.0}, {"common": 0.75, "uncommon": 0.25}]}
ABUELA = {"id": "abuela", "name": "Abuela Carmen", "status": "active", "menu": {
    "sells": [{"pack": "sobre_barrio", "list_price": 26, "opening_ask": 30, "per_team_per_hour": 3},
              {"rarity": "uncommon", "sets": "released", "list_price": 25}],
    "buys": [], "deals_per_team_per_hour": 8}}


class Values:
    def __init__(self, pack_worth, wish):
        self.pw, self.wish = pack_worth, wish
        self.cards = {"SAL-08": {"rarity": "uncommon", "book": 25}}
        self.assets = []

    def pack_ev(self, pack):
        return self.pw

    def wishlist(self, limit=12):
        return self.wish

    def spares(self):
        return []


class Intel:
    def __init__(self, best=None, rounds=None):
        self.best, self.r = best, rounds

    def rounds(self, dealer, team=None):
        return self.r or {}

    def has_beginner_price(self, dealer):
        return False

    def advice(self, dealer, cls):
        return {"best": self.best, "median": self.best + 2, "opening": 30, "n": 9} if self.best else {}


class Ctx:
    def __init__(self, pack_worth=13.0, wish=(("SAL-08", 40.0),), best=None, rounds=(5, 6, 5, 6), every_team={"mean": 5.3, "n": 74}):
        self.S = {**strategy.defaults(), "use_intel": 1}
        self.state = {"dealer_stats": {"abuela:buy:sobre_barrio": {"deals": [21], "walked": 0, "rounds": list(rounds)}},
                      "abuela_visit_hours": 5}
        self.me, self.threads, self.clock = {"cash": 300}, [{"id": 1, "kind": "persona", "with": "abuela", "status": "deal"}], {"tick": 10, "t_hours": 5}
        self.catalog, self.values, self.intel = {"packs": [PACK]}, Values(pack_worth, list(wish)), Intel(best, every_team)
        self.me["unlocked"], self.dealers, self.said = ["abuela"], [ABUELA], []

    def reserve(self):
        return 0

    def locked_assets(self):
        return set()

    def budget_left(self):
        return 120

    def day_key(self):
        return "sat"

    def limit(self, name, default):
        return default

    def log(self, *a, **k):
        pass

    def speak(self, situation, band, fallback):
        return fallback[0], fallback[1], "rules"

    @property
    def api(self):
        ctx = self

        class Api:
            def open_thread(self, dealer, topic):
                return {"id": 7}

            def thread(self, tid):
                return {"id": tid, "status": "open", "standing_offers": [], "messages": [], "topic": None}

            def say(self, tid, text, price):
                ctx.said.append(price)
        return Api()


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    return ok


results = []
# 1) a pack worth ~13 to us: cap at min(13 - 3, 85% of 13) = 10, open at or under 40% of 13; she names her final
#    after ~5.3 offers (every team), so our 5th offer (index 4) is the cap
ctx = Ctx(); h = Haggler(ctx)
topic, plan = h.choose_topic(ABUELA)
results.append(check("pack is the first topic", topic == {"buy": {"pack": "sobre_barrio"}}, topic))
results.append(check("pack cap never above our value", plan["hi"] <= math.floor(13 * 0.85) and plan["hi"] <= 13 - 3, f"hi={plan['hi']}"))
results.append(check("pack opens at or under 40% of our value", plan["lo"] <= math.floor(13 * 0.4), f"lo={plan['lo']}"))
results.append(check("rounds from every team (not just ours): mean 5.3 -> cap at index 4", plan["rounds"] == 4, plan["rounds"]))
results.append(check("plan keeps what it is worth to us", plan["worth"] == 13.0))

# 2) the curve concedes slowly, reaches the cap exactly at that round, and never goes above it
plan.update(k=0, offers=[], asks=[30])
prices = []
for _ in range(10):
    p = h._next_price(plan)
    if p is None:
        break
    prices.append(p); plan["offers"].append(p); plan["k"] += 1
results.append(check("never above the cap", max(prices) <= plan["hi"], prices))
results.append(check("reaches the cap at the usual round", prices[plan["rounds"]] == plan["hi"] if len(prices) > plan["rounds"] else prices[-1] == plan["hi"], prices))
results.append(check("Boulware: small steps first", prices[1] - prices[0] <= prices[-1] - prices[-2] or len(prices) < 3, prices))

# 3) no team ever got one for 10 or less (best 19): don't open a pack haggle at all, go for the single card
ctx = Ctx(best=19); h = Haggler(ctx)
topic, plan = h.choose_topic(ABUELA)
results.append(check("pack skipped when nobody ever got it that cheap", topic != {"buy": {"pack": "sobre_barrio"}}, topic))

# 4) a single card worth 40 to us, list 25: the list price binds (25), the opening is the lower of the two rules
results.append(check("card topic", topic == {"buy": {"card": "SAL-08"}}, topic))
results.append(check("card cap: min(list, 85% of value)", plan["hi"] == min(25, math.floor(40 * 0.85)), plan["hi"]))
results.append(check("card opening near prices that close, not a lowball",
                     plan["lo"] == max(min(math.floor(25 * ctx.S["haggle_open"]), math.floor(40 * 0.4)), math.floor(19 * 0.75)),
                     plan["lo"]))

# 5) a card worth only 12, and nobody ever closed under 19: do not open. Walking burns the hourly quota.
ctx = Ctx(best=19, wish=(("SAL-08", 12.0),)); h = Haggler(ctx)
results.append(check("card skipped when our cap cannot reach any real close", h.choose_topic(ABUELA) is None))

# 6) nobody has finished a conversation with this dealer yet: the knob
ctx = Ctx(every_team={}); h = Haggler(ctx)
results.append(check("rounds fall back to haggle_rounds", h._rounds("abuela") == int(ctx.S["haggle_rounds"]), h._rounds("abuela")))

# 7) opening the conversation keeps those rounds (a patient dealer has no strict pace to override them)
ctx = Ctx(); h = Haggler(ctx); h.step()
plan = ctx.state["plans"]["7"]
results.append(check("opened conversation keeps every team's rounds", plan["rounds"] == 4, plan["rounds"]))
results.append(check("first offer sent at the opening", ctx.said == [plan["lo"]], ctx.said))

# 8) a strict dealer: the sooner of its strict pace (4) and its usual final
ABUELA["traits"] = {"strictness": 0.8}
ctx = Ctx(every_team={"mean": 3.4, "n": 9}); h = Haggler(ctx); h.step()
results.append(check("strict dealer: the sooner of pace and usual final", ctx.state["plans"]["7"]["rounds"] == 2, ctx.state["plans"]["7"]["rounds"]))
del ABUELA["traits"]

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
