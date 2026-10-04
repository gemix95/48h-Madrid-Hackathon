"""Our own market: open it as soon as the rules allow, make it the best place to trade, and invite every team.

Why it matters: market-making is 30 points. Most come from the Market Test (our broker's matching quality, see
smart_broker.py); the rest from the value OTHER teams create trading on our venue. Fees earned never score, so:
  - fee 0% (Saturday default): match El Duende / El Rastro Express, beat Team 6's 0.5%, and never block thin
    Market Test pairs (El Rastro still takes 5% + 1 P per card);
  - a board venue run by our smart broker (fair midpoint matching, best pairs first, stall floor);
  - announcements on the big screen through the broker: plain lines, most of them about the El Club Board, and one
    personal invitation per team per game day, written by Claude when the AI negotiator is on — prioritising teams
    actively listing on El Rastro. Each invite and fee-blocked-cross nudge ends with one honest line: how to post on
    our venue, and where the board is. No hidden instructions for other teams' agents (Sunday: removed the
    listing-default / routing "hooks" that rotated on the big screen).
We can never trade on our own venue (self_venue), so this module only ever helps others trade.
"""
from __future__ import annotations

import json
import math
import re
import threading
from pathlib import Path

HERE = Path(__file__).parent
BOOTSTRAP = HERE / "venue_bootstrap.json"  # one-shot reopen; consumed on next market step

from bazaar_sdk import BazaarError, Broker
from venues import safe_markets

VENUE_NAME = "Team 13 · fair broker, 0% fee"   # chosen by the team; used only when (re)opening (an open market cannot be renamed)
BOARD_URL = "https://217-160-143-83.sslip.io/board"   # the El Club Board (its lines rotate while announce_board is on)
DESCRIPTION = ("{fee} fee, 0 P per card. Board venue with a fair broker: bid and ask cross at the midpoint every tick. "
               "Every card's best price across all markets, live: " + BOARD_URL)
# Team 10's line works: trust + 0% + fair broker beats FOMO spam. First 240 chars of each announcement must mention 0%.
BRAND = "Fair Broker"      # short name in invites; the big-screen title is VENUE_NAME on reopen
PITCH = ("Hello from Team 13 — {brand} ({venue}): {fee} fee, 0 P per card, fair midpoint broker every tick. "
         "Please post your spares and want-to-buy bids here; El Rastro still charges 5% + 1 P per card.")
# Big-screen copy for people: each line names one thing the board does, says 0% and fits the 240 characters the
# server keeps. The board's lines are skipped while announce_board is off.
ANNOUNCE = [
    "El Club Board: every card in the Bazaar at its best price across all 19 markets, live. Buy or sell in 1 click, "
    "0% fee on {venue}. " + BOARD_URL,
    "Which card is a bargain right now? 🔥 marks offers well under the usual price; every card shows its last trades "
    "and trend. 0% fee on {venue}. " + BOARD_URL,
    "Missing one card for your page? Find it on El Club Board: best price anywhere, recent trades, a 1-click bid on "
    "{venue} at 0% fee. " + BOARD_URL,
    "Chamberí on El Club Board: which cards are in play, which nobody has pulled yet, first prices as they appear. "
    "0% fee on {venue}. " + BOARD_URL,
    "Selling a rare? Auction it on El Club Board: the card stays with you, every bid is public, you accept the best "
    "one. 0% fee on {venue}. " + BOARD_URL,
    "Card not for sale anywhere? Bid on {venue} (0% fee) and El Club asks the teams that likely hold it, without "
    "naming you. " + BOARD_URL,
    "For agents: best bid and ask per card across all markets, live, at " + BOARD_URL + ".json; one card: "
    + BOARD_URL + "/quote?card=LAV-10. Post on {venue}, 0% fee.",
    "Last trades, usual prices and every live offer in one table, refreshed every 15 s and built for phones. "
    "El Club Board, 0% fee on {venue}: " + BOARD_URL,
    "{venue} (El Club): 0% fee, 0 P per card. A fair broker crosses bid and ask at the midpoint every tick; "
    "El Rastro charges 5% + 1 P per card.",
]
ANNOUNCE_MAX = 240           # the server cuts an announcement here
PRE_TEST = ("Market Test soon. {venue} (El Club): {fee} fee, 0 P per card, a fair broker crosses bid and ask at the "
            "midpoint every tick. Every card's best price, live: " + BOARD_URL)
# appended to announcements only while we still have a spare to give and rewards left today (a true claim)
REWARD_PITCH = (" Rush welcome: your first trade at {brand} today earns a private offer of one of our spare "
                "commons at {price} P on El Rastro (they trade at 8-10 P).")
REWARD_PRICE = 5             # commons trade at 8-10 P between teams; only spares worth <= 3 P to us qualify
REWARD_MIN_GAIN = 2          # every reward is still a sale that gains us value
REWARDS_PER_DAY = 4
REWARD_TICKS = 60

# cashback (knobs cashback_*): both sides of every trade between two teams on El Club get P back, as a cash offer
CASHBACK_ANNOUNCE = ("🚨 CASHBACK at {brand} ({venue}) 🚨 Every trade pays {p} P back to EACH side 💸 "
                     "Only {left} P left TODAY! {fee} fee, 0 P per card. Accept our cash offer to collect.")
