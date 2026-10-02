"""Strategy tournament: which settings win, against which kinds of opponents.

    python3 tests/tournament.py            # from team13/

1) DEALERS. Our haggler (the real Haggler._next_price, driven by strategy knobs) against three dealer personalities,
   calibrated on our real Friday threads with Abuela (she opened 12 on a 10-list common, dropped to 10 and held;
   opened 17 on a 25-list uncommon and held):
     reciprocal  concedes in proportion to our step, nothing if we repeat
     drop-hold   jumps most of the way to its floor at once, then holds; final offer at the floor
     midpoint    meets us halfway (never below its floor)
   Each has a secret floor and patience; when patience runs out it names a final offer.
   Score = ladder capture = (dealer's opening - price) / (opening - floor); no deal = 0.

2) DUELS. Our duel bot (the real Duels.play) against four rival styles over random limits, 6% decay per round.
   Score = our share of the pie captured; a deal outside our limit would score negative; no deal = 0.
"""
import itertools
import math
import random
import statistics
import sys

sys.path.insert(0, ".")
import strategy  # noqa: E402
from haggler import Haggler  # noqa: E402
import duels as duels_mod  # noqa: E402


# ------------------------------------------------------------------ dealers
class Stub:
    def __init__(self, S):
        self.S = S


def dealer_episode(rnd, S, persona, impatient=False):
    lst = rnd.choice([10, 25, 26])
    O = lst * rnd.uniform(0.7, 1.2)                     # dealer's opening ask
    F = O * rnd.uniform(0.5, 0.85)                      # secret floor
    patience = rnd.randint(4, 10) if impatient else rnd.randint(8, 22)  # Abuela: patience 0.85; later dealers may be less
    h = Haggler(Stub(S))
    plan = {"side": "buy", "lo": max(1, math.floor(S["haggle_open"] * lst)), "hi": math.floor(lst * S["haggle_cap"]),
            "k": 0, "offers": [], "asks": [math.ceil(O)]}
    ask, last, waited = O, None, 0
    for rnd_i in range(1, 60):
        p = h._next_price(plan)
        cur = math.ceil(ask)
        if cur <= plan["hi"] and (p is None or cur <= p):
            return (O - cur) / (O - F), rnd_i                       # we take her ask
        if p is None:
            waited += 1                                             # at our cap: we wait, she loses patience faster
        else:
            plan["offers"].append(p)
            plan["k"] += 1
            step = p - last if last is not None else 0
            if p >= ask:
                return (O - p) / (O - F), rnd_i
            if persona == "reciprocal":
                if last is None or step > 0:
                    ask = max(F, ask - (ask - F) * (0.15 + 0.05 * min(step, 5)))
            elif persona == "drop-hold":
                ask = max(F, F + (O - F) * 0.25) if rnd_i == 1 else max(F, ask - (ask - F) * 0.1 * (step > 0))
            else:  # midpoint
                if last is None or step > 0:
                    ask = max(F, (ask + p) / 2)
            last = p
        if rnd_i + waited * 2 >= patience:
            final = math.ceil(max(F, min(ask, F + (ask - F) * 0.3)))
            return ((O - final) / (O - F), rnd_i) if final <= plan["hi"] else (0.0, rnd_i)
        plan["asks"].append(math.ceil(ask))
    return 0.0, 60


def eval_dealers(S, n=600, seed=1):
    out = {}
    for persona in ("reciprocal", "drop-hold", "midpoint", "impatient"):
        rnd = random.Random(seed)
        res = [dealer_episode(rnd, S, "reciprocal" if persona == "impatient" else persona, impatient=persona == "impatient")
               for _ in range(n)]
        out[persona] = (statistics.mean(max(0, min(1, c)) for c, _ in res), statistics.mean(r for _, r in res),
                        sum(1 for c, _ in res if c > 0) / n)
    return out


# ------------------------------------------------------------------ duels
class DuelCtx:
    def __init__(self, S):
        self.S, self.me, self.state, self.api = S, {"id": "t13"}, {}, self
        self.said = self.accepted = None

    def log(self, *a, **k):
        pass

    def take_accept(self, kind="team"):
        return True

    def duel_say(self, i, text, price=None, days=None):
        self.said = price

    def duel_accept(self, i):
        self.accepted = True


def rival_move(style, k, lim, seller, ours, rnd, anchor):
    """Rival's next price (it is the opposite side of us)."""
    if style == "tough":         # Boulware: barely moves until late
        x = (k / 8) ** 3
    elif style == "conceder":    # moves fast
        x = (k / 5) ** 0.7
    elif style == "tit-for-tat":
        x = None
    else:                        # clone-like: our own schedule
        x = (k / 6) ** 1.3
    if x is None:
        if ours is None:
            return anchor
        return (anchor + ours) / 2 if seller else (anchor + ours) / 2
    return anchor + (lim - anchor) * min(1, x)


