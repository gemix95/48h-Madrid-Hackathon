"""Our own market: open it as soon as the rules allow, make it the best place to trade, and invite every team.

Why it matters: market-making is 30 points. Most come from the Market Test (our broker's matching quality, see
smart_broker.py); the rest from the value OTHER teams create trading on our venue. Fees earned never score, so:
  - fee 0%: the cheapest market in the game (El Rastro charges 5% + 1 P per card);
  - a board venue run by our smart broker (fair midpoint matching, best pairs first);
  - announcements on the big screen through the broker, and one personal invitation per team per game day,
    written by Claude when the AI negotiator is on — prioritising teams actively listing on El Rastro.
We can never trade on our own venue (self_venue), so this module only ever helps others trade.
"""
from __future__ import annotations

import threading

from bazaar_sdk import BazaarError, Broker

VENUE_NAME = "El Club · Where Madrid Trades"      # chosen by the team; used only when (re)opening (an open market cannot be renamed)
DESCRIPTION = ("Madrid's top-tier market: {fee} fee, no per-card charge, a smart broker matching every tick, best "
               "pairs first. Built by the team leading the board.")
# FOMO, but only true claims: fee comparison, matching every tick, first come first matched, no per-card charge
PITCH = ("Hola! Team 13 — Mercado Trece ({venue}). {fee} fee, no per-card charge (El Rastro takes 5% + 1 P per card). "
         "Smart broker matches every tick, best pairs first; earliest offers get matched first. "
         "List or bid on Mercado Trece and keep the primas.")
ANNOUNCE = [
    "Mercado Trece: {fee} fee, no per-card charge, smart broker matching every tick. List now.",
    "Still on El Rastro? 5% + 1 P per card vs Mercado Trece at {fee}. Same cards, more primas left.",
    "Mercado Trece matches every tick (best pairs first). First come, first matched — get on the book.",
    "Leading team, zero cut: Mercado Trece is {fee}, no per-card fee. Where Madrid should trade.",
    "Card-for-card swaps on Mercado Trece: no per-card charge. {fee} fee on cash legs. Are you in?",
]
PRE_TEST = ("Market Test soon: list on Mercado Trece ({venue}) now — {fee} fee, smart broker, matched every tick. "
            "Don't leave liquidity on El Rastro.")

ANNOUNCE_EVERY = 20          # normal cadence (ticks)
ANNOUNCE_PRETEST_EVERY = 10  # denser reminders when a bench session is near
PRETEST_HOURS = 0.35         # ~ game-hour window before a scheduled Market Test


def fee_text(bps) -> str:
    return "0%" if not bps else f"{bps / 100:g}%"


