"""Strategy advisor: learns from every team's real deals (intel.py) and proposes a better strategy when it finds one.

1. Calibrate: from the public feed, each haggled dealer deal gives a real opening ask (relative to list), a price the
   dealer accepted (an upper bound on its floor, relative to the opening) and how many rounds it took.
2. Re-run the dealer tournament with dealers drawn from those real observations (half the weight) plus the generic
   personalities (the other half, so a handful of deals cannot overfit us).
3. Propose the best settings only when they beat the current ones by a clear margin, with the evidence.
The dashboard shows the proposal on the Strategy tab with an Apply button; nothing changes without it.
"""
from __future__ import annotations

import itertools
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "tests"))
import strategy  # noqa: E402
import tournament as T  # noqa: E402

MIN_DEALS = 3          # real haggled deals needed before we trust the calibration
MIN_GAIN = 0.015       # +1.5 points of the dealer's range captured, averaged
# Cap goes up to the knob's max (1.2× list). Stopping at 1.0× made every candidate walk away from the
# same above-list closes (rares at ~1.4×, some uncommons at ~1.2×), so the search reported 69% as the ceiling.
GRID = {"haggle_open": [0.15, 0.2, 0.25, 0.3, 0.4, 0.5], "haggle_rounds": [8, 12, 18, 24],
        "haggle_curve": [1.5, 2.2, 3.0], "haggle_cap": [1.0, 1.1, 1.2]}


def calibrate(summary: dict, list_prices: dict) -> dict:
    """One record per real deal: its opening and the price it closed at stay together.

    Sampling those two ratios independently invented dealers whose floor sat above list even when that
    deal had closed under it, and the search then scored a walk-away (0) on about a third of episodes.
    """
    deals = []
    for r in summary.get("dealer_deals", []):
        if r.get("beginner") or not r.get("opening") or not r.get("price") or not r["cls"].startswith("buy"):
            continue
        lst = list_prices.get(r["cls"])
        if not lst:
            continue
        deals.append({"open_ratio": r["opening"] / lst,
                      "price_ratio": min(1.0, r["price"] / r["opening"]),
                      "rounds": int(r.get("rounds") or 0) + 1})
    if len(deals) < MIN_DEALS:
        return {}
    return {"deals": deals,
            "open_ratio": [d["open_ratio"] for d in deals],
            "floor_ratio": [d["price_ratio"] for d in deals],
            "rounds": [d["rounds"] for d in deals],
            "n": len(deals)}


def score(S, calib) -> float:
    generic = T.eval_dealers(S, n=250)
    observed = T.eval_dealers_calibrated(S, calib, n=300)
    return 0.5 * statistics.mean(v[0] for v in generic.values()) + 0.5 * statistics.mean(v[0] for v in observed.values())


def propose(summary: dict, list_prices: dict, current: dict) -> dict:
    t0 = time.time()
    calib = calibrate(summary, list_prices)
    out = {"at": time.time(), "status": "insufficient_data", "deals_used": calib.get("n", 0), "min_deals": MIN_DEALS}
    if not calib:
        return out
    cur = score(current, calib)
    best, best_s = None, cur
    for vals in itertools.product(*GRID.values()):
        cand = {**current, **dict(zip(GRID, vals))}
        s = score(cand, calib)
        if s > best_s:
            best, best_s = cand, s
    out.update(current_score=round(cur, 3), seconds=round(time.time() - t0, 1),
               evidence={"median_floor_vs_opening": round(statistics.median(calib["floor_ratio"]), 2),
                         "median_opening_vs_list": round(statistics.median(calib["open_ratio"]), 2),
                         "median_rounds": statistics.median(calib["rounds"])})
    if best is None or best_s - cur < MIN_GAIN:
        out.update(status="current_is_best", best_score=round(best_s, 3))
        return out
    changes = {k: best[k] for k in GRID if best[k] != current.get(k)}
    out.update(status="proposal", best_score=round(best_s, 3), gain=round(best_s - cur, 3), changes=changes,
               labels={k: strategy.KNOBS[k][5] for k in changes})
    return out
