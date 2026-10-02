"""Our Boulware haggler vs the starter's +2 strategy against a synthetic dealer.

The dealer has a secret floor, opens at 30, concedes in proportion to our last step (small steps earn small
steps), never moves if we repeat, and names a final offer at its floor plus a margin when patience runs out.
    python3 tests/sim_haggle.py
"""
import random, sys, math
sys.path.insert(0, ".")
from haggler import Haggler


def dealer_run(seed, strategy, patience=14):
    rnd = random.Random(seed)
    floor = rnd.uniform(14, 22)
    ask, last_ours, rounds = 30.0, None, 0
    plan = {"side": "buy", "lo": 11, "hi": 26, "k": 0, "offers": [], "asks": [30]}
    starter_offer = int(27 * 0.6)
    while rounds < 40:
        rounds += 1
        if strategy == "ours":
            p = Haggler._next_price(None, plan)
            if p is None or ask <= p:
                return round(ask), rounds
            plan["offers"].append(p); plan["k"] += 1
        else:
            if ask <= min(27, starter_offer + 1):
                return round(ask), rounds
            p = starter_offer
            starter_offer = min(27, starter_offer + 2)
        step = 0 if last_ours is None else p - last_ours
        if last_ours is not None and step <= 0:
            pass  # repeated price: no concession
        else:
            conc = (ask - floor) * (0.12 + 0.04 * min(step, 5))
            ask = max(floor, ask - conc)
        last_ours = p
        if p >= ask:
            return round(p), rounds
        if rounds >= patience:
            final = max(floor, (ask + p) / 2)
            return (round(final), rounds) if final <= (26 if strategy == "ours" else 27) else (None, rounds)
        plan["asks"].append(math.ceil(ask))
    return None, rounds


if __name__ == "__main__":
    for strat in ("starter", "ours"):
        res = [dealer_run(s, strat) for s in range(2000)]
        deals = [p for p, _ in res if p is not None]
        print(f"{strat:8s} deal rate {len(deals) / len(res):.0%}  avg price {sum(deals) / len(deals):.1f}  "
              f"avg rounds {sum(r for _, r in res) / len(res):.1f}")