class Market:
    def __init__(self, ctx):
        self.ctx = ctx
        self.broker_thread = None

    def step(self):
        ctx, S = self.ctx, self.ctx.S
        st = ctx.state
        if not st.get("venue"):
            return self.try_open()
        self.ensure_broker()
        tick = ctx.clock.get("tick", 0)
        self.fee_safety()
        self.sync_fee(tick)
        every = ANNOUNCE_PRETEST_EVERY if self.bench_soon() else ANNOUNCE_EVERY
        if tick - st.get("announce_tick", -99) >= every:
            self.announce(tick)
        self.invite(tick)

    # ------------------------------------------------------------------ opening
    def try_open(self):
        ctx, S, st = self.ctx, self.ctx.S, self.ctx.state
        if ctx.me.get("level", 1) < 2:
            return
        if ctx.me["cash"] < 270 + 1:
            if st.get("market_wait_log") != ctx.me["cash"]:
                ctx.log("market", "waiting_for_cash", cash=ctx.me["cash"], need=271)
                st["market_wait_log"] = ctx.me["cash"]
            return
        if ctx.clock.get("tick", 0) - st.get("venue_try_tick", -99) < 10:
            return
        st["venue_try_tick"] = ctx.clock.get("tick", 0)
        try:
            ft = fee_text(int(S["venue_fee_bps"]))
            res = ctx.api.open_venue(VENUE_NAME.format(fee=ft)[:40], fee_bps=int(S["venue_fee_bps"]), fee_per_card=0,
                                     rules={"mechanism": "board"}, description=DESCRIPTION.format(fee=ft))
            st["venue"] = res.get("venue") or res.get("id")
            st["broker_key"] = res.get("broker_key")
            st["fee_set"] = int(S["venue_fee_bps"])
            ctx.log("market", "opened", venue=st["venue"], fee_bps=int(S["venue_fee_bps"]))
        except BazaarError as e:
            ctx.log("market", "open_refused", code=e.code, error=str(e)[:200])  # venue_not_live: team markets open at +3h

    # ------------------------------------------------------------------ fee: follow the Strategy tab, with a safety
    def sync_fee(self, tick):
        """Apply the Strategy tab's fee to our open market (fee changes take effect after a public notice)."""
        ctx, st = self.ctx, self.ctx.state
        want = int(ctx.S["venue_fee_bps"])
        if st.get("fee_set") == want or tick - st.get("fee_try_tick", -99) < 10:
            return
        st["fee_try_tick"] = tick
        try:
            ctx.api.set_fee(st["venue"], want, 0)
            st["fee_set"] = want
            ctx.log("market", "fee_changed", fee_bps=want)
        except BazaarError as e:
            ctx.log("market", "fee_change_refused", fee_bps=want, error=str(e)[:200])

    def fee_safety(self):
        """If the Market Test ever had a match refused because of our fee, drop the fee to 0% (it is worth 30 points)."""
        import json as _json
        from pathlib import Path
        ctx, st = self.ctx, self.ctx.state
        if not ctx.S["venue_fee_bps"]:
            return
        log = Path(__file__).parent / "logs" / "broker.jsonl"
        if not log.exists():
            return
        seen = st.get("fee_safety_lines", 0)
        lines = log.read_text().splitlines()
        st["fee_safety_lines"] = len(lines)
        for line in lines[seen:]:
            try:
                r = _json.loads(line)
            except ValueError:
                continue
            if r.get("event") == "match_refused" and str(r.get("sell", "")).startswith("b") and "fee" in str(r.get("error", "")).lower():
                import strategy
                cur = strategy.load()
                cur["venue_fee_bps"] = 0
                strategy.save({k: v for k, v in cur.items() if v != strategy.defaults().get(k)} | {"venue_fee_bps": 0})
                ctx.S["venue_fee_bps"] = 0
                ctx.log("market", "fee_dropped_for_market_test", error=str(r.get("error"))[:160])
                return

    # ------------------------------------------------------------------ broker (Market Test + real offers)
    def ensure_broker(self):
        key = self.ctx.state.get("broker_key")
        if not key or (self.broker_thread and self.broker_thread.is_alive()):
            return
        import smart_broker
        self.broker_thread = threading.Thread(target=smart_broker.run, args=(self.ctx.raw.url, key), daemon=True)
        self.broker_thread.start()
        self.ctx.log("market", "broker_started", venue=self.ctx.state.get("venue"))

    # ------------------------------------------------------------------ schedule helpers
    def next_bench_hours(self):
        """Hours until the next Market Test on /api/schedule, or None if unknown / none soon."""
        ctx, st = self.ctx, self.ctx.state
        tick = ctx.clock.get("tick", 0)
        cached = st.get("bench_eta")
        if cached and tick - st.get("bench_eta_tick", -99) < 5:
            return cached.get("eta")
        eta = None
        try:
            sched = ctx.raw.schedule()
            now = float(sched.get("now_hours") or ctx.clock.get("t_hours") or 0)
            ups = [u for u in sched.get("upcoming", []) if u.get("action") == "bench"]
            if ups:
                at = float(ups[0].get("at_hours") or ups[0].get("at") or 0)
                eta = max(0.0, at - now)
        except BazaarError as e:
            ctx.log("market", "bench_schedule_failed", error=str(e)[:120])
        st["bench_eta"] = {"eta": eta}
        st["bench_eta_tick"] = tick
        return eta

    def bench_soon(self) -> bool:
        eta = self.next_bench_hours()
        return eta is not None and eta <= PRETEST_HOURS

    # ------------------------------------------------------------------ marketing
    def announce(self, tick):
        ctx, st = self.ctx, self.ctx.state
        ft = fee_text(int(ctx.S["venue_fee_bps"]))
        venue = st.get("venue") or "v03"
        if self.bench_soon():
            text = PRE_TEST.format(fee=ft, venue=venue)
        else:
            text = ANNOUNCE[(st.get("announce_n", 0)) % len(ANNOUNCE)].format(fee=ft, venue=venue)
        try:
            Broker(ctx.raw.url, st["broker_key"]).announce(text)
            st["announce_tick"], st["announce_n"] = tick, st.get("announce_n", 0) + 1
            ctx.log("market", "announced", text=text, pretest=bool(self.bench_soon()))
        except BazaarError as e:
            st["announce_tick"] = tick  # do not retry every tick (respect API limits)
            ctx.log("market", "announce_refused", error=str(e)[:200])

    def invite_targets(self, inv, day):
        """Teams to invite today, active El Rastro listers / holders first, then the rest of the board.

        Threads always open on El Rastro (self_venue forbids inviting on our own market).
        """
        ctx = self.ctx
        me = ctx.me["id"]
        lb = [t["team"] for t in (ctx.leaderboard or []) if t["team"] != me]
        seen, ordered = set(), []

        def add(team):
            if team and team != me and team[:1] == "t" and team[1:].isdigit() and team not in seen and inv.get(team) != day:
                seen.add(team)
                ordered.append(team)

        tick = ctx.clock.get("tick", 0)
        intel = getattr(ctx, "intel", None)
        if intel:
            summ = intel.summary()
            # Recent public listings = teams already posting inventory (highest FOMO leverage).
            recent = sorted((l for l in summ.get("listings", []) if tick - l.get("tick", 0) <= 80),
                            key=lambda l: -l.get("tick", 0))
            for l in recent:
                add(l.get("maker"))
            # Settlement counterparties who still show as holders in the feed.
            for e in list(intel.events.values())[-200:]:
                if e.get("type") != "settlement":
                    continue
                for it in (e.get("payload") or {}).get("items", []):
                    to = it.get("to") or ""
                    if it.get("kind") == "card":
                        add(to)
        for t in lb:
            add(t)
        return ordered

    def invite(self, tick):
        """One personal invitation per team per game day; at most one invitation conversation open at a time,
        closed after a few ticks so dealer haggling keeps its conversation slots."""
        ctx, st = self.ctx, self.ctx.state
        day = ctx.clock.get("today", "day")
        inv = st.setdefault("invites", {})
        open_inv = st.setdefault("invite_threads", {})
        for tid, info in list(open_inv.items()):  # close stale invitations nobody answered
            if tick - info["tick"] >= 6:
                th = next((t for t in ctx.threads if str(t["id"]) == tid), None)
                if th and th["status"] == "open" and not any(m.get("sender") != ctx.me["id"] for m in th.get("messages", [])):
                    try:
                        ctx.api.close_thread(int(tid))
                    except BazaarError:
                        pass
                open_inv.pop(tid)
        if open_inv or len([t for t in ctx.threads if t["status"] == "open"]) >= ctx.limit("max_open_threads_per_team", 6) - 2:
            return
        target = next(iter(self.invite_targets(inv, day)), None)
        if not target:
            return
        venue = st["venue"]
        ft = fee_text(int(ctx.S["venue_fee_bps"]))
        text = PITCH.format(venue=venue, fee=ft)
        if self.bench_soon():
            text = PRE_TEST.format(venue=venue, fee=ft) + " " + text
        if ctx.S.get("llm_negotiator", 1):
            situation = {"counterparty": f"team {target}", "goal": "invite them to list and trade on our market Mercado Trece",
                         "facts": {"our_market": "Mercado Trece", "venue_id": venue, "fee": ft + " (no per-card charge)",
                                   "el_rastro_fee": "5% + 1 P per card",
                                   "matching": "smart broker every tick, fair midpoint, best pairs first",
                                   "market_test_soon": bool(self.bench_soon())},
                         "instruction": "Write a short, friendly invitation. Only state the facts given. No price needed. Create FOMO without false claims."}
            text, _, _ = ctx.speak(situation, (0, 0), (text, 0))
        try:
            th = ctx.api.open_thread(target, venue="rastro")  # not on our own market: self_venue forbids it
            ctx.api.say(th["id"], text)
            inv[target] = day
            open_inv[str(th["id"])] = {"team": target, "tick": tick}
            ctx.log("market", "invited", team=target, text=text[:200])
        except BazaarError as e:
            inv[target] = day  # do not hammer a team that refuses
            ctx.log("market", "invite_refused", team=target, error=str(e)[:200])
