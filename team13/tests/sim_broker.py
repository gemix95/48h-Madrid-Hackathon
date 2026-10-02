"""Monte Carlo: our smart bench plan vs the stall's plan (and a perfect-knowledge oracle) on synthetic Market Tests.

Traders arrive over the session, keep hidden limits, shade quotes 5-25% away from them, relax toward them as
patience runs out (30% are firm and never relax), and leave when patience ends.
Efficiency = realised gains between true limits / maximum possible gains.
    python3 tests/sim_broker.py
"""
import random, sys
sys.path.insert(0, ".")
import smart_broker as sb, starter_plans


def run(seed, strategy, n=10, ticks=16, arrivals=True):
    rnd = random.Random(seed)
    tr = []
    for i in range(n):
        side = "ask" if i % 2 == 0 else "bid"
        arr = rnd.randint(0, 8) if arrivals else 0
        tr.append(dict(id=f"b1-{i}", side=side, lim=rnd.uniform(20, 80), shade=rnd.uniform(.05, .25), arr=arr,
                       pat=arr + rnd.randint(3, 10), firm=rnd.random() < .3, alive=True))
    costs = sorted(t["lim"] for t in tr if t["side"] == "ask")
    vals = sorted((t["lim"] for t in tr if t["side"] == "bid"), reverse=True)
    best = sum(max(0, v - c) for v, c in zip(vals, costs))
    k = sum(1 for v, c in zip(vals, costs) if v > c)
    eff = {t["id"] for t in tr if k and ((t["side"] == "ask" and t["lim"] <= costs[k - 1]) or (t["side"] == "bid" and t["lim"] >= vals[k - 1]))}
    tracker, got = sb.Tracker(), 0.0
    by = {t["id"]: t for t in tr}
    for tick in range(ticks):
        book = []
        for t in tr:
            if not t["alive"] or tick < t["arr"]:
                continue
            if tick >= t["pat"]:
                t["alive"] = False
                continue
            relax = 0 if t["firm"] else (tick - t["arr"]) / max(1, t["pat"] - t["arr"])
            sh = t["shade"] * (1 - relax)
            t["q"] = round(t["lim"] * (1 + sh)) if t["side"] == "ask" else round(t["lim"] * (1 - sh))
            book.append({"id": t["id"], "want": {"cash": t["q"] if t["side"] == "ask" else 0},
                         "give": {"cash": t["q"] if t["side"] == "bid" else 0}})
        tracker.update(tick, book)
        if strategy == "smart":
            plan = sb.smart_bench_plan({}, tracker, tick, lambda p: 0)
        elif strategy == "stall":
            plan = starter_plans.bench_plan({"bench_offers": book})
        else:  # oracle: knows limits and departures
            plan, used = [], set()
            live = [by[o["id"]] for o in book]
            S = sorted([t for t in live if t["side"] == "ask"], key=lambda t: t["q"])
            B = sorted([t for t in live if t["side"] == "bid"], key=lambda t: -t["q"])
            for b in B:
                for s in S:
                    if s["id"] in used or b["id"] in used or b["q"] < s["q"]:
                        continue
                    if (s["id"] in eff and b["id"] in eff) or s["pat"] - tick <= 1 or b["pat"] - tick <= 1:
                        plan.append((s["id"], b["id"], 0))
                        used |= {s["id"], b["id"]}
                        break
        for s, b, p in plan:
            if by[s]["alive"] and by[b]["alive"]:
                got += by[b]["lim"] - by[s]["lim"]
                by[s]["alive"] = by[b]["alive"] = False
    return got / best if best > 0 else 1.0


if __name__ == "__main__":
    N = 3000
    for arrivals in (False, True):
        for strat in ("stall", "smart", "oracle"):
            e = [run(s, strat, arrivals=arrivals) for s in range(N)]
            print(f"arrivals={arrivals!s:5} {strat:6s} {sum(e) / len(e):.3f}")
