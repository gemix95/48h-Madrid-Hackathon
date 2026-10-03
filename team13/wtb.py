"""Want-to-buy: ask the teams that probably hold a card we need, and do not value it, to sell it to us.

Friday showed the pattern: t04 sold SAL-09 once asked (it does not collect Salamanca), t17 refused SAL-10 (it does),
and offers inside threads die after 2 ticks. So we ask with a direct offer on a market (`to: <team>`, long expiry)
and let the holder accept when its agent looks; the accepting side pays the venue fee, so we post on the cheapest
market other than ours.

Who: holders from the public card ledger (agent/ledger.py: settlements, gifts, listings, rarest card), skipping
teams that collect that set (agent/team_intel.py lean > 0), teams with no public activity lately, and untrusted ones.
Price: book x wtb_price_share, never above our value minus trade_min_gain, the team cap (agent/caps.json) or our
free cash. Short of cash, a swap instead: one of our spares (values.spares()) from a set the holder collects, kept
only if it still leaves us the minimum gain; swaps need no cash, and Mercado Trece charges no fee on them. At most wtb_max_open asks at once, one new ask per tick, the same team and card once per 60 ticks.
The guard cancels an ask once we own the card (a second copy is worth less than the price).
"""
from __future__ import annotations

import math
import os
import sys

from bazaar_sdk import BazaarError
from trader import team_caps
from venues import safe_markets

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))
from ledger import build  # noqa: E402
from team_intel import lean  # noqa: E402

RETRY_TICKS = 60
ACTIVE_TICKS = 120  # a team with no public event this recent is treated as asleep


class Asker:
    def __init__(self, ctx):
        self.ctx = ctx

    def _venue(self, to=None):
        """Cheapest market owned by a team well behind us (never ours, never the addressee's), else El Rastro:
        a trade on a close rival's market scores for that rival (venues.safe_markets)."""
        return safe_markets(self.ctx, to)[0]

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

    def _swap_card(self, ref, team, leans, locked, gain):
        """Our spare card that `team` collects and that leaves us at least the minimum gain, or None."""
        ctx, v = self.ctx, self.ctx.values
        best = None
        for a in v.spares(reserve=int(self.ctx.S.get("workshop_spares", 0))):
            if a["id"] in locked or a["ref"] == ref or leans.get(team, {}).get(a["ref"][:3], 0) <= 0:
                continue  # only a card from a set they collect makes the swap attractive to them
            net = gain - v.loss_of_removing([a["ref"]])
            if net >= ctx.S["trade_min_gain"] and (best is None or net > best[0]):
                best = (net, a)
        return best

    def plan(self):
        """[(ref, team, price or None, our asset or None)] asks worth posting now, best first (no API writes).
        Cash when we can afford it, else a swap of one of our spares from a set the holder collects."""
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
        locked = ctx.locked_assets() if hasattr(ctx, "locked_assets") else set()
        out = []
        rival = self._rival_bids(events, now)
        pending = {t[5:] for o in ctx.my_offers if o.get("maker") == me and o.get("to")
                   for t in (o.get("want") or {}).get("types") or [] if t.startswith("card:")}
        for ref, gain in v.wishlist(limit=40):
            if ref in pending:
                continue
            book = v.book(ref)
            completer = gain > book * v.m(ref) * 1.2
            price = math.floor(min(book * S.get("wtb_price_share", 0.9), gain - S["trade_min_gain"], caps.get(ref, 10 ** 9)))
            room = free
            if completer:  # the page bonus makes it worth outbidding other teams and spending below the cash floor
                room = ctx.me.get("cash", 0) - S.get("completer_keep_cash", 10) - open_cash
                price = math.floor(min(max(price, rival.get(ref, 0) + 4), gain * 0.6, caps.get(ref, 10 ** 9)))
            cash_ok = price >= 0.6 * book and price <= room  # a lowball ask (2 P for a 10 P card) only annoys the holder
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
                if cash_ok:
                    out.append((gain - price, ref, team, price, []))
                elif completer and room >= 0.4 * book:
                    mix = self._cash_and_spares(ref, team, leans, locked, gain, price, room)
                    if mix:
                        out.append(mix)
                elif S.get("wtb_swaps", 1):
                    sw = self._swap_card(ref, team, leans, locked, gain)
                    if sw:
                        out.append((sw[0], ref, team, None, [sw[1]]))
        out.sort(key=lambda x: -x[0])
        return [(ref, team, price, assets) for _, ref, team, price, assets in out]

    def _cash_and_spares(self, ref, team, leans, locked, gain, price, room):
        """Short of cash for a page completer: all the cash we can spare plus our spares (from sets the holder
        collects first) until their book value covers the rest of `price`."""
        v, cash = self.ctx.values, math.floor(room)
        spares = [a for a in v.spares(reserve=int(self.ctx.S.get("workshop_spares", 0)))
                  if a["id"] not in locked and a["ref"] != ref]
        spares.sort(key=lambda a: (-leans.get(team, {}).get(a["ref"][:3], 0), v.loss_of_removing([a["ref"]]) - v.book(a["ref"])))
        give, worth, refs = [], cash, []
        for a in spares:
            if worth >= price or len(give) >= 3:
                break
            give.append(a)
            refs.append(a["ref"])
            worth += v.book(a["ref"])
        if worth < price:
            return None
        net = gain - cash - v.loss_of_removing(refs)
        return (net, ref, team, cash, give) if net >= 3 * self.ctx.S["trade_min_gain"] else None

    @staticmethod
    def _rival_bids(events, now):
        """Best open cash bid another team has posted for each card (ref -> P), from the public feed."""
        best = {}
        for e in events:
            p = e.get("payload") or {}
            o = p.get("offer") if isinstance(p.get("offer"), dict) else None
            if e.get("type") != "offer.listed" or not o or o.get("maker") == "t13":
                continue
            if (o.get("expires_tick") or 0) < now or (o.get("give") or {}).get("assets"):
                continue
            cash = (o.get("give") or {}).get("cash") or 0
            for t in (o.get("want") or {}).get("types") or []:
                if t.startswith("card:") and cash > best.get(t[5:], 0):
                    best[t[5:]] = cash
        return best

    def step(self):
        ctx, S = self.ctx, self.ctx.S
        me = ctx.me.get("id")
        mine = [o for o in ctx.my_offers if o.get("maker") == me and o.get("to")]  # our direct asks, cash or swap
        if len(mine) >= int(S.get("wtb_max_open", 3)):
            return
        plan = self.plan()
        if not plan:
            return
        ref, team, price, assets = plan[0]
        venue = self._venue(team)
        give = {}
        if price:
            give["cash"] = price
        if assets:
            give["assets"] = [a["id"] for a in assets]
        swap = [a["ref"] for a in assets] or None
        try:
            o = ctx.api.list_offer(give, {"cards": [ref]}, venue=venue, to=team, expires_in_ticks=int(S.get("wtb_ticks", 120)))
            ctx.state.setdefault("wtb", {})[f"{team}:{ref}"] = ctx.clock.get("tick", 0)
            ctx.log("wtb", "asked", ref=ref, team=team, price=price, swap=swap, venue=venue, offer=o.get("id"))
        except BazaarError as e:
            ctx.state.setdefault("wtb", {})[f"{team}:{ref}"] = ctx.clock.get("tick", 0)
            ctx.log("wtb", "ask_refused", ref=ref, team=team, price=price, swap=swap, error=str(e)[:160])
