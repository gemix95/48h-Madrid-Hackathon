"""Auctions on our market, with nothing to trust us for. The seller keeps the card: no ask is posted, so nobody can
buy it around the auction. The seller announces a lot (card, reserve, length); buyers post ordinary open bids for that
card on our market, visible to everyone in GET /api/venues/{vid}/offers. When the lot ends we publish the ranking of the
bids standing on the book at that tick (offer id and price, best first, ties to the earlier offer); the seller then
accepts the best one still on the book (POST /api/offers/{id}/accept with the card). The winner pays its own bid.

The lots live in one JSON file the dashboard writes (registration, closing) and the board and market.py read.
Stdlib only.
"""
from __future__ import annotations

import json
import os
import time

LOTS = os.environ.get("AUCTION_LOTS", "/home/bazaar/app/team13/logs/lots.json")
MIN_TICKS, MAX_TICKS = 8, 60
ACCEPT_TICKS = 8     # after the end, the seller has this long to accept the winning bid
MAX_OPEN = 6         # open lots at once, all cards
RULES = ("The seller keeps the card until the end: there is no ask to buy around the auction. Bid by posting an "
         "ordinary open bid for the card on {vid} (cash for the card, at least the reserve). Every bid is public in "
         "GET /api/venues/{vid}/offers. A bid counts if it is still on the book on the closing tick; raise it with a "
         "new bid, withdraw it by cancelling. On the closing tick we publish the ranking (offer id, price; best first, "
         "a tie goes to the earlier offer) at /board/lots.json. The seller then accepts the best bid still on the book "
         "within {grace} ticks, and the winner pays its own bid, 0% fee. If the seller does not accept, nothing trades "
         "and every bid stays yours to cancel.")


def load(path=LOTS) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(lots: dict, path=LOTS) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(lots, f, indent=1)
    os.replace(tmp, path)


def _team(x) -> bool:
    return bool(x) and str(x)[:1] == "t" and str(x)[1:].isdigit()


def register(ref: str, reserve, ticks, seller: str, tick: int, refs, path=LOTS) -> dict:
    """Open a lot. Returns the lot, or {"error": why}."""
    ref = str(ref or "").upper()
    if ref not in refs:
        return {"error": f"unknown card {ref!r}"}
    if not _team(seller):
        return {"error": "seller must be your team id, like t07"}
    try:
        reserve, ticks = int(reserve), int(ticks or 30)
    except (TypeError, ValueError):
        return {"error": "reserve and ticks must be whole numbers"}
    if reserve < 1:
        return {"error": "reserve must be at least 1"}
    lots = load(path)
    live = [l for l in lots.values() if l.get("status") == "open"]
    if any(l["ref"] == ref for l in live):
        return {"error": f"{ref} already has an open lot"}
    if len(live) >= MAX_OPEN:
        return {"error": f"{MAX_OPEN} lots are open already; try again when one ends"}
    ticks = max(MIN_TICKS, min(MAX_TICKS, ticks))
    lot_id = str(max([int(k) for k in lots] + [0]) + 1)
    lot = {"lot": int(lot_id), "ref": ref, "reserve": reserve, "seller": seller, "start": tick, "end": tick + ticks,
           "status": "open", "at": time.time()}
    lots[lot_id] = lot
    save(lots, path)
    return lot


def bids_for(lot: dict, book: list) -> list:
    """Open single-card cash bids for the lot's card at or above the reserve: [{id, price}], best first, ties to the
    earlier offer."""
    out = []
    for o in book:
        g, w = o.get("give") or {}, o.get("want") or {}
        wanted = [x[5:] for x in w.get("types") or [] if x.startswith("card:")] + [a.get("ref") for a in w.get("assets") or [] if isinstance(a, dict)]
        if o.get("to") or g.get("assets") or w.get("cash") or wanted != [lot["ref"]]:
            continue
        if (g.get("cash") or 0) >= lot["reserve"]:
            out.append({"id": o["id"], "price": g["cash"]})
    return sorted(out, key=lambda b: (-b["price"], b["id"]))


def update(book: list, tick: int, path=LOTS) -> dict:
    """Close lots whose end has come (publish the ranking), and retire ended lots after the acceptance window."""
    lots = load(path)
    changed = False
    on_book = {o.get("id") for o in book}
    for lot in lots.values():
        if lot["status"] == "open" and tick >= lot["end"]:
            lot.update(status="ended", closed_tick=tick, accept_by=tick + ACCEPT_TICKS, ranking=bids_for(lot, book))
            changed = True
        elif lot["status"] == "ended":
            left = [b["id"] for b in lot["ranking"] if b["id"] not in on_book]
            if left != lot.get("left_book"):
                lot["left_book"] = left  # accepted or cancelled since the close: the public feed tells which
                changed = True
            if tick >= lot["accept_by"]:
                lot["status"] = "closed"
                changed = True
    if changed:
        save(lots, path)
    return lots


def broker_plan(plan, *_, **__):
    """Kept for a broker started before the redesign: lots no longer touch the broker's matching."""
    return plan
