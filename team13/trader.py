"""Trading with other teams: this scores the value we gain at OUR private values.

- Take offers on the board that gain us value after fees (best first, one accept per tick).
- List our spares (duplicates, low-multiplier sets) at prices that decay toward a floor that still gains us value.
- Bid for the cards we value most (high-multiplier sets, page completers) below what they are worth to us.
- Answer team conversations by reading only the structured offer, never the words.
"""
from __future__ import annotations

import math

from bazaar_sdk import BazaarError

MIN_GAIN = 3.0          # P of private value an accepted trade must create for us
RASTRO_BPS, RASTRO_PER_CARD = 500, 1
MAX_ASKS, MAX_BIDS = 8, 6
BID_SHARE = 0.4         # at most this share of our free cash sits in open bids (the rest is for dealer deals)
REPRICE_TICKS = 8


def fee(price: float, n_cards: int, bps=RASTRO_BPS, per_card=RASTRO_PER_CARD) -> float:
    return math.ceil(bps * price / 10000) + per_card * n_cards


def refs_of(side: dict, ctx) -> list:
    """Card refs on one side of an offer, whatever shape the server uses."""
    if not side:
        return []
    out = list(side.get("cards") or [])
    for t in side.get("types") or []:
        out.append(t.split(":", 1)[-1])
    for a in side.get("assets") or []:
        if isinstance(a, dict):
            if a.get("kind", "card") == "card" and a.get("ref"):
                out.append(a["ref"])
        else:
            own = next((x for x in ctx.values.assets if x["id"] == a), None)
            out.append(own["ref"] if own else f"#{a}")
    return out


