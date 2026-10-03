"""Two-issue duels (price + delivery days), offline: every priced message carries days, the price never crosses our
limit (days may add value, never excuse a price past the limit), and a rival offer past our limit is never accepted.

    python3 tests/test_duels_days.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import strategy  # noqa: E402
from duels import Duels  # noqa: E402


class Ctx:
    def __init__(self):
        self.me, self.state, self.S, self.clock = {"id": "t13"}, {}, strategy.defaults(), {"tick": 100}
        self.said, self.accepted = [], []
        self.api = self

    def log(self, *a, **k):
        pass

    def take_accept(self, kind="team"):
        return True

    def duel_say(self, i, text, price=None, days=None):
        self.said.append((price, days))

    def duel_accept(self, i):
        self.accepted.append(i)


def duel(role, limit, w, rival_price=None, rival_days=None, rounds=0, deadline=130):
    msgs = []
    if rival_price is not None:
        msgs.append({"tick": 99, "from": "Rival X", "text": f"{rival_price} P, day {rival_days}", "price": rival_price, "days": rival_days})
    return {"duel": 1, "id": 1, "session": 3, "status": "live", "role": role, "issues": ["price", "days"],
            "your_days_weight": w, "your_limit": limit, "deadline_tick": deadline, "decay_per_round": 0.08,
            "rounds": rounds, "messages": msgs, "rival": "Rival X",
            "rival_offer": {"price": rival_price, "days": rival_days} if rival_price is not None else None}


fails = []
# 1) opening move: priced, with days, price on our side of the limit
for role, limit, w in (("seller", 100, 3.0), ("buyer", 100, -3.0), ("seller", 80, 0.2), ("buyer", 120, 0.2)):
    c = Ctx(); Duels(c).play(duel(role, limit, w))
    p, d = c.said[-1]
    ok = d is not None and 0 <= d <= 10 and ((p > limit) if role == "seller" else (p < limit))
    print(f"open {role:6} limit {limit} w {w:+}: price {p} days {d} -> {'ok' if ok else 'FAIL'}"); ok or fails.append(1)
# 2) a rival price past our limit with very attractive days must not be accepted, in the last ticks too
for role, limit, rp, w, rd in (("seller", 100, 92, 5.0, 10), ("buyer", 100, 108, -5.0, 0)):
    c = Ctx(); Duels(c).play(duel(role, limit, w, rp, rd, rounds=7, deadline=101))
    p, d = c.said[-1] if c.said else (None, None)
    ok = not c.accepted and (p is None or ((p > limit) if role == "seller" else (p < limit)))
    print(f"past-limit {role:6} rival {rp} day {rd} w {w:+}: accepted {bool(c.accepted)} our price {p} -> {'ok' if ok else 'FAIL'}"); ok or fails.append(2)
# 3) a rival price inside our limit near the deadline is accepted
c = Ctx(); Duels(c).play(duel("seller", 100, 1.0, 130, 5, rounds=6, deadline=101))
print(f"inside-limit last chance: accepted {bool(c.accepted)} -> {'ok' if c.accepted else 'FAIL'}"); c.accepted or fails.append(3)
# 4) a large days weight must not be given away by meeting halfway
c = Ctx(); Duels(c).play(duel("seller", 80, 7.0, 50, 0, rounds=3, deadline=130))
p, d = c.said[-1] if c.said else (None, None)
ok = (not c.accepted) and d == 10 and p is not None and p > 80
print(f"keep-days w=+7 rival day 0: price {p} days {d} accepted {bool(c.accepted)} -> {'ok' if ok else 'FAIL'}"); ok or fails.append(4)
# 5) last chance against a low rival bid: offer just above our limit so they can take us
c = Ctx(); Duels(c).play(duel("seller", 76, 1.28, 56, 0, rounds=6, deadline=103))
p, d = c.said[-1] if c.said else (None, None)
ok = (not c.accepted) and d == 10 and p is not None and 77 <= p <= 90
print(f"last-chance takeable: price {p} days {d} -> {'ok' if ok else 'FAIL'}"); ok or fails.append(5)

# 6) Saturday Duels II: the API's your_days_weight is positive for both roles, but each day costs the buyer
#    (buyer 5629: limit 148, price 140, 10 days at 7.99 scored -60.8). A buyer with w=+8 asks for day 0, and never
#    accepts a price inside its limit at day 10 when the days cost more than the margin.
c = Ctx(); Duels(c).play(duel("buyer", 148, 7.99))
p, d = c.said[-1]
print(f"buyer w=+7.99 opening: price {p} days {d} -> {'ok' if d == 0 else 'FAIL'}"); d == 0 or fails.append(6)
c = Ctx(); Duels(c).play(duel("buyer", 148, 7.99, 140, 10, rounds=7, deadline=101))
print(f"buyer rival 140 at day 10 (scores 8 - 79.9): accepted {bool(c.accepted)} -> {'ok' if not c.accepted else 'FAIL'}"); c.accepted and fails.append(7)
c = Ctx(); Duels(c).play(duel("seller", 100, 2.0))
p, d = c.said[-1]
print(f"seller w=+2 opening: days {d} -> {'ok' if d == 10 else 'FAIL'}"); d == 10 or fails.append(8)
print("ALL OK" if not fails else f"FAILED {fails}")
sys.exit(1 if fails else 0)
