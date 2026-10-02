"""Learning from every dealer conversation in the game: ours and every rival team's (public feed).

Prices are normalised by the dealer's opening ask, so packs, commons and uncommons teach each other.

What we learn, refreshed every few ticks:
  opening   which first offer (share of the dealer's opening) leads to the lowest price that still closes a deal
  response  how many primas the dealer gives back for each prima we move, and after how many rounds she names a final
  finals    when a final offer is worth taking: what teams that walked away from a final paid next time
  ours      how our deals compare with everyone else's
Then two things act on it:
  - the haggler opens at the learned first offer, explored by a small bandit (UCB1) over our own results, and takes a
    final offer up to the learned threshold;
  - Claude gets the lessons as plain sentences in every dealer negotiation.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict

BINS = [(0.0, 0.3), (0.3, 0.45), (0.45, 0.6), (0.6, 0.8), (0.8, 1.01)]
ARMS = [-0.08, 0.0, 0.08]   # bandit: first offer around the learned one (share of the dealer's opening)
MIN_N = 4                    # conversations a bin needs before we trust it


def _first(xs):
    return xs[0][1] if xs else None


class Learner:
    def __init__(self):
        self.model = {"n": 0}

    # ------------------------------------------------------------------ fit
    def fit(self, threads: list, me: str, now_tick: int = None) -> dict:
        rows = []
        now = now_tick if now_tick is not None else max((t.get("last", 0) for t in threads), default=0)
        for t in threads:
            if t["beginner"] or not t["cls"].startswith("buy") or not t["asks"] or not t["offers"]:
                continue
            if not (t["deal"] or t["final"] or t.get("closed") or now - t.get("last", now) >= 10):
                continue  # still being negotiated: not a failure yet
            opening = t["asks"][0][1]
            if not opening:
                continue
            first = t["offers"][0][1]
            rows.append({"team": t["team"], "cls": t["cls"], "opening": opening, "first_r": first / opening,
                         "deal": t["deal"], "price_r": (t["deal"] / opening) if t["deal"] else None,
                         "rounds": len(t["offers"]), "final": t["final"], "opened": t["opened"],
                         "offers": [p for _, p in t["offers"]], "asks": [p for _, p in t["asks"]]})
        m = {"n": len(rows), "deals": sum(1 for r in rows if r["deal"])}
        if not rows:
            self.model = m
            return m

        # opening policy: deal rate x (1 - price) per bin of first offers, shrunk toward the overall mean
        # score per bin = average capture of its deals x (0.5 + 0.5 x deal rate): price first, since only our best
        # three deals per level count and a conversation that fails costs time, not points
        def bin_score(rs):
            deals = [1 - r["price_r"] for r in rs if r["deal"]]
            return (statistics.mean(deals) if deals else 0.0) * (0.5 + 0.5 * len(deals) / len(rs))
        mu = bin_score(rows)
        bins = []
        for lo, hi in BINS:
            rs = [r for r in rows if lo <= r["first_r"] < hi]
            if not rs:
                continue
            shrunk = (bin_score(rs) * len(rs) + MIN_N * mu) / (len(rs) + MIN_N)
            deals = [r["price_r"] for r in rs if r["deal"]]
            bins.append({"from": lo, "to": hi, "n": len(rs), "deal_rate": round(len(deals) / len(rs), 2),
                         "price_vs_opening": round(statistics.mean(deals), 3) if deals else None, "score": round(shrunk, 3)})
        best = max((b for b in bins if b["n"] >= MIN_N), key=lambda b: b["score"], default=None)
        m["bins"], m["best_first"] = bins, (round((best["from"] + min(best["to"], 1.0)) / 2, 2) if best else None)

        # response: primas she gives back per prima we move (consecutive rounds)
        ratios, final_rounds = [], []
        for r in rows:
            o, a = r["offers"], r["asks"]
            for i in range(1, min(len(o), len(a))):
                step = o[i] - o[i - 1]
                if step > 0:
                    ratios.append((a[i - 1] - a[i]) / step)
            if r["final"]:
                final_rounds.append(r["rounds"])
        m["give_back_per_prima"] = round(statistics.median(ratios), 2) if ratios else None
        m["rounds_to_final"] = statistics.median(final_rounds) if final_rounds else None

        # finals: accepted vs walked, and what walkers paid next in the same class
        finals = [r for r in rows if r["final"]]
        walked = [r for r in finals if not r["deal"]]
        regret = []
        for w in walked:
            nxt = sorted((r for r in rows if r["team"] == w["team"] and r["cls"] == w["cls"] and r["opened"] > w["opened"] and r["deal"]),
                         key=lambda r: r["opened"])
            if nxt:
                regret.append(nxt[0]["price_r"] - w["final"] / w["opening"])  # >0: walking away cost them
        m["finals"] = {"seen": len(finals), "walked": len(walked),
                       "walk_then_paid_more": sum(1 for x in regret if x > 0), "walk_then_paid_less": sum(1 for x in regret if x < 0),
                       "avg_regret_vs_opening": round(statistics.mean(regret), 3) if regret else None}
        deal_ratios = sorted(r["price_r"] for r in rows if r["deal"])
        q = 0.75 if (m["finals"]["avg_regret_vs_opening"] or 0) > 0 else 0.5  # walking away tends to cost: accept more finals
        m["final_max_vs_opening"] = round(deal_ratios[min(len(deal_ratios) - 1, int(q * len(deal_ratios)))], 3) if deal_ratios else None
        m["final_quantile"] = q

        # us vs everyone
        ours = [r["price_r"] for r in rows if r["team"] == me and r["deal"]]
        others = [r["price_r"] for r in rows if r["team"] != me and r["deal"]]
        per_team = defaultdict(list)
        for r in rows:
            if r["deal"]:
                per_team[r["team"]].append(r["price_r"])
        ranking = sorted(((statistics.mean(v), t) for t, v in per_team.items() if len(v) >= 1))
        m["ours"] = {"deals": len(ours), "price_vs_opening": round(statistics.mean(ours), 3) if ours else None,
                     "others_price_vs_opening": round(statistics.mean(others), 3) if others else None,
                     "rank": next((i + 1 for i, (_, t) in enumerate(ranking) if t == me), None), "teams_ranked": len(ranking)}
        m["lessons"] = self.lessons(m)
        self.model = m
        return m

    def lessons(self, m) -> list:
        out = []
        if m.get("best_first"):
            b = next(b for b in m["bins"] if b["from"] <= m["best_first"] < b["to"])
            out.append(f"Across {m['n']} real conversations, first offers around {round(m['best_first'] * 100)}% of the dealer's opening "
                       f"did best: {round(b['deal_rate'] * 100)}% closed, at {round((b['price_vs_opening'] or 0) * 100)}% of the opening.")
        if m.get("give_back_per_prima") is not None:
            out.append(f"The dealer gives back about {m['give_back_per_prima']} P for each 1 P we move; "
                       f"she names a final after ~{m.get('rounds_to_final') or '?'} of our offers.")
        f = m.get("finals") or {}
        if f.get("walked"):
            out.append(f"{f['walked']} of {f['seen']} final offers were refused; teams that walked then paid more "
                       f"{f['walk_then_paid_more']} times and less {f['walk_then_paid_less']} times.")
        return out

    # ------------------------------------------------------------------ act
    def opening_for(self, state: dict, cls: str):
        """(first offer as share of the dealer's opening, arm index) from the learned policy + UCB1 over our results."""
        base = self.model.get("best_first")
        if base is None:
            return None, None
        stats = state.setdefault("bandit", {}).setdefault(cls, [[0, 0.0] for _ in ARMS])
        total = sum(n for n, _ in stats) + 1
        best, arm = -1, 0
        for i, (n, s) in enumerate(stats):
            ucb = float("inf") if n == 0 else s / n + math.sqrt(2 * math.log(total) / n) * 0.3
            if ucb > best:
                best, arm = ucb, i
        return max(0.1, min(0.95, base + ARMS[arm])), arm

    @staticmethod
    def reward(state: dict, cls: str, arm, price, opening):
        """Our own result: 1 - price/opening when we closed, 0 when the dealer walked."""
        if arm is None:
            return
        stats = state.setdefault("bandit", {}).setdefault(cls, [[0, 0.0] for _ in ARMS])
        stats[arm][0] += 1
        stats[arm][1] += (1 - price / opening) if price and opening else 0.0
