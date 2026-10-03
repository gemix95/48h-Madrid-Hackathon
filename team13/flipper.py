"""Flipper: buy a card one team sells below what another team bids for it, then sell into that bid.

Each trade scores at our private values, so a flip scores (their bid - the ask - both fees): we gain its value when
we buy and give the same value back when we sell. We pay both fees because we accept both sides; the profit must
still clear `flip_min_gain`, and we only buy at or below what the card is worth to us, so a bid that vanishes
never leaves us holding a loss.

One flip at a time, so a flip never ties up more than one card and `flip_max_cash`:
  buying   we accepted the ask; it settles next tick
  holding  we own the card: accept the target bid (or the best bid now) if it still pays
  selling  no bid pays any more: a direct offer to the bidder at their old price, kept open, and a fallback
           listing at cost + fees after FLIP_PATIENCE ticks (the trader and guard then treat it like any spare)
"""
from __future__ import annotations

from bazaar_sdk import BazaarError
from trader import fee, refs_of, team_caps

FLIP_PATIENCE = 40  # ticks we wait for the bidder before listing the card for anyone


class Flipper:
    def __init__(self, ctx):
        self.ctx = ctx

    # ------------------------------------------------------------------ book
    def _team(self, o):
        intel = getattr(self.ctx, "intel", None)
        m = intel.summary().get("offer_maker", {}).get(o.get("id")) if intel else None
        return m or o.get("maker")

    def _book(self):
        """{ref: {"asks": [(price, venue, offer, team)], "bids": [...]}} from other teams' single-card cash offers."""
        ctx, out = self.ctx, {}
        for venue, offers in (getattr(ctx, "boards", None) or {"rastro": ctx.board}).items():
            if venue == ctx.state.get("venue"):
                continue  # our own market: we may not trade there
            for o in offers:
                if o.get("status", "open") != "open" or o.get("maker") == ctx.me.get("id") or o.get("to") not in (None, ctx.me.get("id")):
                    continue
                g, w = o.get("give") or {}, o.get("want") or {}
                gr, wr = refs_of(g, ctx), refs_of(w, ctx)
                if len(gr) == 1 and not wr and w.get("cash") and not g.get("cash"):
                    out.setdefault(gr[0], {"asks": [], "bids": []})["asks"].append((w["cash"], venue, o, self._team(o)))
                elif len(wr) == 1 and not gr and g.get("cash") and not w.get("cash"):
                    out.setdefault(wr[0], {"asks": [], "bids": []})["bids"].append((g["cash"], venue, o, self._team(o)))
        return out

    def _fee(self, venue, price):
        bps, per = self.ctx.venue_fee(venue)
        return fee(price, 1, bps, per)

    # ------------------------------------------------------------------ step
    def step(self):
        ctx, S = self.ctx, self.ctx.S
        if ctx.values is None:
            return
        F = ctx.state.get("flip")
        book = self._book()
        tick = ctx.clock.get("tick", 0)
        if F:
            self._advance(F, book, tick)
            return
        self._find(book, tick, S)

    def _find(self, book, tick, S):
        ctx = self.ctx
        min_gain = S.get("flip_min_gain", 4)
        budget = min(S.get("flip_max_cash", 120), ctx.me.get("cash", 0) - ctx.reserve())
        caps = team_caps()
        best = None
        for ref, side in book.items():
            if not side["asks"] or not side["bids"] or ref not in ctx.values.cards:
                continue
            keep = ctx.values.gain_of_adding([ref])
            for ask, av, ao, at in sorted(side["asks"], key=lambda x: x[0])[:3]:
                cost = ask + self._fee(av, ask)
                if cost > budget or ask > caps.get(ref, 10 ** 9) or cost > keep:
                    continue  # never pay more than the card is worth to us, even if the bid vanishes
                for bid, bv, bo, bt in sorted(side["bids"], key=lambda x: -x[0])[:3]:
                    sol = getattr(ctx, "solvency", None)
                    if bt == at or (sol and sol.cannot_pay(bt, bid)):
                        continue  # never route a team's card back to the same team
                    profit = bid - self._fee(bv, bid) - cost
                    # if we value it above the bid, keeping beats flipping
                    if profit >= min_gain and bid - self._fee(bv, bid) > keep and (best is None or profit > best[0]):
                        best = (profit, ref, ask, av, ao, at, bid, bv, bo, bt)
        if not best or not ctx.take_accept():
            return
        profit, ref, ask, av, ao, at, bid, bv, bo, bt = best
        try:
            ctx.api.accept(ao["id"])
        except BazaarError as e:
            ctx.log("flip", "buy_refused", ref=ref, offer=ao["id"], error=str(e)[:160])
            return
        ctx.state["flip"] = {"ref": ref, "stage": "buying", "since": tick, "buy": ask, "buy_venue": av, "seller": at,
                             "target": {"offer": bo["id"], "venue": bv, "price": bid, "team": bt},
                             "held_before": ctx.values.held[ref]}
        ctx.log("flip", "buy", ref=ref, price=ask, venue=av, seller=at, target_bid=bid, target_team=bt, expected=profit)

    def _advance(self, F, book, tick):
        ctx, ref = self.ctx, F["ref"]
        mine = [a for a in ctx.values.assets if a["ref"] == ref]
        if F["stage"] == "buying":
            if len(mine) > F["held_before"]:
                F["stage"], F["since"] = "holding", tick
            elif tick - F["since"] > 3:
                ctx.log("flip", "buy_failed", ref=ref)
                ctx.state.pop("flip", None)
                return
            else:
                return
        if not mine:  # sold (or traded away by another module): done
            ctx.log("flip", "done", ref=ref, stage=F["stage"])
            ctx.state.pop("flip", None)
            return
        if F["stage"] == "settling":
            if tick - F["since"] > 3:  # the sale did not settle: back to looking for a buyer
                F.update(stage="holding", since=tick)
            return
        asset = sorted(mine, key=lambda a: -a.get("serial", 0))[0]["id"]  # sell the copy we just bought
        cost = F["buy"] + self._fee(F["buy_venue"], F["buy"])
        floor = max(cost + 1, ctx.values.loss_of_removing([ref]))  # never sell below what the card is worth to us
        bids = sorted((book.get(ref) or {}).get("bids", []), key=lambda x: -x[0])
        for bid, bv, bo, bt in bids:
            sol = getattr(ctx, "solvency", None)
            if sol and sol.cannot_pay(bt, bid):
                continue  # the bidder cannot have that cash: the sale would fail at settlement
            if bid - self._fee(bv, bid) >= floor and bt != F["seller"]:
                if not ctx.take_accept():
                    return
                try:
                    ctx.api.accept(bo["id"], assets=[asset])
                    ctx.log("flip", "sell", ref=ref, price=bid, venue=bv, buyer=bt, profit=bid - self._fee(bv, bid) - cost)
                    F.update(stage="settling", since=tick)
                except BazaarError as e:
                    ctx.log("flip", "sell_refused", ref=ref, offer=bo["id"], error=str(e)[:160])
                return
        if F["stage"] == "holding":  # the bid is gone: offer the bidder their own price directly, and wait
            T = F["target"]
            try:
                o = ctx.api.list_offer({"assets": [asset]}, {"cash": T["price"]}, venue=T["venue"], to=T["team"],
                                       expires_in_ticks=FLIP_PATIENCE)
                F.update(stage="selling", since=tick, direct=o.get("id"))
                ctx.log("flip", "direct_offer", ref=ref, to=T["team"], price=T["price"], offer=o.get("id"))
            except BazaarError as e:
                ctx.log("flip", "direct_refused", ref=ref, error=str(e)[:160])
                F.update(stage="selling", since=tick)
            return
        if F["stage"] == "selling" and tick - F["since"] >= FLIP_PATIENCE:
            price = int(max(cost + 1, floor))
            try:
                o = ctx.api.list_offer({"assets": [asset]}, {"cash": price}, venue="rastro", expires_in_ticks=240)
                ctx.log("flip", "fallback_listing", ref=ref, price=price, offer=o.get("id"))
            except BazaarError as e:
                ctx.log("flip", "fallback_refused", ref=ref, error=str(e)[:160])
            ctx.state.pop("flip", None)  # from here the card is a spare like any other