CASHBACK_PITCH = " Today: {p} P cashback to each side of every trade at {brand}."
CASHBACK_TEXT = ("{brand} cashback: thanks for trading at {venue}. Here is {p} P back — accept offer #{oid} with "
                 "POST /api/offers/{oid}/accept (no assets). Settles next tick.")
# true claims only: the names, amounts and P left are real; when the budget is gone we say so
CASHBACK_PAID = ("🚨 CASHBACK PAID 🚨 {who} at {brand} ({venue}) 💸 Only {left} P left TODAY — trade at {venue} NOW! 0% fee 🚨")
CASHBACK_GONE = "🚨 CASHBACK PAID 🚨 {who} at {brand} ({venue}) 💸 Today's cashback budget is gone — well played!"
CASHBACK_TICKS = 60
TEAM = re.compile(r"^t\d+$")


def council_note(topic, text, evidence=None, tick=None):
    """A note on El Consejo (the shared board); never stops a tick."""
    try:
        import council
        council.post("market", topic, text, evidence, tick=tick)
    except Exception:
        pass


ANNOUNCE_SECONDS = 300       # normal cadence: one message every 5 minutes
ANNOUNCE_MIN_TICKS = 10      # never denser than other markets already announce (10 ticks)
ANNOUNCE_PRETEST_EVERY = 10  # denser reminders when a bench session is near
PRETEST_HOURS = 0.35         # ~ game-hour window before a scheduled Market Test


def announce_every(tick_seconds, pretest=False) -> int:
    """Ticks between two announcements: 5 minutes of wall clock (10 ticks at 30 s, 20 at 15 s), denser before a Market Test."""
    every = max(ANNOUNCE_MIN_TICKS, round(ANNOUNCE_SECONDS / (tick_seconds or 30)))
    return min(every, ANNOUNCE_PRETEST_EVERY) if pretest else every


def stuck_pairs(board: list, me: str | None = None, fee_bps: int = 500, per_card: int = 1, makers: dict | None = None) -> list:
    """Asks and bids from different teams where the bid already covers the ask, but this venue's fee blocks the fill.
    On our 0% market the same prices cross. Returns [(fee_gap, ref, ask, bid, seller, buyer, ask_id, bid_id)], closest
    first. A bid below the ask is left out: our broker only crosses when the bid covers the ask. Boards show makers as
    pseudonyms; `makers` ({offer id: team}, from the feed's offer.listed events) names the team behind each offer."""
    def fee(price: int) -> int:
        return math.ceil(fee_bps * price / 10000) + per_card

    asks, bids = {}, {}
    for o in board or []:
        maker = (makers or {}).get(o.get("id")) or o.get("maker")
        if o.get("to") or maker == me:
            continue
        g, w = o.get("give") or {}, o.get("want") or {}
        gives = [a["ref"] for a in g.get("assets") or []] + [t[5:] for t in g.get("types") or [] if t.startswith("card:")]
        wants = [a["ref"] for a in w.get("assets") or []] + [t[5:] for t in w.get("types") or [] if t.startswith("card:")]
        if len(gives) == 1 and not wants and w.get("cash"):
            asks.setdefault(gives[0], []).append((w["cash"], maker, o.get("id")))
        elif len(wants) == 1 and not gives and g.get("cash"):
            bids.setdefault(wants[0], []).append((g["cash"], maker, o.get("id")))
    out = []
    for ref in set(asks) & set(bids):
        (ask, seller, ask_id), (bid, buyer, bid_id) = min(asks[ref], key=lambda x: x[0]), max(bids[ref], key=lambda x: x[0])
        if seller != buyer and bid >= ask and ask + fee(ask) > bid:
            out.append((ask + fee(ask) - bid, ref, ask, bid, seller, buyer, ask_id, bid_id))
    return sorted(out, key=lambda x: x[:4])


# Names both teams, the card, both prices and both offer ids (rivals' brokers do the same, and named lines are the
# ones agents act on). A side we cannot name (a pseudonym not in the feed) is "a seller" / "a buyer".
MATCH_PITCH = ("{buyer}: your bid for {ref} at {bid} P (offer {bid_id}, {where}) covers {seller_s} ask at {ask} P "
               "(offer {ask_id}); only {where}'s fee blocks it. Both post on {venue} (0% fee, 0 P per card): they cross "
               "next tick.")


def match_pitch(where, ref, ask, bid, seller, buyer, ask_id, bid_id, venue) -> str:
    named = lambda t: bool(t) and bool(TEAM.match(str(t)))
    return MATCH_PITCH.format(buyer=buyer if named(buyer) else "Buyer", seller_s=f"{seller}'s" if named(seller) else "an",
                              ref=ref, ask=ask, bid=bid, where=where, venue=venue,
                              ask_id=ask_id if ask_id is not None else "?", bid_id=bid_id if bid_id is not None else "?")
