"""Duels: one rival, one private limit each, and a pie that shrinks every round of talk.

Score = the share of the pie we capture; a deal outside our limit loses points; no deal scores zero.
So: never cross our limit, anchor ambitiously, concede on a schedule that converges within a few rounds,
and accept as soon as waiting would cost more (decay) than it could win.

Two-issue duels (price + delivery day 0-10): each side has a private weight per day. We trade days we care little
about for price: we learn what the rival prefers from the days it proposes and give it those days if they are cheap
for us, while asking for a better price.

The duel payload is only known once a session starts, so every read is defensive and the raw payload is logged
the first time we see a duel (the practice round is for exactly that).
"""
from __future__ import annotations

import math

from bazaar_sdk import BazaarError


def first(d: dict, *keys, default=None):
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return default


class Duels:
    ROUNDS = 6  # we aim to settle within this many of our own messages

    def __init__(self, ctx):
        self.ctx = ctx

    def step(self):
        ctx = self.ctx
        try:
            duels = ctx.api.duels().get("duels", [])
        except BazaarError as e:
            ctx.log("duel", "read_refused", error=str(e))
            return
        seen = ctx.state.setdefault("duels_seen", [])
        for d in duels:
            if str(d.get("id")) not in seen:
                seen.append(str(d.get("id")))
                ctx.log("duel", "raw", payload=d)  # learn the shape during the practice round
            status = d.get("status", "open")
            if status not in ("open", "active", "live", "running", "negotiating"):
                continue
            try:
                self.play(d)
            except BazaarError as e:
                if e.code != "wait_for_tick":
                    ctx.log("duel", "refused", duel=d.get("id"), error=str(e))
            except Exception as e:  # a payload shape we did not expect: log it, keep the agent alive
                ctx.log("duel", "error", duel=d.get("id"), error=repr(e), payload=d)

    def play(self, d: dict):
        ctx = self.ctx
        me = ctx.me["id"]
        role = str(first(d, "role", "side", "you_are", default="")).lower()
        limit = first(d, "your_limit", "limit", "your_cost", "cost", "your_value", "value", "reservation")
        if limit is None or role not in ("seller", "buyer", "sell", "buy"):
            ctx.log("duel", "unknown_shape", duel=d.get("id"), keys=sorted(d))
            return
        seller = role.startswith("sell")
        two_issue = "days" in (d.get("issues") or []) or d.get("your_days_weight") is not None
        w = float(first(d, "your_days_weight", "days_weight", default=0) or 0)
        decay = float(first(d, "decay", default=0.06) or 0.06)

        msgs = first(d, "messages", "history", default=[]) or []
        mine = [m for m in msgs if first(m, "sender", "from", "by", "author", "side", default=None) in (me, "you", "us", role)]
        for m in msgs:  # the price sits in the message's structured offer, as in dealer threads
            o = m.get("offer") if isinstance(m.get("offer"), dict) else {}
            if m.get("price") is None and o:
                m["price"] = (o.get("give") or {}).get("cash") or (o.get("want") or {}).get("cash") or o.get("price")
            if m.get("days") is None and o:
                m["days"] = o.get("days")
        theirs = [m for m in msgs if m not in mine and first(m, "price", default=None) is not None]
        standing = first(d, "standing_offer", "rival_offer", "their_offer", "offer", default=None)
        rival = None
        if standing and first(standing, "by", "from", "maker", default=None) not in (me, "you"):
            rival = standing
        elif theirs:
            rival = theirs[-1]
        r_price = first(rival, "price", default=None) if rival else None
        r_days = first(rival, "days", default=None) if rival else None

        def util(price, days):
            """Our surplus in primas for a deal at (price, days). Days enter as weight x days."""
            s = (price - limit) if seller else (limit - price)
            return s + (w * days if two_issue and days is not None else 0)

        k = len(mine)
        # anchor: far from our limit; if the rival has spoken, aim past the midpoint on our side
        span = max(5.0, abs(limit) * 0.6)
        if r_price is not None:
            span = max(span, abs(r_price - limit) * 1.4)
        anchor = limit + span if seller else limit - span
        x = min(1.0, k / self.ROUNDS)
        target = anchor + ((limit + (1 if seller else -1) * max(1, 0.08 * span)) - anchor) * (x ** 1.3)
        price = math.ceil(target) if seller else math.floor(target)
        if r_price is not None:  # never concede past the rival's own offer
            price = max(price, r_price) if seller else min(price, r_price)

        days = None
        if two_issue:
            if abs(w) < 0.5 and r_days is not None:
                days = int(r_days)  # cheap for us: give the rival the days it wants, keep pushing on price
                bump = max(1, round(abs(w) * 3))
                price = price + bump if seller else price - bump
            else:
                days = 10 if w > 0 else 0
                if r_days is not None and k >= 2:  # meet halfway on days late in the talk
                    days = round((days + int(r_days)) / 2)

        # accept the rival's offer when it is inside our limit and at least as good as what waiting would likely bring
        if r_price is not None:
            u_r = util(r_price, r_days if r_days is not None else days)
            u_next = util(price, days) * (1 - decay)
            if u_r > 0 and (u_r >= 0.9 * u_next or k >= self.ROUNDS):
                if ctx.take_accept(kind="duel"):
                    ctx.api.duel_accept(d["id"])
                    ctx.log("duel", "accept", duel=d["id"], price=r_price, days=r_days, our_surplus=round(u_r, 1), limit=limit)
                    return
        if util(price, days) <= 0:  # would cross our limit: hold at a safe price instead
            price = math.ceil(limit + 1) if seller else math.floor(limit - 1)
        last = ctx.state.setdefault("duel_last", {}).get(str(d["id"]))
        if last and last == [price, days, r_price]:
            return  # nothing new on either side: repeating a price earns nothing
        ctx.state["duel_last"][str(d["id"])] = [price, days, r_price]
        text = (f"I can do {price}" + (f" with delivery on day {days}" if days is not None else "") +
                ". That is a fair deal for both of us.")
        ctx.api.duel_say(d["id"], text, price=price, days=days)
        ctx.log("duel", "offer", duel=d["id"], role=role, limit=limit, price=price, days=days, rival_price=r_price, k=k)
