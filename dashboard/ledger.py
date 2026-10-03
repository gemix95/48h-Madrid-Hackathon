"""Cash ledger for the dashboard's Ledger tab: every prima that moved for our team.

Built from the public feed (dealers, team trades, the venue bond, organiser grants). Card gifts are not cash.
The venue fee is recorded on the trade; the side that accepts the offer pays it, so it is not added to `out`.
"""
from __future__ import annotations

import re

OPEN_FEE = 20  # primas charged on top of the refundable bond when a team opens a market


def build(events, me: str = "t13") -> dict:
    """{"rows": [...]} oldest first. Each row is one cash movement."""
    rows = []
    seen = set()
    for e in sorted(events, key=lambda e: (e.get("tick") or 0, e.get("id") or 0)):
        row = _row(e, me)
        if not row or row["id"] in seen:
            continue
        seen.add(row["id"])
        rows.append(row)
    return {"rows": rows}


def _row(e: dict, me: str) -> dict | None:
    t, p = e.get("type"), e.get("payload") or {}
    tick = e.get("tick")
    if t == "settlement" and p.get("kind") != "match":
        return _trade(p, tick if p.get("tick") is None else p.get("tick"), me)
    if t == "schedule.fired" and p.get("action") == "grant_all":
        m = re.search(r"(\d+)\s*primas", p.get("note") or "")
        if not m:
            return None
        amt = int(m.group(1))
        return {"id": f"grant-{tick}", "tick": tick, "kind": "grant", "with": None, "where": None,
                "items": [], "price": amt, "fee": 0, "out": 0, "in": amt, "note": p.get("note") or "Allowance"}
    if t == "gift.given" and p.get("team") == me and int(p.get("cash") or 0):
        amt = int(p["cash"])
        return {"id": f"gift-{e.get('id')}", "tick": tick, "kind": "gift", "with": None, "where": None,
                "items": [], "price": amt, "fee": 0, "out": 0, "in": amt, "note": p.get("reason") or "Cash gift"}
    if t == "venue.opened" and (p.get("owner") or p.get("team")) == me:
        bond = int(p.get("bond") or 0)
        cost = bond + OPEN_FEE
        return {"id": f"venue-{p.get('venue')}", "tick": tick, "kind": "bond", "with": p.get("venue"),
                "where": p.get("venue"), "items": [], "price": cost, "fee": 0, "out": cost, "in": 0,
                "note": f"{bond} P bond + {OPEN_FEE} P to open {p.get('name') or 'our market'}"}
    if t == "venue.closed" and ((p.get("owner") or p.get("team")) == me) and int(p.get("refund") or 0):
        amt = int(p["refund"])
        return {"id": f"refund-{p.get('venue')}-{tick}", "tick": tick, "kind": "refund", "with": p.get("venue"),
                "where": p.get("venue"), "items": [], "price": amt, "fee": 0, "out": 0, "in": amt,
                "note": "Venue bond returned"}
    return None


def _trade(p: dict, tick, me: str) -> dict | None:
    items = p.get("items") or []
    got = [it for it in items if it.get("to") == me and it.get("kind") in ("card", "pack")]
    gave = [it for it in items if (it.get("frm") or it.get("from")) == me and it.get("kind") in ("card", "pack")]
    if not got and not gave:
        return None
    price, fee = int(p.get("price") or 0), int(p.get("fee") or 0)
    if got and not gave:
        kind, out, inn = "buy", price, 0
    elif gave and not got:
        kind, out, inn = "sell", 0, price
    else:
        kind, out, inn = "swap", 0, 0
    other = next((x for x in (p.get("parties") or []) if x != me), None) or p.get("persona")
    where = p.get("persona") or p.get("venue")
    packed = [_item(it, "in") for it in got] + [_item(it, "out") for it in gave]
    return {"id": f"s-{p.get('settlement')}", "tick": tick, "kind": kind, "with": other, "where": where,
            "items": packed, "price": price, "fee": fee, "out": out, "in": inn, "note": ""}


def _item(it: dict, direction: str) -> dict:
    return {"ref": it.get("ref"), "name": it.get("name") or it.get("ref"), "kind": it.get("kind") or "card",
            "dir": direction}
