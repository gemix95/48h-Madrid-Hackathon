"""Can a team actually pay? Cash bounds for any team, rebuilt from the public feed.

Team cash is private, but every team starts with the same 400 P and every cash movement that matters is public:
settlements (who gave which items to whom, at what price), venue openings (250 P bond + 20 P) and the daily
allowances. Only the fees are uncertain (the side that accepts pays them, and the feed does not say who accepted),
so we keep a corridor:
  high = cash if the team never paid a fee        -> above it the team surely cannot pay
  low  = cash if the team paid every fee it touched -> below it the team surely can

An accepted offer whose maker cannot pay fails at settlement and wastes our one accept of the tick, so the trader
and the flipper skip bids whose cash is above the maker's `high`.
"""
from __future__ import annotations

START_CASH = 400
DAILY_ALLOWANCE = {"sat": 150, "sun": 150}  # /api/schedule grant_all on Saturday and Sunday mornings
VENUE_COST = 270


def cash_bounds(events, team: str) -> tuple:
    """(low, high) estimate of `team`'s cash after `events` (public feed events, any order)."""
    low = high = START_CASH
    for e in sorted(events, key=lambda e: e.get("id", 0)):
        p, t = e.get("payload") or {}, e.get("type")
        if t == "day.opened" and p.get("day") in DAILY_ALLOWANCE:
            low += DAILY_ALLOWANCE[p["day"]]
            high += DAILY_ALLOWANCE[p["day"]]
        elif t == "gift.given" and p.get("team") == team and p.get("cash"):
            low += p["cash"]
            high += p["cash"]
        elif t == "venue.opened" and p.get("owner") == team:
            low -= VENUE_COST
            high -= VENUE_COST
        elif t == "settlement" and team in (p.get("parties") or []):
            items = p.get("items") or []
            price, fee = p.get("price") or 0, p.get("fee") or 0
            got = any(i.get("to") == team for i in items)
            gave = any(i.get("frm") == team for i in items)
            if got and not gave:      # bought: paid the price
                low -= price
                high -= price
            elif gave and not got:    # sold: received the price
                low += price
                high += price
            low -= fee                # maybe we paid the fee (accepting side): only the pessimistic bound
    return low, high


class Solvency:
    """Per-tick cache of cash bounds from ctx.intel's events."""

    def __init__(self, ctx):
        self.ctx, self._tick, self._cache = ctx, None, {}

    def unexplained(self) -> float:
        """Cash we really have minus what the feed explains for us. Grants reach every team alike, so a positive gap
        (an allowance under another event type, a changed amount) is added to every team's high bound."""
        me = getattr(self.ctx, "me", None) or {}
        intel = getattr(self.ctx, "intel", None)
        if not intel or "cash" not in me or not me.get("id"):
            return 0.0
        return max(0.0, me["cash"] - cash_bounds(intel.events.values(), me["id"])[1])

    def bounds(self, team: str):
        intel = getattr(self.ctx, "intel", None)
        if not intel or not team or not (team[:1] == "t" and team[1:].isdigit()):
            return None
        tick = self.ctx.clock.get("tick")
        if tick != self._tick:
            self._tick, self._cache = tick, {}
        if team not in self._cache:
            if "_gap" not in self._cache:
                self._cache["_gap"] = self.unexplained()
            lo, hi = cash_bounds(intel.events.values(), team)
            self._cache[team] = (lo, hi + self._cache["_gap"])
        return self._cache[team]

    SLACK = 10  # unseen cash events (a grant under another event type) must not block a good deal

    def cannot_pay(self, team: str, cash: int) -> bool:
        if not self.ctx.S.get("solvency_check", 0):  # Strategy tab switch, in case the bounds are off
            return False
        b = self.bounds(team)
        return bool(b) and cash > b[1] + self.SLACK