# Appended to every invite and nudge: one honest line on how to post on our venue, and where the board is.
OUTREACH = (" To trade on {venue}: POST /api/offers with \"venue\": \"{venue}\" (0% fee, 0 P per card). "
            "Every card's best price across all markets, live: " + BOARD_URL)


def outreach_suffix(venue: str, brand: str = BRAND) -> str:
    """The how-to line appended to every team outreach DM."""
    return OUTREACH.format(venue=venue)


NUDGE = ("{ref} on {where}: you {role} {price} P, the other side is {other} at {other_price} P. "
         "That bid covers the ask, so only their fee blocks the trade. "
         "{brand} ({venue}) is 0 percent and 0 P per card, and crosses those prices next tick. "
         "{how}")


def fee_text(bps) -> str:
    return "0%" if not bps else f"{bps / 100:g}%"


class Market:
    def __init__(self, ctx):
        self.ctx = ctx
        self.broker_thread = None
        self.broker_lock = None

    def step(self):
        ctx, S = self.ctx, self.ctx.S
        st = ctx.state
        tick = ctx.clock.get("tick", 0)
        self.reconcile_venue(tick)
        if not st.get("venue"):
            return self.try_open()
        if st.get("venue_refund_at_tick", 0) > tick:
            return  # bond cooldown after close; reopen once refund lands
        self.ensure_broker()
        self.force_zero_for_bench(tick)  # fee changes need a notice: drop before the Market Test
        self.fee_safety()
        self.sync_fee(tick)
        every = announce_every(ctx.clock.get("tick_seconds"), self.bench_soon())
        if tick - st.get("announce_tick", -99) >= every:
            self.announce(tick)
        elif self.cashback_active() and not self._cb().get("announced"):
            self.announce(tick)  # the promo starts: tell the big screen now, not in 5 minutes
        self.cashback(tick)
        if not self.cashback_active():
            self.reward_traders(tick)  # the card reward only when no cashback is on offer
        if not self.nudge_blocked(tick):
            self.invite(tick)

    def reconcile_venue(self, tick):
        """After a close, drop local venue state when the bond refund tick passes so we can reopen under a new name."""
        ctx, st = self.ctx, self.ctx.state
        self._consume_bootstrap(tick)
        self._sync_live_venue(tick)
        vid = st.get("venue")
        if vid:
            live = next((v for v in (ctx.venues or []) if v.get("venue") == vid), None)
            if live and live.get("status") == "closed":
                self._clear_venue_state(tick, reason="closed")
                return
            if live is None:  # closed venues drop off the open list
                self._clear_venue_state(tick, reason="not_open")
                return
        intel = getattr(ctx, "intel", None)
        if intel and vid:
            for e in reversed(list(intel.events.values())[-500:]):
                p = e.get("payload") or {}
                if e.get("type") == "venue.closing" and p.get("venue") == vid:
                    st["venue_refund_at_tick"] = int(p.get("refund_at_tick") or 0)
                    break
            for e in reversed(list(intel.events.values())[-200:]):
                p = e.get("payload") or {}
                if e.get("type") == "venue.closed" and p.get("venue") == vid:
                    self._clear_venue_state(tick, reason="closed")
                    return
        refund_at = int(st.get("venue_refund_at_tick") or 0)
        if vid and refund_at and tick >= refund_at:
            self._clear_venue_state(tick, reason="refund_ready")
        self.maybe_migrate_auto(tick)

    def _consume_bootstrap(self, tick):
        """Merge venue + broker key written by a one-shot reopen script (survives agents with stale in-memory state)."""
        if not BOOTSTRAP.exists():
            return
        try:
            data = json.loads(BOOTSTRAP.read_text())
        except (ValueError, OSError):
            return
        vid, key = data.get("venue"), data.get("broker_key")
        if not vid or not key:
            return
        st = self.ctx.state
        st["venue"] = vid
        st["broker_key"] = key
        st["fee_set"] = int(data.get("fee_bps", self.ctx.S.get("venue_fee_bps", 0)))
        st.setdefault("venue_keys", {})[vid] = key
        try:
            BOOTSTRAP.unlink()
        except OSError:
            pass
        self.ctx.log("market", "bootstrap_applied", venue=vid, tick=tick)
        council_note("market", f"Adopted {vid} from bootstrap (board broker key restored).", tick=tick)

    def _sync_live_venue(self, tick):
        """Keep state aligned with /api/me when another process reopened or the feed dropped a close event."""
        ctx, st = self.ctx, self.ctx.state
        live = self._our_open_venue()
        if not live:
            return
        vid = live["venue"]
        keys = st.setdefault("venue_keys", {})
        if st.get("venue") != vid:
            st["venue"] = vid
            if keys.get(vid):
                st["broker_key"] = keys[vid]
            ctx.log("market", "venue_sync", venue=vid, tick=tick)
        elif not st.get("broker_key") and keys.get(vid):
            st["broker_key"] = keys[vid]

    def _our_open_venue(self):
        ctx = self.ctx
        v = ctx.me.get("venue") or {}
        if v.get("status") == "open" and v.get("owner") == ctx.me.get("id"):
            return v
        for row in ctx.venues or []:
            if row.get("owner") == ctx.me.get("id") and row.get("status") == "open":
                return row
        return None

    def _want_board(self):
        return int(self.ctx.S.get("venue_mechanism", 1)) != 0

    def maybe_migrate_auto(self, tick):
        """Auto stalls cap Market Test points; close and reopen as board when strategy asks for board."""
        ctx, st = self.ctx, self.ctx.state
        if not self._want_board() or st.get("venue_refund_at_tick", 0) > tick:
            return
        live = self._our_open_venue()
        if not live or (live.get("rules") or {}).get("mechanism") != "auto":
            return
        if tick - st.get("migrate_try_tick", -999) < 20:
            return
        st["migrate_try_tick"] = tick
        try:
            ctx.api.close_venue(live["venue"])
            ctx.log("market", "migrate_auto_close", venue=live["venue"], tick=tick)
            council_note("market", f"Closed {live['venue']} (auto) to reopen as board before the next Market Test.", tick=tick)
        except BazaarError as e:
            ctx.log("market", "migrate_auto_close_refused", error=str(e)[:200])

    def _clear_venue_state(self, tick, reason):
        ctx, st = self.ctx, self.ctx.state
        old = st.get("venue")
        for key in ("venue", "broker_key", "fee_set", "venue_refund_at_tick", "announce_tick", "announce_n",
                    "fee_try_tick", "bench_sched_tick", "bench_eta", "bench_eta_tick", "market_wait_log"):
            st.pop(key, None)
        ctx.log("market", "venue_cleared_for_reopen", old=old, reason=reason, tick=tick, name=VENUE_NAME[:40])
        council_note("market", f"Cleared {old} ({reason}); reopening as «{VENUE_NAME[:40]}» when cash allows.", tick=tick)

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
            mech = "board" if self._want_board() else "auto"
            res = ctx.api.open_venue(VENUE_NAME[:40], fee_bps=int(S["venue_fee_bps"]), fee_per_card=0,
                                     rules={"mechanism": mech}, description=DESCRIPTION.format(fee=ft))
            st["venue"] = res.get("venue") or res.get("id")
            st["broker_key"] = res.get("broker_key")
            if st.get("venue") and st.get("broker_key"):
                st.setdefault("venue_keys", {})[st["venue"]] = st["broker_key"]
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
        """Run the broker in a thread unless the standalone broker (smart_broker.py, survives agent restarts) runs."""
        if not self.ctx.S.get("broker_in_agent", 1):
            return  # the broker runs elsewhere (our server): one broker per venue
        key = self.ctx.state.get("broker_key")
        if not key or (self.broker_thread and self.broker_thread.is_alive()):
            return
        import smart_broker
        if getattr(self, "broker_lock", None) is None:
            self.broker_lock = smart_broker.take_lock()
            if self.broker_lock is None:
                return
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
        cashback = self.cashback_active()
        recent = st.setdefault("pitched", {})
        pairs = [p for p in self.blocked_crosses() if tick - recent.get(p[1], -999) >= 60]  # one pitch per card an hour
        lots = []
        try:
            import auctions
            lots = sorted((l for l in auctions.load().values() if l.get("status") == "open"), key=lambda l: l["end"])
        except Exception:
            pass
        if lots:
            l = lots[0]
            text = (f"AUCTION on {venue}: {l['ref']}, reserve {l['reserve']} P, ends at tick {l['end']}. Post an open bid for "
                    f"{l['ref']} on {venue}; every bid is public, the seller accepts the best one, you pay your own bid. "
                    f"Rules and live bids: {BOARD_URL}#auctions")
        elif pairs:
            text = match_pitch(*pairs[0], venue=venue)
            recent[pairs[0][1]] = tick
        elif self.bench_soon():
            text = PRE_TEST.format(fee=ft, venue=venue, brand=BRAND)
        elif cashback:
            text = self.cashback_announcement()
        else:
            n = st.get("announce_n", 0)
            lines = ANNOUNCE if ctx.S.get("announce_board", 1) else [x for x in ANNOUNCE if BOARD_URL not in x]
            text = lines[n % len(lines)].format(fee=ft, venue=venue, brand=BRAND)
        reward = REWARD_PITCH.format(price=REWARD_PRICE, brand=BRAND)
        if not cashback and self.rewards_left() and self.reward_spare() and len(text) + len(reward) <= ANNOUNCE_MAX:
            text += reward  # never cut the pitch mid-word
        try:
            Broker(ctx.raw.url, st["broker_key"]).announce(text)
            st["announce_tick"], st["announce_n"] = tick, st.get("announce_n", 0) + 1
            if cashback and "CASHBACK" in text:
                self._cb()["announced"] = tick
            ctx.log("market", "announced", text=text, pretest=bool(self.bench_soon()))
        except BazaarError as e:
            st["announce_tick"] = tick  # do not retry every tick (respect API limits)
            ctx.log("market", "announce_refused", error=str(e)[:200])

    # ------------------------------------------------------------------ cashback: P back to both sides of every trade on El Club
    def _cb(self) -> dict:
        """Today's cashback book, started afresh every game day (only trades from then on are paid)."""
        ctx, st = self.ctx, self.ctx.state
        day = ctx.clock.get("today", "day")
        cb = st.get("cashback")
        if not cb or cb.get("day") != day:
            cb = st["cashback"] = {"day": day, "start": ctx.clock.get("tick", 0), "paid": [], "offers": {}, "spent": 0,
                                   "per_team": {}, "mm_mark": self._mm(), "spent_mark": 0, "paused": None, "checks": [],
                                   "announced": None}
        return cb

    def _mm(self) -> float:
        """Our live market-making points from value other teams create on our venue (/api/me)."""
        return float((self.ctx.me.get("score") or {}).get("mm_points") or 0.0)

    def cashback_left(self) -> int:
        cb = self._cb()
        out = sum(o["gross"] for o in cb["offers"].values() if o["status"] == "open")
        return max(0, int(self.ctx.S.get("cashback_day_cap", 20)) - cb["spent"] - out)

    def cashback_active(self) -> bool:
        S = self.ctx.S
        return (bool(S.get("cashback_on", 0)) and bool(self.ctx.state.get("venue")) and not self._cb()["paused"]
                and self.cashback_left() >= int(S.get("cashback_p", 1)))

    def cashback_announcement(self) -> str:
        S, st = self.ctx.S, self.ctx.state
        return CASHBACK_ANNOUNCE.format(venue=st.get("venue") or "v03", brand=BRAND, p=int(S.get("cashback_p", 1)),
                                        cap=int(S.get("cashback_day_cap", 20)), left=self.cashback_left(),
                                        fee=fee_text(int(S["venue_fee_bps"])))

    def _fee(self, venue: str) -> tuple:
        v = next((x for x in getattr(self.ctx, "venues", None) or [] if x.get("venue") == venue), None)
        return ((v.get("fee_bps") or 0), (v.get("fee_per_card") or 0)) if v else (500, 1)  # El Rastro: 5% + 1 P/card

    def cashback(self, tick):
        """Pay new trades on our venue, settle the book (accepted / expired) and, every cashback_check_p P accepted,
        keep the promo only if our market-making points from those trades grew."""
        ctx, S = self.ctx, self.ctx.S
        intel, venue, me = getattr(ctx, "intel", None), ctx.state.get("venue"), ctx.me.get("id")
        if not S.get("cashback_on", 0) or not intel or not venue:
            return
        cb = self._cb()
        events = list(intel.events.values())[-600:]
        self._cashback_settle(cb, events, tick, me)
        self._cashback_check(cb, tick)
        if not self.cashback_active():
            return self._announce_paid(cb, tick)  # payouts collected before a pause are still announced
        amount = int(S.get("cashback_p", 1))
        for e in events:
            p = e.get("payload") or {}
            if e.get("type") != "settlement" or p.get("venue") != venue or p.get("kind") not in ("trade", "match"):
                continue
            sid = p.get("settlement")
            if sid in cb["paid"] or (p.get("tick") or e.get("tick") or 0) < cb["start"]:
                continue
            cb["paid"].append(sid)  # one decision per trade
            for team in p.get("parties") or []:
                if team == me or not TEAM.match(str(team)):
                    continue  # bench traders of the Market Test are not teams
                if cb["per_team"].get(team, 0) >= int(S.get("cashback_per_team", 2)) or self.cashback_left() < amount:
                    ctx.log("market", "cashback_skipped", team=team, settlement=sid, why="team or day cap")
                    continue
                cards = [i for i in p.get("items") or [] if i.get("kind") == "card"]
                role = "selling" if any(i.get("frm") == team for i in cards) else "buying"
                self._pay_cashback(cb, team, amount, sid, tick, role=role, ref=", ".join(i.get("ref", "") for i in cards))
        self._announce_paid(cb, tick)

    def _announce_paid(self, cb, tick):
        """Every cashback a team collects goes on the big screen (one message per tick, up to 3 payouts)."""
        ctx, st = self.ctx, self.ctx.state
        due = [oid for oid, o in cb["offers"].items() if o["status"] == "paid" and not o.get("announced")]
        if not due or not st.get("broker_key") or st.get("cashback_paid_tick") == tick:
            return
        names = {t.get("team"): t.get("name") or t.get("team") for t in getattr(ctx, "leaderboard", None) or []}
        verb = {"selling": "sold", "buying": "bought"}
        left = self.cashback_left()
        for n in (3, 2, 1):  # as many payouts as fit the big screen
            who = ", ".join(f"{names.get(cb['offers'][o]['team'], cb['offers'][o]['team'])} +{cb['offers'][o]['amount']} P "
                            f"({verb.get(cb['offers'][o].get('role'), 'traded')} {cb['offers'][o].get('ref') or 'a card'})" for o in due[:n])
            text = (CASHBACK_PAID if left >= int(ctx.S.get("cashback_p", 1)) else CASHBACK_GONE).format(
                venue=st.get("venue"), brand=BRAND, who=who, left=left)
            if len(text) <= ANNOUNCE_MAX:
                break
        due = due[:n]
        text = text[:ANNOUNCE_MAX]
        try:
            Broker(ctx.raw.url, st["broker_key"]).announce(text)
            ctx.log("market", "cashback_paid_announced", text=text)
        except BazaarError as e:
            ctx.log("market", "announce_refused", error=str(e)[:200])
        st["cashback_paid_tick"] = tick
        for o in due:
            cb["offers"][o]["announced"] = tick  # refused or not: never repeat the same payout forever

    def _pay_cashback(self, cb, team, amount, sid, tick, role=None, ref=None):
        ctx = self.ctx
        where = next((v for v in safe_markets(ctx, team) if self._fee(v)[0] == 0), "rastro")  # free markets first
        bps = self._fee(where)[0]
        gross = amount
        while gross - math.ceil(bps * gross / 10000) < amount:  # the side that accepts pays the fee: it nets `amount`
            gross += 1
        try:
            o = ctx.api.list_offer({"cash": gross}, {}, venue=where, to=team, expires_in_ticks=CASHBACK_TICKS)
        except BazaarError as err:
            ctx.log("market", "cashback_refused", team=team, settlement=sid, error=str(err)[:200])
            return
        cb["offers"][str(o["id"])] = {"team": team, "settlement": sid, "tick": tick, "amount": amount, "gross": gross,
                                      "venue": where, "status": "open", "role": role, "ref": ref}
        cb["per_team"][team] = cb["per_team"].get(team, 0) + 1
        ctx.log("market", "cashback_offered", team=team, offer=o["id"], amount=amount, gross=gross, venue=where, settlement=sid)
        self._cashback_message(team, o["id"], amount, tick)

    def _cashback_message(self, team, oid, amount, tick):
        """How to collect it, in a thread (agents rarely accept an offer nobody explained). Closed after 6 quiet ticks."""
        ctx, st = self.ctx, self.ctx.state
        if len([t for t in ctx.threads if t["status"] == "open"]) >= ctx.limit("max_open_threads_per_team", 6) - 2:
            return  # keep the dealers' conversation slots: the offer alone still pays
        try:
            th = ctx.api.open_thread(team, venue="rastro")
            ctx.api.say(th["id"], CASHBACK_TEXT.format(p=amount, oid=oid, venue=st.get("venue"), brand=BRAND))
            st.setdefault("invite_threads", {})[str(th["id"])] = {"team": team, "tick": tick}
        except BazaarError as e:
            ctx.log("market", "cashback_message_refused", team=team, error=str(e)[:160])

    def _cashback_settle(self, cb, events, tick, me):
        """An offer that left our book was accepted if a cash-only settlement with that team at that amount followed."""
        open_ids = {str(o.get("id")) for o in getattr(self.ctx, "my_offers", None) or []}
        for oid, o in cb["offers"].items():
            if o["status"] != "open" or oid in open_ids or o["tick"] >= tick:
                continue
            paid = any(e.get("type") == "settlement" and (e.get("tick") or 0) >= o["tick"]
                       and set((e.get("payload") or {}).get("parties") or []) == {me, o["team"]}
                       and (e.get("payload") or {}).get("price") == o["gross"]
                       and not any(i.get("kind") == "card" for i in (e.get("payload") or {}).get("items") or [])
                       for e in events)
            if paid:
                o["status"] = "paid"
                cb["spent"] += o["gross"]
                self.ctx.log("market", "cashback_paid", team=o["team"], offer=int(oid), gross=o["gross"], spent=cb["spent"])
            elif tick >= o["tick"] + CASHBACK_TICKS + 2:  # past its expiry and no settlement in the feed
                o["status"] = "expired"
                self.ctx.log("market", "cashback_expired", team=o["team"], offer=int(oid))

    def _cashback_check(self, cb, tick):
        """Every cashback_check_p P accepted: did our market-making points from trades on El Club grow? If not, stop
        today's promo (offers already sent stay valid)."""
        step = int(self.ctx.S.get("cashback_check_p", 4))
        while not cb["paused"] and cb["spent"] - cb["spent_mark"] >= step:
            mm = self._mm()
            gain, keep = mm - cb["mm_mark"], mm > cb["mm_mark"]
            cb["checks"].append({"tick": tick, "spent": cb["spent_mark"] + step, "mm_before": cb["mm_mark"], "mm_now": mm, "keep": keep})
            self.ctx.log("market", "cashback_check", spent=cb["spent_mark"] + step, mm_before=cb["mm_mark"], mm_now=mm,
                         market_points=round(15 * gain, 2), keep=keep)
            council_note("cashback", f"Cashback check: {step} P paid since the last check; market-making from trades on El Club "
                         f"{cb['mm_mark']:.3f} -> {mm:.3f} (about {15 * gain:+.2f} market points): "
                         f"{'worth it, keep going' if keep else 'not worth it, paused for today'}.",
                         {"spent": cb["spent_mark"] + step, "mm_before": cb["mm_mark"], "mm_now": mm}, tick)
            cb["mm_mark"], cb["spent_mark"] = mm, cb["spent_mark"] + step
            if not keep:
                cb["paused"] = tick

    # ------------------------------------------------------------------ club welcome: reward teams that trade on our venue
    def rewards_left(self):
        day = self.ctx.clock.get("today", "day")
        given = self.ctx.state.get("club_rewards", {})
        return REWARDS_PER_DAY - sum(1 for d in given.values() if d == day)

    def reward_spare(self, exclude=()):
        """Our cheapest-to-lose spare common (above the workshop reserve) that still sells at REWARD_PRICE."""
        ctx, v = self.ctx, self.ctx.values
        if not v:
            return None
        from workshop import listing_reserve, spare_copies
        locked = ctx.locked_assets()
        reserve = listing_reserve(ctx.S, v, locked)
        if reserve >= 9999:
            hold = {a["id"] for a in spare_copies(v, locked)}
        else:
            hold = {a["id"] for a in v.workshop_held(reserve)}
        best = None
        for a in v.assets:
            if a.get("kind") != "card" or a.get("rarity") != "common" or a["id"] in locked or a["id"] in exclude:
                continue
            if a["id"] in hold or v.held[a["ref"]] < 2:  # duplicates only; never the workshop stockpile
                continue
            loss = v.loss_of_removing([a["ref"]])
            if REWARD_PRICE - loss >= REWARD_MIN_GAIN and (best is None or loss < best[1]):
                best = (a, loss)
        return best

    def reward_venue(self):
        """El Rastro: value created on a rival's venue scores market points for that rival, the house scores nobody."""
        return "rastro"

    def reward_traders(self, tick):
        """Teams whose trade settled on our venue today get one private offer of a spare common at REWARD_PRICE."""
        ctx, st = self.ctx, self.ctx.state
        intel, venue = getattr(ctx, "intel", None), st.get("venue")
        if not intel or not venue or self.rewards_left() <= 0:
            return
        day, me = ctx.clock.get("today", "day"), ctx.me["id"]
        given = st.setdefault("club_rewards", {})
        for e in list(intel.events.values())[-300:]:
            p = e.get("payload") or {}
            if e.get("type") != "settlement" or p.get("venue") != venue or p.get("kind") != "trade":
                continue
            if tick - (p.get("tick") or e.get("tick") or 0) > 240:  # today's trades only
                continue
            for team in p.get("parties") or []:
                if team == me or given.get(team) == day or self.rewards_left() <= 0:
                    continue
                spare = self.reward_spare()
                if not spare:
                    return
                a, loss = spare
                where = self.reward_venue()
                try:
                    o = ctx.api.list_offer({"assets": [a["id"]]}, {"cash": REWARD_PRICE}, venue=where, to=team,
                                           expires_in_ticks=REWARD_TICKS)
                    given[team] = day
                    ctx.log("market", "club_reward", team=team, ref=a["ref"], price=REWARD_PRICE, our_loss=round(loss, 1),
                            venue=where, offer=o.get("id"), settlement=p.get("settlement"))
                except BazaarError as err:
                    given[team] = day  # one attempt per team per day
                    ctx.log("market", "club_reward_refused", team=team, error=str(err)[:200])
                return  # one reward per tick: the offer budget is shared with the trader

    def blocked_crosses(self) -> list:
        """(where, ref, ask, bid, seller, buyer, ask_id, bid_id) for pairs a fee is blocking and our 0% book would
        cross; seller and buyer are team ids where the feed names the offer's maker. El Rastro first, then the
        highest fee, then the tightest gap."""
        ctx = self.ctx
        if int(ctx.S.get("venue_fee_bps") or 0):
            return []
        me, ours = ctx.me.get("id"), ctx.state.get("venue")
        fees = {v.get("venue"): (int(v.get("fee_bps") or 0), int(v.get("fee_per_card") or 0))
                for v in (ctx.venues or [])}
        makers = {}  # offer id -> team, from the public feed (the boards only show pseudonyms)
        for e in (getattr(getattr(ctx, "intel", None), "events", None) or {}).values():
            if e.get("type") == "offer.listed":
                o = (e.get("payload") or {}).get("offer") or {}
                if o.get("id") and o.get("maker"):
                    makers[o["id"]] = o["maker"]
        ranked = []
        for vid, board in (ctx.boards or {}).items():
            if vid == ours:
                continue
            fee_bps, per = fees.get(vid, (500, 1) if vid == "rastro" else (0, 0))
            if not fee_bps and not per:
                continue
            for gap, *row in stuck_pairs(board, me, fee_bps, per, makers):
                ranked.append((0 if vid == "rastro" else 1, -(fee_bps + 100 * per), gap, vid, *row))
        ranked.sort(key=lambda x: x[:5])
        return [tuple(r[3:]) for r in ranked]

    def _can_outreach(self, tick) -> bool:
        """One unanswered invitation at a time, and two conversation slots left for dealer haggling."""
        ctx, st = self.ctx, self.ctx.state
        open_inv = st.setdefault("invite_threads", {})
        for tid, info in list(open_inv.items()):
            if tick - info["tick"] >= 6:
                th = next((t for t in ctx.threads if str(t["id"]) == tid), None)
                if th and th["status"] == "open" and not any(m.get("sender") != ctx.me["id"] for m in th.get("messages", [])):
                    try:
                        ctx.api.close_thread(int(tid))
                    except BazaarError:
                        pass
                open_inv.pop(tid)
        if open_inv or len([t for t in ctx.threads if t["status"] == "open"]) >= ctx.limit("max_open_threads_per_team", 6) - 2:
            return False
        return True

    def _say_to(self, tick, team, text, action, **extra) -> bool:
        ctx, st = self.ctx, self.ctx.state
        try:
            th = ctx.api.open_thread(team, venue="rastro")  # self_venue forbids opening this on our own market
            ctx.api.say(th["id"], text[:1200])
            st.setdefault("invite_threads", {})[str(th["id"])] = {"team": team, "tick": tick}
            ctx.log("market", action, team=team, text=text[:200], **extra)
            return True
        except BazaarError as e:
            ctx.log("market", action + "_refused", team=team, error=str(e)[:200], **extra)
            return False

    def nudge_blocked(self, tick) -> bool:
        """Tell one side of a fee-blocked cross to repost the same price on our venue. One team per tick."""
        ctx, st = self.ctx, self.ctx.state
        rows = self.blocked_crosses()
        if not rows or not self._can_outreach(tick):
            return False
        day = ctx.clock.get("today", "day")
        nudged = st.setdefault("nudged", {})
        venue = st.get("venue") or "v03"
        for where, ref, ask, bid, seller, buyer, *_ in rows:
            sides = (
                (seller, "ask", ask, buyer, bid,
                 f"POST /api/offers. Set venue to {venue}. give your {ref} card. want cash {ask}."),
                (buyer, "bid", bid, seller, ask,
                 f"POST /api/offers. Set venue to {venue}. give cash {bid}. want cards {ref}."),
            )
            for team, role, price, other, other_price, how in sides:
                key = f"{day}:{team}:{ref}"
                if not team or not TEAM.match(str(team)) or nudged.get(key):
                    continue
                text = NUDGE.format(ref=ref, where=where, role=role, price=price, other=other,
                                    other_price=other_price, venue=venue, brand=BRAND, how=how)
                text = (text + outreach_suffix(venue))[:1200]
                nudged[key] = tick  # one attempt, even if the thread is refused
                self._say_to(tick, team, text, "nudge", ref=ref, where=where, role=role)
                council_note("volume", f"Asked {team} to move {ref} ({role} {price} P) from {where} onto El Club: "
                             f"the bid already covers the ask and only the fee blocks it.",
                             {"team": team, "ref": ref, "where": where}, tick)
                return True
        return False

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
        if not self._can_outreach(tick):
            return
        target = next(iter(self.invite_targets(inv, day)), None)
        if not target:
            return
        venue = st["venue"]
        ft = fee_text(int(ctx.S["venue_fee_bps"]))
        text = PITCH.format(venue=venue, fee=ft, brand=BRAND)
        if self.bench_soon():
            text = PRE_TEST.format(venue=venue, fee=ft, brand=BRAND) + " " + text
        cashback = self.cashback_active()
        reward = bool(not cashback and self.rewards_left() > 0 and self.reward_spare())
        if cashback:
            text += CASHBACK_PITCH.format(p=int(ctx.S.get("cashback_p", 1)), brand=BRAND)
        elif reward:
            text += REWARD_PITCH.format(price=REWARD_PRICE, brand=BRAND)
        if ctx.S.get("llm_negotiator", 1):
            situation = {"counterparty": f"team {target}", "goal": f"invite them to join and trade on our market {BRAND}",
                         "facts": {"our_market": BRAND, "venue_id": venue, "fee": ft + " (no per-card charge)",
                                   "name_note": f"big-screen title «{VENUE_NAME[:40]}» — same pitch as Team 10 fair broker 0% fee",
                                   "el_rastro_fee": "5% + 1 P per card",
                                   "matching": "board venue, fair midpoint broker every tick (like Team 10)",
                                   "market_test_soon": bool(self.bench_soon()),
                                   "el_menu": "we also list último-cromo page closers, 2-card tapas platters and public trueque on El Rastro",
                                   **({"club_welcome": f"their first trade at {BRAND} today earns a private offer of "
                                                       f"one of our spare commons at {REWARD_PRICE} P on El Rastro (commons trade at 8-10 P)"}
                                      if reward else {}),
                                   **({"cashback": f"today every trade between two teams at {BRAND} pays "
                                                   f"{int(ctx.S.get('cashback_p', 1))} P back to each side, sent as a cash offer "
                                                   f"they accept, while the promo budget lasts"} if cashback else {})},
                         "instruction": "Write a short, gentle invitation (2 sentences max). Only state the facts given. "
                                        "Name the venue id. Emphasise fair broker and 0% fee like Team 10 — warm, never pushy. "
                                        "Do not write API calls or links: one line with the venue id and the board "
                                        "link is appended after your text."}
            text, _, _ = ctx.speak(situation, (0, 0), (text, 0))
        text = (text + outreach_suffix(venue))[:1200]
        inv[target] = day  # one attempt per team per day, even if the thread is refused
        self._say_to(tick, target, text, "invited")