class Trader:
    def __init__(self, ctx):
        self.ctx = ctx

    # ------------------------------------------------------------------ evaluation
    def evaluate(self, offer: dict) -> dict:
        """What accepting `offer` would gain us, at our values, after fees."""
        ctx, v = self.ctx, self.ctx.values
        give, want = offer.get("give") or {}, offer.get("want") or {}
        they_give, they_want = refs_of(give, ctx), refs_of(want, ctx)
        cash_in, cash_out = give.get("cash") or 0, want.get("cash") or 0
        if any(r.startswith("#") for r in they_give) or any(r not in v.cards for r in they_give + they_want):
            return {"gain": None, "why": "unknown items"}
        if len(they_want) > 0 and any(v.held[r] == 0 for r in they_want):
            return {"gain": None, "why": "they want cards we do not hold"}
        f = fee(cash_in or cash_out, len(they_give) + len(they_want))
        gain = v.gain_of_adding(they_give) - v.loss_of_removing(they_want) + cash_in - cash_out - f
        if cash_out + f > ctx.me["cash"] - ctx.reserve():
            return {"gain": None, "why": "cash reserved"}
        return {"gain": gain, "give": they_give, "want": they_want, "cash_in": cash_in, "cash_out": cash_out, "fee": f}

    def assets_for(self, refs: list) -> list:
        """Which of our copies to hand over: the highest serial first (keep low serials, they are nicer)."""
        out, locked = [], self.ctx.locked_assets()
        for r in refs:
            cands = sorted((a for a in self.ctx.values.assets if a["ref"] == r and a["id"] not in locked and a["id"] not in out),
                           key=lambda a: -a.get("serial", 0))
            if not cands:
                return []
            out.append(cands[0]["id"])
        return out

    # ------------------------------------------------------------------ loop
    def step(self):
        self.take_board()
        self.answer_teams()
        self.maintain_listings()

    def take_board(self):
        ctx = self.ctx
        if not ctx.accepts_left():
            return
        best = None
        for o in ctx.board:
            if o.get("maker") == ctx.me["id"] or o.get("status", "open") != "open":
                continue
            ev = self.evaluate(o)
            if ev.get("gain") is not None and ev["gain"] >= MIN_GAIN and (best is None or ev["gain"] > best[1]["gain"]):
                best = (o, ev)
        if not best:
            return
        o, ev = best
        # double-check with the server's own value of what we receive (page bonus included)
        try:
            exact = sum(ctx.api.value(r)["your_value"] for r in ev["give"])
            if ev["give"] and exact - ev["cash_out"] - ev["fee"] - ctx.values.loss_of_removing(ev["want"]) < MIN_GAIN - 1:
                ctx.log("trade", "skip_after_check", offer=o["id"], est=ev["gain"], exact=exact)
                return
        except BazaarError:
            pass
        assets = self.assets_for(ev["want"]) if ev["want"] else None
        if ev["want"] and not assets:
            return
        if ctx.take_accept():
            try:
                ctx.api.accept(o["id"], assets=assets)
                ctx.log("trade", "accept", offer=o["id"], gain=round(ev["gain"], 1), detail=ev)
            except BazaarError as e:
                ctx.log("trade", "accept_refused", offer=o["id"], error=str(e))

    def answer_teams(self):
        ctx = self.ctx
        for th in ctx.threads:
            if th.get("kind") == "persona" or th["status"] != "open":
                continue
            theirs = [o for o in th.get("standing_offers", []) if o.get("maker") != ctx.me["id"] and o.get("status") == "open"]
            if theirs:
                o = theirs[-1]
                ev = self.evaluate(o)
                if ev.get("gain") is not None and ev["gain"] >= MIN_GAIN and ctx.take_accept():
                    try:
                        ctx.api.accept(o["id"], assets=self.assets_for(ev["want"]) or None)
                        ctx.log("trade", "accept_team", thread=th["id"], gain=round(ev["gain"], 1), detail=ev)
                    except BazaarError as e:
                        ctx.log("trade", "accept_team_refused", thread=th["id"], error=str(e))
                    continue
                counter = self.counter(ev)
                last_tick = ctx.state.setdefault("team_reply_tick", {}).get(str(th["id"]), -99)
                if counter and ctx.clock["tick"] - last_tick >= 2:
                    try:
                        ctx.api.say(th["id"], counter["text"], offer=counter["offer"])
                        ctx.state["team_reply_tick"][str(th["id"])] = ctx.clock["tick"]
                        ctx.log("trade", "counter_team", thread=th["id"], offer=counter["offer"], their=ev)
                    except BazaarError as e:
                        ctx.log("trade", "counter_refused", thread=th["id"], error=str(e))

    def counter(self, ev: dict):
        """A structured counter on the same cards that gains us MIN_GAIN + 2."""
        v = self.ctx.values
        if ev.get("gain") is None:
            return None
        if ev["want"] and not ev["give"]:  # they want our cards for cash: name our price
            loss = v.loss_of_removing(ev["want"])
            price = math.ceil(loss + MIN_GAIN + 2 + fee(loss, len(ev["want"])))
            assets = self.assets_for(ev["want"])
            if not assets:
                return None
            return {"text": f"Happy to trade. {', '.join(ev['want'])} for {price} primas.",
                    "offer": {"give": {"assets": assets}, "want": {"cash": price}}}
        if ev["give"] and not ev["want"]:  # they sell cards for cash: name what we pay
            gain = v.gain_of_adding(ev["give"])
            price = math.floor(gain - MIN_GAIN - 2 - fee(gain, len(ev["give"])))
            if price < 1:
                return None
            return {"text": f"We would love those. {price} primas for {', '.join(ev['give'])}?",
                    "offer": {"give": {"cash": price}, "want": {"cards": ev["give"]}}}
        return None

    def maintain_listings(self):
        ctx, v = self.ctx, self.ctx.values
        tick = ctx.clock["tick"]
        listed = ctx.state.setdefault("listings", {})  # offer id -> {"kind", "ref", "price", "tick", "start"}
        mine = {str(o["id"]): o for o in ctx.my_offers if o.get("status", "open") == "open"}
        for oid in list(listed):
            if oid not in mine:
                ctx.log("trade", "listing_gone", offer=oid, info=listed.pop(oid))
        budget = ctx.limit("offers_per_team_per_tick", 12) - 1
        open_total = len(mine)

        # reprice stale listings toward their floor
        for oid, L in list(listed.items()):
            if budget <= 1 or tick - L["tick"] < REPRICE_TICKS:
                continue
            new = self._price(L, tick)
            if new == L["price"]:
                continue
            try:
                ctx.api.cancel(int(oid))
                listed.pop(oid)
                budget -= 1
                open_total -= 1
                ctx.log("trade", "reprice_cancel", offer=oid, old=L["price"], new=new, ref=L["ref"])
            except BazaarError as e:
                ctx.log("trade", "cancel_refused", offer=oid, error=str(e))

        locked = ctx.locked_assets()
        asks = [L for L in listed.values() if L["kind"] == "ask"]
        bids = [L for L in listed.values() if L["kind"] == "bid"]
        # asks: spares, priced from book (what others may value) down to a floor that still gains us MIN_GAIN
        for a in v.spares():
            if len(asks) >= MAX_ASKS or budget <= 0 or open_total >= ctx.limit("max_open_offers_per_team", 30):
                break
            if a["id"] in locked or any(L.get("asset") == a["id"] for L in asks):
                continue
            loss = v.loss_of_removing([a["ref"]])
            book = v.book(a["ref"])
            floor = math.ceil(loss + MIN_GAIN + fee(book, 1))
            start = max(floor, math.ceil(book * 1.25))
            L = {"kind": "ask", "ref": a["ref"], "asset": a["id"], "start": start, "floor": floor, "born": tick}
            L["price"] = self._price(L, tick)
            try:
                o = ctx.api.list_offer({"assets": [a["id"]]}, {"cash": L["price"]}, venue="rastro")
                L["tick"] = tick
                listed[str(o.get("id"))] = L
                asks.append(L)
                budget -= 1
                open_total += 1
                ctx.log("trade", "list_ask", ref=a["ref"], price=L["price"], floor=floor, our_value=round(loss, 1))
            except BazaarError as e:
                ctx.log("trade", "list_refused", ref=a["ref"], error=str(e))
                break
        # bids: cards worth a lot to us, offered below that value; total committed stays inside free cash
        free = BID_SHARE * (ctx.me["cash"] - ctx.reserve()) - sum(L["price"] for L in bids)
        # best value per prima committed first: cheap cards from our high-multiplier sets and page completers
        wants = sorted(v.wishlist(limit=30), key=lambda x: (-round(x[1] / max(1, v.book(x[0])), 2), v.book(x[0])))
        for ref, gain in wants:
            if len(bids) >= MAX_BIDS or budget <= 0 or open_total >= ctx.limit("max_open_offers_per_team", 30):
                break
            if any(L["ref"] == ref for L in bids):
                continue
            book = v.book(ref)
            ceiling = math.floor(gain - MIN_GAIN - fee(book, 1))
            start = min(ceiling, math.floor(book * 0.6))
            if ceiling < 2 or start < 1:
                continue
            L = {"kind": "bid", "ref": ref, "start": start, "floor": min(ceiling, math.floor(book * 1.1)), "born": tick}
            L["price"] = self._price(L, tick)
            if L["price"] > free:
                continue
            try:
                o = ctx.api.list_offer({"cash": L["price"]}, {"cards": [ref]}, venue="rastro")
                L["tick"] = tick
                listed[str(o.get("id"))] = L
                bids.append(L)
                free -= L["price"]
                budget -= 1
                open_total += 1
                ctx.log("trade", "list_bid", ref=ref, price=L["price"], our_value=round(gain, 1))
            except BazaarError as e:
                ctx.log("trade", "bid_refused", ref=ref, error=str(e))
                break

    @staticmethod
    def _price(L, tick):
        """Walk from the start price toward the floor (asks down, bids up) over ~10 reprices."""
        steps = max(0, (tick - L["born"]) // REPRICE_TICKS)
        x = min(1.0, steps / 10)
        p = L["start"] + (L["floor"] - L["start"]) * x
        return int(math.ceil(p) if L["kind"] == "ask" else math.floor(p))