def duel_episode(rnd, S, style):
    we_sell = rnd.random() < 0.5
    a, b = sorted(rnd.uniform(20, 100) for _ in range(2))
    if b - a < 5:
        b = a + 5
    cost, value = a, b                                   # seller cost < buyer value: a deal exists
    our_lim, their_lim = (cost, value) if we_sell else (value, cost)
    their_anchor = their_lim * (0.6 if we_sell else 1.5)  # they buy low / sell high
    ctx = DuelCtx(S)
    D = duels_mod.Duels(ctx)
    msgs, pie, decay = [], value - cost, 0.06
    for k in range(16):
        r = rival_move(style, k, their_lim, not we_sell, ctx.said, rnd, their_anchor)
        r = round(min(r, their_lim) if we_sell else max(r, their_lim))  # a rival never offers past its own limit
        msgs.append({"sender": "rival", "price": r})
        # rival accepts our standing offer if it is inside its limit and at least as good as its own next move
        if ctx.said is not None:
            ours = ctx.said
            ok_rival = (ours <= their_lim) if we_sell else (ours >= their_lim)
            if ok_rival and ((we_sell and ours <= r * 1.02) or (not we_sell and ours >= r * 0.98)):
                return surplus(ours, our_lim, we_sell, pie, decay, k)
        ctx.accepted = None
        D.play({"id": 1, "role": "seller" if we_sell else "buyer", "your_limit": our_lim, "status": "open",
                "messages": msgs, "decay": decay})
        if ctx.accepted:
            return surplus(r, our_lim, we_sell, pie, decay, k)
        msgs.append({"sender": "t13", "price": ctx.said})
    return 0.0


def surplus(price, lim, seller, pie, decay, k):
    s = (price - lim) if seller else (lim - price)
    return (s / pie) * (1 - decay) ** k


def eval_duels(S, n=500, seed=2):
    out = {}
    for style in ("tough", "conceder", "tit-for-tat", "clone"):
        rnd = random.Random(seed)
        out[style] = statistics.mean(duel_episode(rnd, S, style) for _ in range(n))
    return out


# ------------------------------------------------------------------ run
def main():
    base = strategy.defaults()
    print("DEALER LADDER: capture of the dealer's range (higher is better) · deal rate · rounds\n")
    grid = list(itertools.product([0.2, 0.25, 0.3, 0.35, 0.45, 0.6], [6, 10, 14, 18, 24, 30], [1.2, 2.2, 3.2], [1.0]))
    rows = []
    for o, r, c, cap in grid:
        S = {**base, "haggle_open": o, "haggle_rounds": r, "haggle_curve": c, "haggle_cap": cap}
        ev = eval_dealers(S)
        worst = min(v[0] for v in ev.values())
        rows.append((statistics.mean(v[0] for v in ev.values()), worst, o, r, c, cap, ev))
    rows.sort(key=lambda x: -(x[0] + x[1]) / 2)  # robust: average of mean and worst case
    for mean, worst, o, r, c, cap, ev in rows[:6] + [x for x in rows if (x[2], x[3], x[4], x[5]) == (0.45, 12, 2.2, 1.0)]:
        tag = "  <- current default" if (o, r, c, cap) == (0.45, 12, 2.2, 1.0) else ""
        print(f"open {o:.2f} rounds {r:>2} curve {c:.1f} cap {cap:.1f} | mean {mean:.3f} worst {worst:.3f} | " +
              " ".join(f"{k} {v[0]:.2f}/{v[2]:.0%}/{v[1]:.0f}r" for k, v in ev.items()) + tag)

    for name, ov in strategy.PRESETS.items():
        ev = eval_dealers({**base, **ov})
        print(f"preset {name:16s} mean {statistics.mean(v[0] for v in ev.values()):.3f}")

    print("\nDUELS: share of the pie captured (higher is better)\n")
    drows = []
    for rr, an, ac in itertools.product([4, 6, 8, 10, 12], [0.6, 1.0, 1.25, 1.5], [0.6, 0.75, 0.9]):
        S = {**base, "duel_rounds": rr, "duel_anchor": an, "duel_accept": ac}
        ev = eval_duels(S)
        drows.append((statistics.mean(ev.values()), min(ev.values()), rr, an, ac, ev))
    drows.sort(key=lambda x: -(x[0] + x[1]) / 2)
    for mean, worst, rr, an, ac, ev in drows[:6] + [x for x in drows if (x[2], x[3], x[4]) == (6, 0.6, 0.9)]:
        tag = "  <- current default" if (rr, an, ac) == (6, 0.6, 0.9) else ""
        print(f"rounds {rr} anchor {an:.1f} accept {ac:.2f} | mean {mean:.3f} worst {worst:.3f} | " +
              " ".join(f"{k} {v:.2f}" for k, v in ev.items()) + tag)


if __name__ == "__main__":
    main()
