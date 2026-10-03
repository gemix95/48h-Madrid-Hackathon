"""Want-to-buy: ask the teams that probably hold a card we need, and do not value it, to sell it to us.

Friday showed the pattern: t04 sold SAL-09 once asked (it does not collect Salamanca), t17 refused SAL-10 (it does),
and offers inside threads die after 2 ticks. So we ask with a direct offer on a market (`to: <team>`, long expiry)
and let the holder accept when its agent looks; the accepting side pays the venue fee, so we post on the cheapest
market other than ours.

Who: holders from the public card ledger (agent/ledger.py: settlements, gifts, listings, rarest card), skipping
teams that collect that set (agent/team_intel.py lean > 0), teams with no public activity lately, and untrusted ones.
Price: book x wtb_price_share, never above our value minus trade_min_gain, the team cap (agent/caps.json) or our
free cash. At most wtb_max_open asks at once, one new ask per tick, the same team and card once per 60 ticks.
The guard cancels an ask once we own the card (a second copy is worth less than the price).
"""
from __future__ import annotations

import math
import os
import sys

from bazaar_sdk import BazaarError
from trader import team_caps

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))
from ledger import build  # noqa: E402
from team_intel import lean  # noqa: E402

RETRY_TICKS = 60
ACTIVE_TICKS = 120  # a team with no public event this recent is treated as asleep


class Asker:
    def __init__(self, ctx):
        self.ctx = ctx

    def _venue(self):
        """Cheapest open market for the seller (it accepts, so it pays the fee), never our own."""
        best = None
        for v in getattr(self.ctx, "venues", None) or []:
            if v.get("venue") == self.ctx.state.get("venue") or v.get("status", "open") != "open":
                continue
            cost = (v.get("fee_bps", 500), v.get("fee_per_card", 1))
            if best is None or cost < best[0]:
                best = (cost, v["venue"])
        return best[1] if best else "rastro"

    def _active(self, events, now):
        """Teams that did something themselves lately (deals, messages, listings, threads); a level the server
        opened for everyone is not activity (t11 never moved on Friday)."""
        seen = {}
        for e in events:
            p, t = e.get("payload") or {}, e.get("type")
            o = p.get("offer") if isinstance(p.get("offer"), dict) else {}
            if t == "settlement":
                who = [x for x in p.get("parties") or [] if x != p.get("persona")]
            elif t == "thread.message":
                who = [p.get("sender")]
            elif t == "offer.listed":
                who = [o.get("maker")]
            elif t == "thread.opened":
                who = [p.get("team")]
            else:
                continue
            for team in who:
                if team and team[:1] == "t" and team[1:].isdigit():
                    seen[team] = max(seen.get(team, -1), e.get("tick", -1))
        return {t for t, tick in seen.items() if now - tick <= ACTIVE_TICKS}

    def plan(self):
        """[(ref, team, price)] asks worth posting now, best first (no API writes)."""
        ctx, S, v = self.ctx, self.ctx.S, self.ctx.values
        intel = getattr(ctx, "intel", None)
        if v is None or intel is None:
            return []
        events = sorted(intel.events.values(), key=lambda e: e.get("id", 0))
        now = ctx.clock.get("tick", 0)
        board = {"teams": getattr(ctx, "leaderboard", None) or []}  # each team's rarest card is evidence too
        hold, leans, active = build(events, board), lean(events), self._active(events, now)
        caps, me = team_caps(), ctx.me.get("id")
        asks = ctx.state.setdefault("wtb", {})  # "team:ref" -> tick asked
        open_cash = sum((o.get("give") or {}).get("cash") or 0 for o in ctx.my_offers
                        if o.get("maker") == me and o.get("to") and not (o.get("give") or {}).get("assets"))
        free = ctx.me.get("cash", 0) - ctx.reserve() - open_cash
        out = []
        for ref, gain in v.wishlist(limit=40):
            book = v.book(ref)
            price = math.floor(min(book * S.get("wtb_price_share", 0.9), gain - S["trade_min_gain"], caps.get(ref, 10 ** 9)))
            if price < 0.6 * book or price > free:  # a lowball ask (2 P for a 10 P card) only annoys the holder
                continue
            for team, cards in hold.items():
                c = cards.get(ref)
                if team == me or not c or c[0] <= 0 or team not in active:
                    continue
                if leans.get(team, {}).get(ref[:3], 0) > 0:
                    continue  # it collects this set: it will not sell (t17 and SAL-10 on Friday)
                if hasattr(ctx, "is_untrusted") and ctx.is_untrusted(team):
                    continue
                if now - asks.get(f"{team}:{ref}", -10 ** 6) < RETRY_TICKS:
                    continue
                out.append((gain - price, ref, team, price))
        return [(ref, team, price) for _, ref, team, price in sorted(out, reverse=True)]

    def step(self):
        ctx, S = self.ctx, self.ctx.S
        me = ctx.me.get("id")
        mine = [o for o in ctx.my_offers if o.get("maker") == me and o.get("to") and not (o.get("give") or {}).get("assets")]
        if len(mine) >= int(S.get("wtb_max_open", 3)):
            return
        plan = self.plan()
        if not plan:
            return
        ref, team, price = plan[0]
        venue = self._venue()
        try:
            o = ctx.api.list_offer({"cash": price}, {"cards": [ref]}, venue=venue, to=team,
                                   expires_in_ticks=int(S.get("wtb_ticks", 120)))
            ctx.state.setdefault("wtb", {})[f"{team}:{ref}"] = ctx.clock.get("tick", 0)
            ctx.log("wtb", "asked", ref=ref, team=team, price=price, venue=venue, offer=o.get("id"))
        except BazaarError as e:
            ctx.state.setdefault("wtb", {})[f"{team}:{ref}"] = ctx.clock.get("tick", 0)
            ctx.log("wtb", "ask_refused", ref=ref, team=team, price=price, error=str(e)[:160])
