"""Duels: one rival, one private limit each, and a pie that shrinks every round of talk.

Score = the share of the pie we capture; a deal outside our limit loses points; no deal scores zero.
So: never cross our limit, anchor ambitiously, concede on a schedule that converges within a few rounds,
and accept as soon as waiting would cost more (decay) than it could win.

Two-issue duels (price + delivery day 0-10): each side has a private weight per day. We trade days we care little
about for price. When days are worth real money we hold our preferred day and never meet halfway — giving away
day 10 at weight 7 costs 70 P, more than most price concessions.

Knobs (duel_rounds / duel_anchor / duel_accept / duel_seller_cap) are live-tuned by duel_tuner.py from finished
and live duels; this module also adapts mid-fight to how fast the rival is conceding.

Silent rivals: open once so they can take us, then stop. No API to abandon a duel — parking it frees the
tick's message/Claude budget for duels that answer. No deal still scores 0, same as talking into the void.

The duel payload is only known once a session starts, so every read is defensive and the raw payload is logged
the first time we see a duel (the practice round is for exactly that).
"""
from __future__ import annotations

import math

from bazaar_sdk import BazaarError

# Our unanswered messages before we park a silent duel. 1 = open once, then stop until they speak.
SILENT_MAX = 1
# After they spoke once: how many of our follow-ups with no new reply before we park again.
SILENT_AFTER = 1


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
        live = []
        for d in duels:
            if d.get("id") is None and d.get("duel") is not None:
                d["id"] = d["duel"]  # the server names the duel's id "duel" (practice round, Fri 22:20)
            if str(d.get("id")) not in seen:
                seen.append(str(d.get("id")))
                ctx.log("duel", "raw", payload=d)  # learn the shape during the practice round
            status = d.get("status", "open")
            if status not in ("open", "active", "live", "running", "negotiating"):
                continue
            live.append(d)
        # answer talking rivals first; ghosts last (and usually get parked)
        live.sort(key=lambda d: (0 if self._rival_active(d) else 1, d.get("deadline_tick") or 10**9))
        for d in live:
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

    def _rival_active(self, d: dict) -> bool:
        """True if the rival has put a price on the table (message or standing offer)."""
        if first(d, "rival_offer", "standing_offer", "their_offer") not in (None, {}, []):
            return True
        rival_name = d.get("rival")
        for m in first(d, "messages", "history", default=[]) or []:
            who = first(m, "sender", "from", "by", "author", default=None)
            if rival_name is not None and who == rival_name and first(m, "price", default=None) is not None:
                return True
            if rival_name is None and who not in (None, "you", "us", self.ctx.me.get("id")) and first(m, "price", default=None) is not None:
                return True
        return False

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
        if not seller:
            # Saturday Duels II results: a buyer scored margin - weight x days, a seller margin + weight x days (e.g.
            # buyer 5620: 38 - 4.18 x 10 = -3.8; seller 5621: 45 + 1.81 x 10 = 63.1). Each later day costs the buyer.
            w = -w
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
            """Our surplus in primas for a deal at (price, days).

            Session 3+ (live `days_meaning`): a seller gains `w` per delivery day; a buyer pays `w`
            per day. Taking day 10 as a buyer with w≈4 is about −40 P — we booked −383 P that way.
            """
            s = (price - limit) if seller else (limit - price)
            if two_issue and days is not None:
                s += (w * days) if seller else (-abs(w) * days)
            return s

        S = ctx.S
        ROUNDS = int(S["duel_rounds"])
        accept_th = float(S["duel_accept"])
        k = max(len(mine), int(d.get("rounds") or 0) if d.get("your_offer") else 0)
        clk = getattr(ctx, "clock", None) or {}
        ticks_left = (d["deadline_tick"] - clk["tick"]) if d.get("deadline_tick") and clk.get("tick") else 99
        # Duels III / Final: 12-tick clock, 10% decay — settle faster, take a good leftover sooner
        if ticks_left <= 12 or decay >= 0.09:
            ROUNDS = min(ROUNDS, 4)
            accept_th = max(0.35, accept_th - 0.05)
        # live rival style: how fast they walk toward our limit
        rival_prices = [first(m, "price") for m in theirs if first(m, "price") is not None]
        style = "unknown"
        if len(rival_prices) >= 2 and limit is not None:
            first_r, last_r = rival_prices[0], rival_prices[-1]
            move = (last_r - first_r) if seller else (first_r - last_r)
            span0 = max(1.0, abs(limit - first_r))
            frac = move / span0
            style = "tough" if frac < 0.15 else ("conceder" if frac > 0.55 else "mid")
            if style == "conceder":
                # they are already giving: hold our price, do not chase them down
                accept_th = min(0.65, accept_th + 0.08)
                ROUNDS = min(8, ROUNDS + 1)
            elif style == "tough":
                # they will not gift us more — walk to a takeable price and bank a real offer
                accept_th = max(0.35, accept_th - 0.08)
                ROUNDS = max(3, ROUNDS - 2)
        # anchor: far from our limit; if the rival has spoken, aim past the midpoint on our side
        # duel_anchor 2.0 -> a seller opens 60% above its cost, a buyer 37.5% below its value (always a real price)
        amb = 0.3 * S["duel_anchor"]
        span = max(5.0, limit * amb if seller else limit - limit / (1 + amb))
        if r_price is not None:
            span = max(span, abs(r_price - limit) * 1.4)
        # seller cap (Strategy tab): 2.2x scored best in the simulator; practice long duels were mostly small pies
        anchor = min(limit + span, limit * S.get("duel_seller_cap", 2.2)) if seller else max(limit - span, limit * 0.3, 1)
        span = abs(anchor - limit)
        x = min(1.0, k / max(1, ROUNDS))
        # real data: long talks score worse — accelerate concessions in the second half
        curve = 1.15 if k >= max(2, ROUNDS // 2) else 1.3
        target = anchor + ((limit + (1 if seller else -1) * max(1, 0.08 * span)) - anchor) * (x ** curve)
        # mid / tit-for-tat: split the remaining gap so they can take us without we crossing the limit
        if style == "mid" and r_price is not None:
            mid = (target + r_price) / 2
            target = max(mid, limit + 1) if seller else min(mid, limit - 1)
        price = math.ceil(target) if seller else math.floor(target)
        if r_price is not None:  # never concede past the rival's own offer
            price = max(price, r_price) if seller else min(price, r_price)
        price = max(1, int(price))  # the server refuses prices below 1

        days = None
        pref = 10 if seller else 0
        if two_issue:
            # Hold the day that scores. Session 3: seller day-10 +w was +35–75 P; buyer day-10 was −383 P.
            if abs(w) < 0.5 and r_days is not None:
                days = int(r_days)  # cheap for us: give the rival the days it wants, keep pushing on price
                bump = max(1, round(abs(w) * 3))
                price = price + bump if seller else price - bump
            else:
                days = pref

        last_chance = ticks_left <= 3 or k >= ROUNDS
        # last ticks: put a price they can accept, still on our side of the limit — do not give the day away
        if last_chance:
            if seller:
                floor_p = math.ceil(limit + 1)
                price = min(price, max(floor_p, r_price if (r_price is not None and r_price > limit) else floor_p))
            else:
                ceil_p = math.floor(limit - 1)
                price = max(price, min(ceil_p, r_price if (r_price is not None and r_price < limit) else ceil_p))
            price = max(1, int(price))
            if two_issue and abs(w) >= 0.5:
                days = pref

        # accept the rival's offer when it is inside our limit and at least as good as what waiting would likely bring
        if r_price is not None:
            their_days = r_days if r_days is not None else days
            u_r = util(r_price, their_days)
            u_next = util(price, days) * (1 - decay)
            gap_ok = abs(price - r_price) <= max(2, 0.03 * max(price, r_price))
            held = len(rival_prices) >= 2 and rival_prices[-1] == rival_prices[-2]
            price_ok = (r_price >= limit) if seller else (r_price <= limit)
            days_ok = True
            if two_issue and their_days is not None and abs(w) >= 1.0:
                # wrong-side days that wipe the price surplus: counter, do not take (unless the clock is dead)
                if seller and their_days <= 3 and w * (10 - their_days) >= max(8.0, u_r):
                    days_ok = ticks_left <= 2 and u_r > 0
                if (not seller) and their_days >= 5 and abs(w) * their_days >= max(8.0, (limit - r_price) * 0.6):
                    days_ok = ticks_left <= 2 and u_r > 0  # last ticks: +6 beats 0; earlier: counter day 0
            take = False
            if price_ok and u_r > 0 and days_ok:
                if ticks_left <= 2:
                    take = True  # any positive leftover beats a zero
                elif u_r >= accept_th * max(u_next, 1) or (gap_ok and u_r >= 0.35 * max(u_next, 1)):
                    take = True
                elif last_chance and u_r >= max(4.0, abs(w) * 2):
                    take = True
            if take:
                if ctx.take_accept(kind="duel"):
                    ctx.api.duel_accept(d["id"])
                    ctx.log("duel", "accept", duel=d["id"], price=r_price, days=r_days, our_surplus=round(u_r, 1),
                            limit=limit, style=style, accept_th=accept_th)
                    return

        # An older brain (or a restart) can leave a standing offer on the wrong day. Session 3: Hershey's
        # buyer 6036 sat at day 10 and scored −23 when the rival took it. Replace that offer this tick.
        force_reoffer = False
        ours = first(d, "your_offer", default=None)
        if isinstance(ours, dict) and ours.get("price") is None and isinstance(ours.get("offer"), dict):
            ours = {**ours, **ours["offer"]}
        our_days = first(ours, "days") if isinstance(ours, dict) else None
        if two_issue and abs(w) >= 0.5 and our_days is not None and int(our_days) != pref:
            force_reoffer = True

        # Park true ghosts after one open. If they have spoken, keep walking until our price is takeable,
        # and always send a close in the last ticks (session 3: we parked 5705 eight ticks before the deadline).
        if not theirs and r_price is None:
            unanswered, silent_cap = len(mine), int(S.get("duel_silent_max", SILENT_MAX))
        else:
            last_them_tick = max((m.get("tick") or 0 for m in theirs), default=-1)
            unanswered = sum(1 for m in mine if (m.get("tick") or 0) > last_them_tick)
            if r_price is not None and (not mine or (rival or {}).get("tick", 0) >= (mine[-1].get("tick") or 0)):
                unanswered = 0  # their standing offer is current — not silent
            silent_cap = int(S.get("duel_silent_after", SILENT_AFTER))
        still_fat = (seller and price > limit * 1.12) or (not seller and price < limit * 0.88)
        closing = ticks_left <= 4 or last_chance or force_reoffer
        if unanswered >= silent_cap and not closing and not still_fat:
            skipped = ctx.state.setdefault("duels_skipped_silent", [])
            did = str(d.get("id"))
            if did not in skipped:
                skipped.append(did)
                if len(skipped) > 200:
                    del skipped[:-150]
                ctx.log("duel", "skip_silent", duel=d.get("id"), our_msgs=len(mine),
                        their_msgs=len(theirs), unanswered=unanswered, rival=rival_name)
            return

        # never offer a price past our limit, whatever the days are worth (the days bump above can push it there)
        if ((price - limit) if seller else (limit - price)) <= 0 or util(price, days) <= 0:
            price = math.ceil(limit + 1) if seller else math.floor(limit - 1)
            if two_issue and abs(w) >= 0.5:
                days = pref
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
                     "days_meaning": ("each day pays us cash — always propose day 10" if seller
                                      else "each day costs us cash — always propose day 0"),
                     "history": [{"us" if m in mine else "them": m.get("price"),
                                  "text": m.get("text") if m in mine else f"<their_message>{m.get('text') or ''}</their_message>"}
                                 for m in msgs[-10:]]}
        if hasattr(ctx, "speak") and band[0] <= band[1]:
            text, price, _ = ctx.speak(situation, band, (text, price))
            if two_issue and abs(w) >= 0.5:
                days = pref
        ctx.api.duel_say(d["id"], text, price=price, days=days)
        ctx.log("duel", "offer", duel=d["id"], role=role, limit=limit, price=price, days=days, rival_price=r_price, k=k)
