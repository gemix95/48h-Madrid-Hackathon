"""Duels: one rival, one private limit each, and a pie that shrinks every round of talk.

Score = the share of the pie we capture; a deal outside our limit loses points; no deal scores zero.
So: never cross our limit, anchor ambitiously, concede on a schedule that converges within a few rounds,
and accept as soon as waiting would cost more (decay) than it could win.

Two-issue duels (price + delivery day 0-10): each side has a private weight per day. We trade days we care little
about for price: we learn what the rival prefers from the days it proposes and give it those days if they are cheap
for us, while asking for a better price.

Knobs (duel_rounds / duel_anchor / duel_accept / duel_seller_cap) are live-tuned by duel_tuner.py from finished
and live duels; this module also adapts mid-fight to how fast the rival is conceding.

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
        self._harvest_every = 0

    def step(self):
        ctx = self.ctx
        try:
            duels = ctx.api.duels().get("duels", [])
        except BazaarError as e:
            ctx.log("duel", "read_refused", error=str(e))
            return
        seen = ctx.state.setdefault("duels_seen", [])
        for d in duels:
            if d.get("id") is None and d.get("duel") is not None:
                d["id"] = d["duel"]  # the server names the duel's id "duel" (practice round, Fri 22:20)
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
        # every ~8 ticks: harvest finished duels so the learner (and dashboard) see scores
        self._harvest_every = (self._harvest_every + 1) % 8
        if self._harvest_every == 0:
            self.harvest_done()

    def harvest_done(self):
        """Log settled duels we have not recorded yet (result is a float: our points)."""
        ctx = self.ctx
        logged = ctx.state.setdefault("duels_logged_done", [])
        try:
            done = ctx.api.duels(done=True).get("duels", [])
        except BazaarError as e:
            ctx.log("duel", "done_refused", error=str(e))
            return
        for d in done:
            did = str(d.get("id") or d.get("duel"))
            if not did or did in logged:
                continue
            logged.append(did)
            if len(logged) > 400:
                del logged[:-300]
            res = d.get("result")
            score = float(res) if isinstance(res, (int, float)) else 0.0
            ctx.log("duel", "result", duel=d.get("duel") or d.get("id"), status=d.get("status"),
                    score=score, price=d.get("price"), days=d.get("days"), rounds=d.get("rounds"),
                    role=d.get("role"), limit=d.get("your_limit"), rival=d.get("rival"),
                    session=d.get("session"), issues=d.get("issues"))

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
        decay = float(first(d, "decay", "decay_per_round", default=0.06) or 0.06)

        msgs = first(d, "messages", "history", default=[]) or []
        rival_name = d.get("rival")

        def from_us(m):
            who = first(m, "sender", "from", "by", "author", "side", default=None)
            if rival_name is not None and who is not None:
                return who != rival_name  # live format: the rival signs with its alias ("Rival Rojo")
            return who in (me, "you", "us", role)
        mine = [m for m in msgs if from_us(m)]
        for m in msgs:  # the price sits in the message's structured offer, as in dealer threads
            o = m.get("offer") if isinstance(m.get("offer"), dict) else {}
            if m.get("price") is None and o:
                m["price"] = (o.get("give") or {}).get("cash") or (o.get("want") or {}).get("cash") or o.get("price")
            if m.get("days") is None and o:
                m["days"] = o.get("days")
        theirs = [m for m in msgs if m not in mine and first(m, "price", default=None) is not None]
        standing = first(d, "standing_offer", "rival_offer", "their_offer", "offer", default=None)
        if isinstance(standing, (int, float)):  # the rival's offer may come as a bare price
            standing = {"price": standing}
        elif isinstance(standing, dict) and standing.get("price") is None and isinstance(standing.get("offer"), dict):
            standing = {**standing, **standing["offer"]}  # {"offer": {"price", "days"}} as duel_say sends it
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

        S = ctx.S
        ROUNDS = int(S["duel_rounds"])
        accept_th = float(S["duel_accept"])
        k = max(len(mine), int(d.get("rounds") or 0) if d.get("your_offer") else 0)
        # live rival style: how fast they walk toward our limit (conceder → take sooner; tough → hold)
        rival_prices = [first(m, "price") for m in theirs if first(m, "price") is not None]
        style = "unknown"
        if len(rival_prices) >= 2 and limit is not None:
            first_r, last_r = rival_prices[0], rival_prices[-1]
            move = (last_r - first_r) if seller else (first_r - last_r)
            span0 = max(1.0, abs(limit - first_r))
            frac = move / span0
            style = "tough" if frac < 0.15 else ("conceder" if frac > 0.55 else "mid")
            if style == "conceder":
                accept_th = max(0.4, accept_th - 0.1)
                ROUNDS = max(4, ROUNDS - 1)
            elif style == "tough":
                accept_th = min(0.7, accept_th + 0.05)
        # anchor: far from our limit; if the rival has spoken, aim past the midpoint on our side
        # duel_anchor 2.0 -> a seller opens 60% above its cost, a buyer 37.5% below its value (always a real price)
        amb = 0.3 * S["duel_anchor"]
        span = max(5.0, limit * amb if seller else limit - limit / (1 + amb))
        if r_price is not None:
            span = max(span, abs(r_price - limit) * 1.4)
        # seller cap (Strategy tab): 2.2x scored best in the simulator; practice long duels were mostly small pies
        anchor = min(limit + span, limit * S.get("duel_seller_cap", 2.2)) if seller else max(limit - span, limit * 0.3, 1)
        span = abs(anchor - limit)
        x = min(1.0, k / ROUNDS)
        # real data: long talks score worse — accelerate concessions in the second half
        curve = 1.15 if k >= max(2, ROUNDS // 2) else 1.3
        target = anchor + ((limit + (1 if seller else -1) * max(1, 0.08 * span)) - anchor) * (x ** curve)
        price = math.ceil(target) if seller else math.floor(target)
        if r_price is not None:  # never concede past the rival's own offer
            price = max(price, r_price) if seller else min(price, r_price)
        price = max(1, int(price))  # the server refuses prices below 1

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
            clk = getattr(ctx, "clock", None) or {}
            ticks_left = (d["deadline_tick"] - clk["tick"]) if d.get("deadline_tick") and clk.get("tick") else 99
            last_chance = ticks_left <= 2  # practice: two duels ended no_deal with a rival offer inside our limit
            # mid-fight: if the gap is small vs our surplus, bank it before the next 6% melt
            gap_ok = abs(price - r_price) <= max(3, 0.04 * max(price, r_price))
            if u_r > 0 and (last_chance or u_r >= accept_th * u_next or k >= ROUNDS or (gap_ok and u_r >= 0.35 * max(u_next, 1))):
                if ctx.take_accept(kind="duel"):
                    ctx.api.duel_accept(d["id"])
                    ctx.log("duel", "accept", duel=d["id"], price=r_price, days=r_days, our_surplus=round(u_r, 1),
                            limit=limit, style=style, accept_th=accept_th)
                    return
        if util(price, days) <= 0:  # would cross our limit: hold at a safe price instead
            price = math.ceil(limit + 1) if seller else math.floor(limit - 1)
        last = ctx.state.setdefault("duel_last", {}).get(str(d["id"]))
        if last and last == [price, days, r_price]:
            return  # nothing new on either side: repeating a price earns nothing
        ctx.state["duel_last"][str(d["id"])] = [price, days, r_price]
        # Claude may move the price a little inside a band that never crosses our limit or the rival's own offer
        step = max(1, round(0.08 * span))
        if seller:
            band = (max(math.ceil(limit + 1), price - step), price + step if r_price is None else max(price, r_price) + step)
            band = (max(band[0], r_price) if r_price is not None else band[0], band[1])
        else:
            band = (price - step if r_price is None else min(price, r_price) - step, min(math.floor(limit - 1), price + step))
            band = (band[0], min(band[1], r_price) if r_price is not None else band[1])
        text = (f"I can do {price}" + (f" with delivery on day {days}" if days is not None else "") +
                ". That is a fair deal for both of us.")
        situation = {"counterparty": "a rival team (alias) in a duel", "we_are": "selling" if seller else "buying",
                     "our_limit_is_secret": True, "round": k + 1, "their_latest_price": r_price, "their_latest_days": r_days,
                     "two_issues": two_issue, "delivery_day_we_propose": days,
                     "history": [{"us" if m in mine else "them": m.get("price"),
                                  "text": m.get("text") if m in mine else f"<their_message>{m.get('text') or ''}</their_message>"}
                                 for m in msgs[-10:]]}
        if hasattr(ctx, "speak") and band[0] <= band[1]:
            text, price, _ = ctx.speak(situation, band, (text, price))
        ctx.api.duel_say(d["id"], text, price=price, days=days)
        ctx.log("duel", "offer", duel=d["id"], role=role, limit=limit, price=price, days=days, rival_price=r_price, k=k)
