"""Guard: the last module each tick. Cancels any open offer of ours that would lose value at our private values.

Why it exists: on Friday three different code paths offered cards from our 9/10 Salamanca page (SAL-07, SAL-08) or
bid for cards we already held. Each was fixed, but one cheap check over every open offer catches the next one too.

An offer is cancelled when, at our values (page bonus and the near-complete page option included):
  - what we give (cards + cash) is worth more than what we get (cards + cash), or
  - it is a bid above the team's cap for that card (agent/caps.json), or
  - it is a bid for a card now worth less to us than the bid (a second copy once one bid filled).
Packs and other non-card items are left alone (dealer pack haggles).
The dealer bid of an arbitrage pair is left alone on purpose: it is above our value by design because the card is
resold at once into a team's open bid that pays more.

Selling below worth is never allowed, to a team or a dealer, in any mode: the server scores every deal as
min(0, V - P) + ..., so a dealer sale under our value is a full loss whatever the ladder gives. On Sunday 13:00
`ladder_mode` sold LAV-07, MAL-06 and SAL-06 to Pícaros at 10-12 P (worth 57-82 to us, page bonus included) and
broke all three complete pages: neg_points 134 -> -324. `below_worth` is the one check every sale path calls,
and the haggler runs it before accepting a dealer's offer too (that is how MAL-06 went: an accept, not our offer).
"""
from __future__ import annotations

from bazaar_sdk import BazaarError
from loans import repay_offer_ids
from trader import team_caps


SELL_TOLERANCE = 0.5  # P: rounding of our value model against the server's


def below_worth(ctx, refs: list, cash_in: float):
    """(loss, cash_in) when selling `refs` for `cash_in` gives up more value than it brings, else None. Hard rule:
    no strategy knob turns it off."""
    v = getattr(ctx, "values", None)
    if v is None or not refs:
        return None
    loss = v.loss_of_removing(refs)
    return (round(loss, 1), cash_in) if cash_in + SELL_TOLERANCE < loss else None


class Guard:
    def __init__(self, ctx):
        self.ctx = ctx

    def _refs(self, side: dict) -> list:
        own = {a["id"]: a["ref"] for a in self.ctx.values.assets}
        out = list(side.get("cards") or [])
        out += [t.split(":", 1)[1] for t in side.get("types") or [] if t.startswith("card:")]
        for a in side.get("assets") or []:
            out.append(a["ref"] if isinstance(a, dict) else own.get(a, f"#{a}"))
        return out

    def step(self):
        ctx, v = self.ctx, self.ctx.values
        if v is None:
            return
        try:
            offers = ctx.raw.my_offers().get("offers", [])  # re-read: this tick's modules may have posted new ones
        except BazaarError:
            offers = ctx.my_offers
        caps = team_caps()
        repays = repay_offer_ids(ctx.state)
        arb_thread = ((ctx.state.get("arb") or {}).get("active") or {}).get("thread")
        for o in offers:
            if o.get("maker") != ctx.me.get("id") or o.get("status", "open") != "open":
                continue
            if getattr(ctx, "shared", False) and not ctx.owns(o):
                continue  # a teammate's agent made it (AGENT_ROLE split): its own guard watches it
            if str(o.get("id")) in repays:
                continue  # a loan repayment: the borrower buys its collateral back at principal + interest
            if arb_thread and o.get("thread") == arb_thread:
                continue  # the arbitrage pair's dealer bid: it is meant to be above our value, because the same card
                # goes straight into a team's open bid that pays more (team13/arbitrage.py scores the two deals together)
            g, w = o.get("give") or {}, o.get("want") or {}
            if any(not t.startswith("card:") for t in (g.get("types") or []) + (w.get("types") or [])):
                continue
            out_refs, in_refs = self._refs(g), self._refs(w)
            if not out_refs and not in_refs:
                continue
            loss = v.loss_of_removing(out_refs) + (g.get("cash") or 0) if out_refs else (g.get("cash") or 0)
            gain = v.gain_of_adding(in_refs) + (w.get("cash") or 0)
            over_cap = not out_refs and len(in_refs) == 1 and in_refs[0] in caps and (g.get("cash") or 0) > caps[in_refs[0]]
            S = ctx.S
            under_worth = bool(out_refs and not in_refs and below_worth(ctx, out_refs, w.get("cash") or 0))
            over_worth = S.get("buy_max_worth", 1) and in_refs and not out_refs and (g.get("cash") or 0) > v.gain_of_adding(in_refs)
            if loss <= gain and not over_cap and not under_worth and not over_worth:
                continue
            if under_worth:
                why = "below_worth"
            elif over_worth:
                why = "above_worth"
            else:
                why = "over_team_cap" if over_cap else "loses_value"
            try:
                ctx.api.cancel(o["id"])
                if o.get("thread"):
                    try:
                        ctx.api.close_thread(o["thread"])
                    except BazaarError:
                        pass
                ctx.log("guard", "cancelled", offer=o["id"], thread=o.get("thread"), why=why, give=out_refs or g.get("cash"),
                        want=in_refs or w.get("cash"), loss=round(loss, 1), gain=round(gain, 1))
            except BazaarError as e:
                ctx.log("guard", "cancel_refused", offer=o["id"], error=str(e)[:160])
