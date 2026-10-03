"""Auctions on our market. A seller posts an ask on our venue (its price is the reserve) and registers it as a lot
for N ticks. Until the lot ends our broker does not match it; on its last tick the broker gives it to the highest
bid for that card on our book that covers the reserve, at the second-highest bid + 1 (never below the reserve, never
above the winner's bid): everyone bids what the card is worth to them.

The lots live in one JSON file the dashboard writes (registration) and the broker reads (closing). Stdlib only: the
broker runs from its own folder with copies of this file, smart_broker.py and bazaar_sdk.py.
"""
from __future__ import annotations

import json
import os
import time

LOTS = os.environ.get("AUCTION_LOTS", "/home/bazaar/app/team13/logs/auctions.json")
MIN_TICKS, MAX_TICKS = 8, 60


def load(path=LOTS) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(lots: dict, path=LOTS) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(lots, f)
    os.replace(tmp, path)


def _card(side: dict):
    assets = [a for a in side.get("assets") or [] if isinstance(a, dict)]
    return assets[0].get("ref") if len(assets) == 1 else None


def _wanted(side: dict):
    t = [x[5:] for x in side.get("types") or [] if x.startswith("card:")] + [a.get("ref") for a in side.get("assets") or [] if isinstance(a, dict)]
    return t[0] if len(t) == 1 else None


def register(offer_id: int, book: list, tick: int, ticks: int, path=LOTS) -> dict:
    """Make an ask resting on our market a lot. Returns the lot, or {"error": why}."""
    o = next((x for x in book if x.get("id") == offer_id), None)
    if not o:
        return {"error": f"offer {offer_id} is not on our market's book"}
    g, w = o.get("give") or {}, o.get("want") or {}
    ref = _card(g)
    if not ref or g.get("cash") or not w.get("cash") or _wanted(w):
        return {"error": "a lot is an ask for one card against cash"}
    if o.get("to"):
        return {"error": "a lot must be open to everyone (no 'to')"}
    ticks = max(MIN_TICKS, min(MAX_TICKS, int(ticks or 30)))
    end = tick + ticks
    if o.get("expires_tick") and o["expires_tick"] - 1 < end:
        end = o["expires_tick"] - 1  # the lot must close before the ask expires
    if end - tick < MIN_TICKS:
        return {"error": f"the ask expires too soon: post it with expires_in_ticks of at least {MIN_TICKS + 2}"}
    lots = load(path)
    lot = {"offer": offer_id, "ref": ref, "reserve": w["cash"], "start": tick, "end": end, "status": "open", "at": time.time()}
    lots[str(offer_id)] = lot
    save(lots, path)
    return lot


def standing(lot: dict, book: list) -> dict:
    """The lot's bids on our book (offers to buy that card at or above the reserve, not the seller's own), best first."""
    seller = next((o.get("maker") for o in book if o.get("id") == lot["offer"]), None)
    bids = sorted((o for o in book if _wanted(o.get("want") or {}) == lot["ref"] and not _card(o.get("give") or {})
                   and (o.get("give") or {}).get("cash", 0) >= lot["reserve"] and (seller is None or o.get("maker") != seller)),
                  key=lambda o: -o["give"]["cash"])
    return {"bids": len(bids), "best": bids[0]["give"]["cash"] if bids else None, "ids": [o["id"] for o in bids]}


def clearing(lot: dict, book: list):
    """(winning bid id, price) for a lot on its last tick, or None when no bid covers the reserve."""
    s = standing(lot, book)
    if not s["ids"]:
        return None
    bids = [next(o for o in book if o["id"] == i) for i in s["ids"]]
    top = bids[0]["give"]["cash"]
    second = bids[1]["give"]["cash"] if len(bids) > 1 else None
    price = max(lot["reserve"], (second + 1) if second is not None else lot["reserve"])
    return s["ids"][0], min(price, top)


def broker_plan(plan: list, book: list, tick: int, path=LOTS) -> list:
    """Hold back every match of an open lot; on a lot's last tick add its sale. Returns the new plan."""
    lots = load(path)
    if not lots:
        return plan
    live = {o.get("id") for o in book}
    held = {int(k) for k, lot in lots.items() if lot.get("status") == "open"}
    out = [(s, b, p) for s, b, p in plan if s not in held]
    changed = False
    for k, lot in lots.items():
        if lot.get("status") != "open":
            continue
        if int(k) not in live:
            lot["status"] = "gone"  # sold elsewhere, cancelled or expired
            changed = True
            continue
        if tick >= lot["end"] - 1:
            win = clearing(lot, book)
            lot["status"] = "closing" if win else "unsold"
            if win:
                lot.update(winner=win[0], price=win[1], closed_tick=tick)
                out.append((int(k), win[0], win[1]))
            changed = True
    if changed:
        save(lots, path)
    return out
