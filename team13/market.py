"""Our own market: open it as soon as the rules allow, make it the best place to trade, and invite every team.

Why it matters: market-making is 30 points. Most come from the Market Test (our broker's matching quality, see
smart_broker.py); the rest from the value OTHER teams create trading on our venue. Fees earned never score, so:
  - fee 0% (Saturday default): match El Duende / El Rastro Express, beat Team 6's 0.5%, and never block thin
    Market Test pairs (El Rastro still takes 5% + 1 P per card);
  - a board venue run by our smart broker (fair midpoint matching, best pairs first, stall floor);
  - announcements on the big screen through the broker, and one personal invitation per team per game day,
    written by Claude when the AI negotiator is on.
We can never trade on our own venue (self_venue), so this module only ever helps others trade.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from bazaar_sdk import BazaarError, Broker

VENUE_NAME = "El Club · Where Madrid Trades"      # chosen by the team; used only when (re)opening (an open market cannot be renamed)
DESCRIPTION = ("Madrid's top-tier market: {fee} fee, no per-card charge, a smart broker matching every tick, best "
               "pairs first. Built by the team leading the board.")
# FOMO, but only true claims: fee comparison, matching every tick, first come first matched
PITCH = ("Hola! Team 13 here, top of the board. Our market Mercado Trece ({venue}) is the club where Madrid trades: "
         "{fee} fee, no per-card charge (El Rastro takes 5% + 1 P per card), and a broker matching every tick. "
         "The earliest offers get matched first.")
ANNOUNCE = ["Mercado Trece: Madrid's top-tier market. {fee} fee, no per-card charge, matched every tick.",
            "Still paying El Rastro 5% + 1 P per card? Mercado Trece charges {fee}. Keep your primas.",
            "First come, first matched: list on Mercado Trece now, our broker crosses offers every tick.",
            "The leading team runs Mercado Trece: {fee} fee, fair midpoint prices, best pairs first.",
            "Mercado Trece, the club where Madrid trades: {fee} fee, no per-card charge. Are you in?"]


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
        self.force_zero_for_bench(tick)  # fee changes need a notice: drop before the Market Test
        self.fee_safety()
        self.sync_fee(tick)
        if tick - st.get("announce_tick", -99) >= 20:
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
    def live_fee_bps(self):
        """Fee currently advertised on our open venue (from /api/venues), else the last fee we set."""
        st = self.ctx.state
        for v in getattr(self.ctx, "venues", None) or []:
            if v.get("venue") == st.get("venue"):
                return int(v.get("fee_bps", st.get("fee_set") or 0))
        return int(st.get("fee_set") or self.ctx.S.get("venue_fee_bps") or 0)

    def drop_fee_to_zero(self, reason, **detail):
        """Persist venue_fee_bps=0 so sync_fee announces the change (worth up to 30 Market Test points)."""
        import strategy
        ctx = self.ctx
        if not ctx.S.get("venue_fee_bps") and self.live_fee_bps() == 0:
            return False
        cur = strategy.load()
        strategy.save({k: v for k, v in cur.items() if v != strategy.defaults().get(k)} | {"venue_fee_bps": 0})
        ctx.S["venue_fee_bps"] = 0
        ctx.log("market", "fee_dropped_for_market_test", reason=reason, **detail)
        return True

    def sync_fee(self, tick):
        """Apply the Strategy tab's fee to our open market (fee changes take effect after a public notice)."""
        ctx, st = self.ctx, self.ctx.state
        want = int(ctx.S["venue_fee_bps"])
        live = self.live_fee_bps()
        if st.get("fee_set") == want and live == want:
            return
        if tick - st.get("fee_try_tick", -99) < 10:
            return
        st["fee_try_tick"] = tick
        try:
            ctx.api.set_fee(st["venue"], want, 0)
            st["fee_set"] = want
            ctx.log("market", "fee_changed", fee_bps=want, was_live=live)
        except BazaarError as e:
            ctx.log("market", "fee_change_refused", fee_bps=want, error=str(e)[:200])

    def force_zero_for_bench(self, tick):
        """Drop fee to 0% as soon as a Market Test is on the schedule or already in the book.

        Fee changes need a public notice, so waiting for a refused match during the test is too late.
        """
        ctx, st = self.ctx, self.ctx.state
        if not ctx.S.get("venue_fee_bps") and self.live_fee_bps() == 0:
            return
        # Live bench offers in our book (broker thread may already be matching)
        if tick - st.get("bench_sched_tick", -99) >= 5:
            st["bench_sched_tick"] = tick
            try:
                up = [u for u in ctx.raw.schedule().get("upcoming", []) if u.get("action") == "bench"]
                if up:
                    self.drop_fee_to_zero("upcoming_bench", when=up[0].get("at") or up[0].get("game_hour"))
                    return
            except BazaarError as e:
                ctx.log("market", "bench_schedule_failed", error=str(e)[:120])
        blog = Path(__file__).parent / "logs" / "broker.jsonl"
        if blog.exists():
            # Cheap tail check: any recent book event with bench>0 while we still charge a fee
            for line in blog.read_text().splitlines()[-30:]:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("event") == "book" and r.get("bench", 0) > 0:
                    self.drop_fee_to_zero("bench_live", tick=r.get("tick"))
                    return

    def fee_safety(self):
        """If the Market Test had a match refused or silently fee-blocked, drop the fee to 0%."""
        ctx, st = self.ctx, self.ctx.state
        if not ctx.S.get("venue_fee_bps") and self.live_fee_bps() == 0:
            return
        blog = Path(__file__).parent / "logs" / "broker.jsonl"
        if not blog.exists():
            return
        seen = st.get("fee_safety_lines", 0)
        lines = blog.read_text().splitlines()
        st["fee_safety_lines"] = len(lines)
        for line in lines[seen:]:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            ev = r.get("event")
            if ev == "fee_blocked":
                self.drop_fee_to_zero("fee_blocked", sell=r.get("sell"), buy=r.get("buy"))
                return
            if ev == "match_refused" and str(r.get("sell", "")).startswith("b") and "fee" in str(r.get("error", "")).lower():
                self.drop_fee_to_zero("match_refused", error=str(r.get("error"))[:160])
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

    # ------------------------------------------------------------------ marketing
    def announce(self, tick):
        ctx, st = self.ctx, self.ctx.state
        text = ANNOUNCE[(st.get("announce_n", 0)) % len(ANNOUNCE)].format(fee=fee_text(int(ctx.S["venue_fee_bps"])))
        try:
            Broker(ctx.raw.url, st["broker_key"]).announce(text)
            st["announce_tick"], st["announce_n"] = tick, st.get("announce_n", 0) + 1
            ctx.log("market", "announced", text=text)
        except BazaarError as e:
            st["announce_tick"] = tick  # do not retry every tick
            ctx.log("market", "announce_refused", error=str(e)[:200])

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
        teams = [t["team"] for t in (ctx.leaderboard or []) if t["team"] != ctx.me["id"]]
        target = next((t for t in teams if inv.get(t) != day), None)
        if not target:
            return
        venue = st["venue"]
        ft = fee_text(int(ctx.S["venue_fee_bps"]))
        text = PITCH.format(venue=venue, fee=ft)
        if ctx.S.get("llm_negotiator", 1):
            situation = {"counterparty": f"team {target}", "goal": "invite them to list and trade on our new market",
                         "facts": {"our_market": VENUE_NAME.format(fee=ft), "venue_id": venue, "fee": ft + " (no per-card charge)",
                                   "el_rastro_fee": "5% + 1 P per card", "matching": "fair midpoint, best pairs first"},
                         "instruction": "Write a short, friendly invitation. Only state the facts given. No price needed."}
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
