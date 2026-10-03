"""Dealer negotiation: the ladder scores the share of each dealer's price range we capture (our best three deals per
level), and three negotiated deals unlock the next dealer early.

Strategy, from the rules and the kickoff deck:
- Dealers move only when we move, small steps earn small steps, the same price twice earns nothing. So we use a
  Boulware schedule: open low, concede slowly at first and faster near our cap, a new price every message.
- Abuela likes kindness: every message is polite, and the words vary so no message repeats. The words bargain
  kindly with small white lies about ourselves (a tight purse, a gift for a granddaughter, a cheaper stall nearby).
- A final offer ("final": true) is take-it-or-walk: we take it when it is inside our cap.
- If the dealer's standing ask is already at or below what we would offer next, we take it instead of overpaying.
- After every conversation we remember the outcome per dealer and item, and open the next one lower if we did well.
- Never pay more than the item is worth to us: open buy_open_margin under our value (lower if the list-price rule
  opens lower) and concede Boulware-style up to (1 - buy_value_margin) of it, reaching that cap at the round this
  dealer usually names its final: the mean over every team's conversations with it (intel.rounds), our last offer
  before her usual final being the cap. A pack's worth is an expectation (we cannot know what it holds), hence the margin.
"""
from __future__ import annotations

import json
import math
import os
import random

from bazaar_sdk import BazaarError

KIND_BUY = [
    "¡Hola, {name}! Qué cartas tan bonitas. ¿Le parecería bien {p} primas? Muchas gracias.",
    "Thank you for your patience, {name}. Could we say {p} primas? It would make my afternoon.",
    "{name}, you are very kind. My little budget stretches to {p} primas, would that be all right?",
    "Me encanta su puesto, {name}. ¿Y si lo dejamos en {p}? Gracias de corazón.",
    "I really appreciate it, {name}. {p} primas, and I will tell all my friends about your stall.",
    "Usted es un encanto, {name}. ¿Podría ser por {p} primas?",
    "Thank you, {name}! I can come up a little: {p} primas?",
    "Gracias por todo, {name}. Subo un poquito: {p} primas, ¿vale?",
    "Ay, {name}, es para mi nieta, que colecciona estas cartas. ¿Me la dejaría en {p} primas?",
    "{name}, I counted my purse twice: {p} primas is what I have today. Would you be so kind?",
    "En el Rastro me la dejaban más barata, pero prefiero comprársela a usted, {name}. ¿{p} primas?",
    "Es mi último día en Madrid, {name}. ¿{p} primas y me llevo un recuerdo precioso de su puesto?",
    "My grandmother had a stall just like yours, {name}. Could you do {p} primas for me?",
]
KIND_SELL = [
    "¡Hola, {name}! Tengo una carta preciosa para usted. ¿{p} primas le parece bien?",
    "Thank you, {name}. This one is in lovely condition. Would {p} primas be fair?",
    "{name}, for you I can come down a little: {p} primas. Muchas gracias.",
    "Gracias, {name}. ¿Lo dejamos en {p}? Me haría muy feliz.",
    "You are very kind, {name}. {p} primas and it is yours.",
]


def Learner_reward(ctx, plan, price, opening):
    if plan.get("arm") is not None and plan.get("cls"):
        from learner import Learner
        Learner.reward(ctx.state, plan["cls"], plan["arm"], price, opening)
        ctx.log("learn", "reward", cls=plan["cls"], arm=plan["arm"], price=price, opening=opening,
                capture=round(1 - price / opening, 3) if price and opening else 0)


# Abuela gives a small gift (a random common) to teams that are kind to her, about once per team every two game
# hours (Saturday feed: t06 at ticks 447 and 687, t07 at 506 and 759). A visit is one warm message, no price, no buy.
VISIT_TEXTS = [
    "¡Buenas tardes, Abuela Carmen! I only came by to say thank you. Your stall is the warmest corner of El Rastro, "
    "and after a long day of running around it is lovely to see a friendly face. How is your day going?",
    "Abuela Carmen, hello again! No hurry and nothing to sell today, I just wanted to see how you are. "
    "Your stories make El Rastro feel like home. Have you had a moment to rest?",
    "¡Hola, Abuela! Passing by to wish you a lovely afternoon. Thank you for always being so patient with us; "
    "it means a lot to a team far from home. Is there anything nice happening at your stall today?",
]
VISIT_HOURS = 2.0       # game hours between two visits
VISIT_CLOSE_TICKS = 3   # leave the visit thread open this long for her answer (and the gift), then close it
PERSONA_QUOTA = 10      # the game: at most 10 conversations per hour with one dealer
FEVER = (9.15, 11.15)   # Saturday schedule: "Salamanca fever: Doña Pilar pays 25 % over book for Salamanca"
FEED_STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "feed_events.jsonl")


def short_name(dealer: dict) -> str:
    """How we address a dealer: 'Abuela', but 'El Chato', 'Doña Pilar', 'Los Pícaros' (never a bare article)."""
    name = dealer.get("name") or dealer.get("id", "")
    first = name.split()[0] if name.split() else name
    return name if first in ("El", "La", "Los", "Las", "Doña", "Don") else first


def S_use_intel(ctx) -> bool:
    return bool(ctx.S.get("use_intel", 1)) and getattr(ctx, "intel", None) is not None


def boulware(lo: float, hi: float, k: int, rounds: int = 12, e: float = 2.2) -> float:  # knobs: haggle_rounds, haggle_curve
    """Concession schedule: move little at first, more as patience runs out."""
    x = min(1.0, k / rounds)
    return lo + (hi - lo) * (x ** e)


class Haggler:
    def __init__(self, ctx):
        self.ctx = ctx  # shared Context: api, values, state, log, accepts budget

    # ------------------------------------------------------------------ planning
    def _hour(self) -> int:
        return int(self.ctx.clock.get("t_hours", 0))

    def _value_band(self, worth: float):
        """(first offer, most we pay) from what the item is worth to us."""
        S = self.ctx.S
        return (max(1, math.floor(worth * (1 - S.get("buy_open_margin", 0.4)))),
                max(0, math.floor(worth * (1 - S.get("buy_value_margin", 0.1)))))

    def _rounds(self, dealer_id: str) -> int:
        """Boulware rounds for this dealer: she names her final after ~N offers (mean over every team's conversations
        with her), so our offer number N, index N - 1, is the cap. Fallbacks: every dealer together (learner), the knob."""
        r = self.ctx.intel.rounds(dealer_id) if S_use_intel(self.ctx) else {}
        if (r.get("n") or 0) >= 3:
            return max(2, round(r["mean"]) - 1)
        L = getattr(self.ctx, "learner", None)
        rtf = L.model.get("rounds_to_final") if L and getattr(L, "model", None) else None
        return max(2, round(rtf) - 1) if rtf else int(self.ctx.S["haggle_rounds"])

    def _counts(self, dealer: str) -> dict:
        h = self.ctx.state.setdefault("dealer_hours", {}).setdefault(dealer, {})
        return h.setdefault(str(self._hour()), {"packs": 0, "deals": 0, "opened": 0})

    def choose_topic(self, dealer: dict):
        """What to negotiate next with this dealer, or None. Returns (topic, plan)."""
        ctx, menu = self.ctx, dealer.get("menu") or {}
        counts = self._counts(dealer["id"])
        blocked = ctx.state.setdefault("dealer_blocked", {})
        if blocked.get(dealer["id"], -1) >= ctx.clock.get("tick", 0):
            return None
        if counts["deals"] >= menu.get("deals_per_team_per_hour", 8):
            return None
        unsupported = set(ctx.state.setdefault("unsupported_topics", []))
        cash = ctx.me["cash"] - ctx.reserve()
        stats = ctx.state.setdefault("dealer_stats", {})
        S = ctx.S
        intel = ctx.intel if S_use_intel(ctx) else None
        first_time = not any(t.get("with") == dealer["id"] and t["status"] == "deal" for t in ctx.threads)
        if intel and first_time and intel.has_beginner_price(dealer["id"]):
            # every team's first deal with a dealer goes at a fixed welcome price (~70% of list): spend it on the
            # single item worth most to us
            best = None
            for s in menu.get("sells", []):
                if s.get("rarity"):
                    for ref, gain in ctx.values.wishlist(limit=40):
                        if ctx.values.cards[ref]["rarity"] == s["rarity"] and (best is None or gain > best[2]):
                            best = (s, ref, gain)
            # the welcome price is ~70% of list: a bargain worth more than the per-item cap, but never past today's budget
            if best and best[0].get("list_price", 99) * 0.75 <= min(cash, ctx.budget_left(), self._value_band(best[2])[1]):
                s, ref, gain = best
                return {"buy": {"card": ref}}, {"side": "buy", "key": f"{dealer['id']}:buy:{s['rarity']}", "lo": 1,
                                                "hi": min(math.floor(s["list_price"] * S["haggle_cap"]), ctx.budget_left(),
                                                          math.floor(gain - S["trade_min_gain"]), self._value_band(gain)[1]),
                                                "list": s["list_price"], "ref": ref, "beginner": True, "worth": round(gain, 1)}

        S = ctx.S
        buys_today = ctx.state.setdefault("dealer_buys", {}).get(f"{ctx.day_key()}:{dealer['id']}", 0)
        can_buy = buys_today < S.get("deals_per_dealer_day", 5)
        item_cap = min(S.get("max_dealer_buy", 40), ctx.budget_left())
        from workshop import accumulating, restock_packs
        buy_packs = restock_packs(S, ctx.values, ctx.locked_assets())
        accum, acc_rar, acc_have = (accumulating(ctx.values, ctx.locked_assets(), int(S.get("workshop_trio_target", 3)))
                                    if int(S.get("workshop_accumulate", 1)) and int(S.get("enable_workshop", 1))
                                    else (False, "", 0))
        for s in (menu.get("sells", []) if can_buy else []):  # 1) packs: the cleanest price range to capture
            if buy_packs and "pack" in s and counts["packs"] < s.get("per_team_per_hour", 3):
                key = f"{dealer['id']}:buy:{s['pack']}"
                hi = min(math.floor((s.get("list_price") or s.get("opening_ask", 30)) * S["haggle_cap"]), cash, item_cap)
                pack = next((p for p in (getattr(ctx, "catalog", None) or {}).get("packs", []) if p["id"] == s["pack"]), None)
                worth = ctx.values.pack_ev(pack) if pack else 0.0  # expected value at our values: the contents are luck
                v_lo, v_hi = self._value_band(worth)
                if pack:  # not worth it for us above its value: don't buy, move on
                    hi = min(hi, math.floor(worth - S["trade_min_gain"]), v_hi)
                if (s.get("list_price") or 0) * 0.6 > item_cap:
                    continue  # too expensive for the ladder: the same capture is available on cheaper items
                if hi < 5 or key in unsupported:
                    continue
                best_any = (intel.advice(dealer["id"], f"buy:pack:{s['pack']}") or {}).get("best") if intel else None
                if best_any is not None and hi < best_any:
                    continue  # no team has ever got one this cheap: don't spend the dealer's patience on it
                lo = min(self._opening(stats.get(key), s, side="buy"), v_lo)
                return {"buy": {"pack": s["pack"]}}, {"side": "buy", "key": key, "lo": min(lo, hi), "hi": hi, "pack": s["pack"],
                                                       "list": s.get("list_price"), "opening": s.get("opening_ask"),
                                                       "worth": round(worth, 1), "rounds": self._rounds(dealer["id"])}
        buys = {b.get("rarity") for b in menu.get("buys", [])}
        hold_spares = accum or not S["haggle_sell_spares"]
        spare_pool = [] if hold_spares else ctx.values.spares(reserve=int(S.get("workshop_spares", 0)))
        for a in spare_pool:  # 2) sell a spare above the workshop reserve, never below its value to us
            if a.get("rarity") not in buys or a["id"] in ctx.locked_assets():
                continue
            key = f"{dealer['id']}:sell:{a['rarity']}"
            if key in unsupported:
                continue
            book = ctx.values.book(a["ref"])
            floor = max(math.ceil(ctx.values.loss_of_removing([a["ref"]]) + 1), math.ceil(book * 0.35))
            if floor >= book:
                continue
            hi_ask = self._opening(stats.get(key), {"list_price": book}, side="sell")
            return {"sell": {"assets": [a["id"]]}}, {"side": "sell", "key": key, "lo": floor, "hi": hi_ask,
                                                   "list": book, "asset": a["id"], "ref": a["ref"]}
        ladder = self._ladder_card(dealer, buys) if S.get("ladder_sell", 1) else None
        if ladder:  # 2b) higher-level dealers we cannot buy from: three sales fill the level's best-three
            a, book = ladder
            key = f"{dealer['id']}:sell:{a['rarity']}"
            if key not in unsupported:
                hi_ask = self._opening(stats.get(key), {"list_price": book}, side="sell")
                # only a sale above our third-best price with this dealer improves the ladder's best three
                done = sorted((stats.get(key) or {}).get("deals") or [], reverse=True)
                # a level with fewer than three deals scores 0 for each missing one: any price above the dealer's
                # opening (negotiate() keeps lo >= opening + 2) fills a slot; after that, beat the third-best
                floor = (done[2] + 1) if len(done) >= 3 else 1
                if floor >= hi_ask:
                    return None
                return {"sell": {"assets": [a["id"]]}}, {"side": "sell", "key": key, "lo": floor,
                                                       "hi": hi_ask, "list": book, "asset": a["id"], "ref": a["ref"],
                                                       "ladder": True}
        # 3) one card: the copy worth most to us that we can actually close.
        # Saturday: 8 closes out of 81 uncommon threads. Almost all of those were El Retiro / La Latina,
        # worth 12–17 P to us, while every team closes uncommons at 20–26. We walked, the hourly quota
        # was gone, and the ladder (best three per level) stayed near zero. A missing deal scores 0,
        # so we only open when our cap reaches the cheapest price anyone has already paid.
        best_card = None
        if can_buy and S["haggle_buy_cards"] and cash > 15 and item_cap >= 5:
            for s in menu.get("sells", []):
                if not s.get("rarity"):
                    continue
                key = f"{dealer['id']}:buy:{s['rarity']}"
                if key in unsupported:
                    continue
                adv = (intel.advice(dealer["id"], f"buy:card:{s['rarity']}") or {}) if intel else {}
                market_best = adv.get("best")
                for ref, gain in ctx.values.wishlist(limit=40):
                    if ctx.values.cards[ref]["rarity"] != s["rarity"]:
                        continue
                    v_lo, v_hi = self._value_band(gain)
                    # Pay up to what it is worth. trade_min_gain is for team trades; a dealer buy scores
                    # on the ladder, and the best Chato rare (77) is exactly a Lavapiés rare's value.
                    hi = min(math.floor(s.get("list_price", 10) * S["haggle_cap"]), cash, math.floor(gain), v_hi, item_cap)
                    if hi < 3:
                        continue
                    if market_best is not None and hi < market_best:
                        continue
                    lo = min(self._opening(stats.get(key), s, side="buy"), v_lo)
                    # Chato mirrors the step and walks on a lowball. Open near the prices that close.
                    closeable = None
                    if market_best is not None:
                        closeable = min(hi - 1, max(1, math.floor(market_best * 0.75)))
                        lo = max(lo, closeable)
                    lo = min(lo, hi)
                    if best_card is None or gain > best_card[0]:
                        plan = {"side": "buy", "key": key, "lo": lo, "hi": hi, "list": s.get("list_price"),
                                "ref": ref, "worth": round(gain, 1), "rounds": self._rounds(dealer["id"])}
                        if closeable:
                            plan["closeable_open"] = closeable
                        best_card = (gain, {"buy": {"card": ref}}, plan)
        if best_card:
            return best_card[1], best_card[2]
        return None

    def _team_acquired(self) -> set:
        """Asset ids we received in trades with teams (public feed). Never sold to a dealer: if team-trade value is
        counted on what we hold at the end, selling them could undo that gain. Refreshed every 30 ticks."""
        st, tick = self.__dict__.setdefault("_ta", {"tick": -99, "ids": set()}), self.ctx.clock.get("tick", 0)
        if tick - st["tick"] >= 30:
            ids, me = set(), self.ctx.me.get("id")
            try:
                with open(FEED_STORE) as f:
                    for line in f:
                        if '"settlement"' not in line:
                            continue
                        p = (json.loads(line).get("payload") or {})
                        if p.get("persona") or not any(str(x).startswith("t") and x != me for x in p.get("parties") or []):
                            continue
                        ids.update(i["id"] for i in p.get("items") or [] if i.get("to") == me and i.get("id"))
                st.update(tick=tick, ids=ids)
            except (OSError, ValueError):
                pass
        return st["ids"]

    def _fever(self, dealer, ref) -> bool:
        """Salamanca fever (Saturday 9.15-11.15 game hours): Pilar pays 25% over book for Salamanca."""
        t = self.ctx.clock.get("t_hours") or 0
        return dealer["id"] == "pilar" and ref.startswith("SAL-") and FEVER[0] <= t < FEVER[1]

    def _ladder_card(self, dealer, buys):
        """For a level-3+ dealer: the uncommon/rare she buys that we would miss least. The ladder keeps our best three
        deals per level, so a better-priced sale still improves it after three. Collections never score and dealer
        sales are not team trades, so a sale costs no points. Never sold: a card from a page one card from complete
        (its completer is a big team-trade gain), and a card we got from a team. In Salamanca fever, SAL first."""
        ctx, v = self.ctx, self.ctx.values
        if (dealer.get("level") or 0) < 3:
            return None
        if sum(1 for t in ctx.threads if t.get("with") == dealer["id"] and t["status"] == "deal") >= ctx.S.get("ladder_max_deals", 6):
            return None
        locked, best, from_teams = ctx.locked_assets(), None, self._team_acquired()
        for a in v.assets:
            if a.get("kind") != "card" or a.get("rarity") not in buys or a.get("rarity") not in ("uncommon", "rare"):
                continue
            card = v.cards.get(a["ref"]) or {}
            if a["id"] in locked or a["id"] in from_teams or not card.get("page"):
                continue
            page = v.page_cards(card["set"])
            if sum(1 for r in page if v.held[r] > 0) == len(page) - 1:
                continue
            key = (not self._fever(dealer, a["ref"]), v.loss_of_removing([a["ref"]]))
            if best is None or key < best[1]:
                best = (a, key)
        if not best:
            return None
        a = best[0]
        return a, v.book(a["ref"]) * (1.25 if self._fever(dealer, a["ref"]) else 1.0)

    def _intel(self, dealer_id, plan):
        """Attach what other teams got for this kind of item: a target to close at, a cap never to exceed."""
        ctx = self.ctx
        if not S_use_intel(ctx) or plan.get("beginner"):
            return plan
        cls = (f"buy:pack:{plan['pack']}" if plan.get("pack") else
               f"{plan['side']}:card:{ctx.values.cards.get(plan.get('ref', ''), {}).get('rarity', '')}")
        L = getattr(ctx, "learner", None)
        if L and ctx.S.get("learn_conversations", 1) and plan["side"] == "buy" and (L.model.get("n") or 0) >= 15:
            ratio, arm = L.opening_for(ctx.state, cls)
            if ratio is not None:
                opening = (ctx.intel.advice(dealer_id, cls) or {}).get("opening") or plan.get("opening") or (plan.get("list") or 10) * 1.15
                plan["lo"] = max(1, min(plan["hi"] - 1, math.floor(ratio * opening)))
                if plan.get("worth"):  # the learned first offer may not open above our value-based opening
                    plan["lo"] = min(plan["lo"], self._value_band(plan["worth"])[0])
                # a lowball that never closed (learned ~38% of her ask) is how Chato and Abuela stopped moving
                if plan.get("closeable_open"):
                    plan["lo"] = min(plan["hi"], max(plan["lo"], plan["closeable_open"]))
                plan.update(arm=arm, cls=cls, learned_first=ratio, final_max_r=L.model.get("final_max_vs_opening"),
                            lessons=L.model.get("lessons"))
        adv = ctx.intel.advice(dealer_id, cls)
        if adv:
            plan["target"] = adv["best"]
            if plan["side"] == "buy":
                plan["hi"] = min(plan["hi"], math.ceil(adv["median"]))
            else:
                plan["lo"] = max(plan["lo"], math.floor(adv["median"] * 0.8))
            plan["intel"] = adv
        return plan

    def _opening(self, st, s, side):
        lst = s.get("list_price") or s.get("opening_ask") or 10
        if side == "buy":
            lo = self.ctx.S["haggle_open"] * lst
            if st and st.get("deals"):
                lo = min(lo, 0.85 * min(st["deals"]))  # she went that low before: start below it
            return max(1, math.floor(lo))
        hi = (2 - self.ctx.S["haggle_open"]) * 1.1 * lst  # mirror of the buy opening, for selling
        if st and st.get("walked") and not st.get("deals"):
            # she walked out on our asks: open 20% lower per walkout, near her last counter, never below 1.15x book
            hi = hi * 0.8 ** st["walked"]
            if st.get("last_counter"):
                hi = min(hi, 2 * st["last_counter"])
            hi = max(hi, 1.15 * lst)
        if st and st.get("deals"):
            hi = max(hi, 1.15 * max(st["deals"]))
        return math.ceil(hi)

    @staticmethod
    def _pace(dealer, st):
        """(rounds, curve) for this dealer. Strict dealers (Pilar, Chato) walk out on small 'gesture' steps:
        concede in even steps over few rounds. Patient Abuela keeps the tested Boulware pace."""
        strict = ((dealer.get("traits") or {}).get("strictness") or 0) >= 0.6 or (st or {}).get("walked", 0) >= 2
        return (4, 1.0) if strict else (None, None)

    # ------------------------------------------------------------------ conversations per hour, and Abuela visits
    def _opens(self, dealer_id: str) -> list:
        """Game hours at which we opened a conversation with this dealer within the last hour."""
        now = self.ctx.clock.get("t_hours") or 0
        opens = self.ctx.state.setdefault("dealer_opens", {}).setdefault(dealer_id, [])
        opens[:] = [h for h in opens if now - h < 1.0]
        return opens

    def _visit_due(self, d) -> bool:
        if d["id"] != "abuela" or not self.ctx.S.get("abuela_visits", 1):
            return False
        now = self.ctx.clock.get("t_hours") or 0
        return now - self.ctx.state.get("abuela_visit_hours", -99) >= VISIT_HOURS and len(self._opens("abuela")) < PERSONA_QUOTA

    def _visit(self, d):
        ctx = self.ctx
        try:
            th = ctx.api.open_thread(d["id"], topic={"buy": {"pack": "sobre_barrio"}})
        except BazaarError as e:
            ctx.log("haggle", "visit_refused", dealer=d["id"], error=str(e)[:120])
            ctx.state["abuela_visit_hours"] = (ctx.clock.get("t_hours") or 0) - VISIT_HOURS + 0.25  # retry in 15 min
            return
        n = ctx.state.get("abuela_visit_n", 0)
        ctx.api.say(th["id"], VISIT_TEXTS[n % len(VISIT_TEXTS)])
        ctx.state.update(abuela_visit_hours=ctx.clock.get("t_hours") or 0, abuela_visit_n=n + 1)
        self._opens(d["id"]).append(ctx.clock.get("t_hours") or 0)
        ctx.state.setdefault("plans", {})[str(th["id"])] = {"visit": True, "dealer": d["id"], "tick": ctx.clock.get("tick", 0),
                                                             "key": "abuela:visit"}
        ctx.log("haggle", "visit", dealer=d["id"], thread=th["id"])

    # ------------------------------------------------------------------ the loop
    def step(self):
        ctx = self.ctx
        dealers = [d for d in ctx.dealers if d.get("status") == "active" and d.get("id") in ctx.me.get("unlocked", [])]
        open_threads = {t["with"]: t for t in ctx.threads if t.get("kind") == "persona" and t["status"] == "open"}
        for d in dealers:
            th = open_threads.get(d["id"])
            plan = ctx.state.get("plans", {}).get(str(th["id"])) if th else None
            if plan and plan.get("visit"):  # a gift visit: no haggling, close it after a few ticks
                if ctx.clock.get("tick", 0) - plan["tick"] >= VISIT_CLOSE_TICKS:
                    try:
                        ctx.api.close_thread(th["id"])
                    except BazaarError:
                        pass
                continue
            if th is None:
                if len([t for t in ctx.threads if t["status"] == "open"]) >= ctx.limit("max_open_threads_per_team", 6):
                    continue
                if self._visit_due(d):
                    self._visit(d)
                    continue
                if d["id"] == "abuela" and len(self._opens(d["id"])) >= ctx.S.get("abuela_haggle_per_hour", 6):
                    continue  # keep the rest of her hourly quota for a visit
                choice = self.choose_topic(d)
                if not choice:
                    continue
                topic, plan = choice
                plan = self._intel(d["id"], plan)
                try:
                    th = ctx.api.open_thread(d["id"], topic=topic)
                except BazaarError as e:
                    ctx.log("haggle", "open_refused", dealer=d["id"], topic=topic, error=str(e))
                    if e.code in ("invalid", "bad_topic", "http_400", "http_422") or e.status in (400, 422):
                        ctx.state["unsupported_topics"] = sorted(set(ctx.state.get("unsupported_topics", [])) | {plan["key"]})
                    elif e.code in ("persona_quota", "cooloff", "locked", "sold_out"):
                        ctx.state.setdefault("dealer_blocked", {})[d["id"]] = ctx.clock.get("tick", 0) + 5
                    continue
                rounds, curve = self._pace(d, ctx.state.get("dealer_stats", {}).get(plan["key"]))
                rounds = min([x for x in (rounds, plan.get("rounds")) if x] or [None])  # strict pace or her usual final, the sooner
                plan.update(k=0, offers=[], dealer=d["id"], name=short_name(d), rounds=rounds, curve=curve)
                ctx.state.setdefault("plans", {})[str(th["id"])] = plan
                self._counts(d["id"])["opened"] += 1
                self._opens(d["id"]).append(ctx.clock.get("t_hours") or 0)
                ctx.log("haggle", "opened", dealer=d["id"], topic=topic, plan=plan)
                th = ctx.api.thread(th["id"])
            self.negotiate(th, d)
        for th in ctx.threads:  # bookkeeping for conversations that ended
            plan = ctx.state.get("plans", {}).get(str(th["id"]))
            if plan and th["status"] != "open" and not plan.get("done"):
                self.finish(th, plan)

    def negotiate(self, th: dict, dealer: dict):
        ctx = self.ctx
        plans = ctx.state.setdefault("plans", {})
        plan = plans.get(str(th["id"]))
        if plan is None and not ctx.S.get("adopt_threads", 0):
            # opened by someone else on our key (a teammate's tool): leave it alone, never talk over them
            seen = ctx.state.setdefault("foreign_threads", [])
            if th["id"] not in seen:
                seen.append(th["id"])
                ctx.log("haggle", "skip_foreign_thread", thread=th["id"], dealer=dealer["id"])
            return
        if plan is None:  # a conversation we did not open (the starter's): adopt it with defaults
            menu = (dealer.get("menu") or {}).get("sells", [])
            want = (th.get("topic") or {}).get("buy") or {}
            s = next((x for x in menu if x.get("pack") == want.get("pack")), menu[0] if menu else {})
            plan = {"side": "buy", "key": f"{dealer['id']}:buy:{want.get('pack', 'item')}", "lo": 12,
                    "hi": min(s.get("list_price", 26), ctx.me["cash"] - ctx.reserve()), "list": s.get("list_price"),
                    "opening": s.get("opening_ask"), "k": 0, "offers": [], "dealer": dealer["id"], "name": "Carmen",
                    "pack": want.get("pack")}
            plans[str(th["id"])] = plan
            ctx.log("haggle", "adopted", thread=th["id"], plan=plan)
        th = ctx.api.thread(th["id"])
        if th["status"] != "open":
            return self.finish(th, plan)
        theirs = [o for o in th.get("standing_offers", []) if o.get("maker") == dealer["id"] and o.get("status") == "open"]
        last = theirs[-1] if theirs else None
        ask = None
        if last:
            ask = (last.get("want") or {}).get("cash") if plan["side"] == "buy" else (last.get("give") or {}).get("cash")
        plan["asks"] = plan.get("asks", []) + ([ask] if ask is not None and (not plan.get("asks") or plan["asks"][-1] != ask) else [])
        buy = plan["side"] == "buy"
        nxt = self._next_price(plan)

        def good(p):  # inside our limits, and (buying) within today's budget and never with the cash we keep
            return p is not None and ((buy and p <= plan["hi"] and p <= ctx.budget_left()) or (not buy and p >= plan["lo"]))

        def good_final(p):  # a buying final above plan["hi"] would cost more than the item is worth to us: walk away
            if buy and p is not None and p > ctx.budget_left():
                return False  # never past today's budget or into the cash we keep
            return good(p)

        if last and last.get("final"):
            if good_final(ask) and ctx.take_accept():
                self._accept(last, th, plan, reason="final offer inside our cap")
            else:
                ctx.log("haggle", "final_declined", thread=th["id"], ask=ask, plan=plan)
                self._move_on(th, plan, f"final offer {ask} P is more than it is worth to us")
            return
        if buy and ask is not None and ask > ctx.budget_left():
            pass  # cannot afford it without touching the bond reserve: keep talking, never accept
        elif last and ask is not None and good(ask) and (plan.get("beginner") or (plan.get("target") is not None and (
                (buy and ask <= plan["target"]) or (not buy and ask >= plan["target"])))):
            if ctx.take_accept():
                why = "fixed first-deal price" if plan.get("beginner") else f"matches the best price any team got ({plan['target']})"
                return self._accept(last, th, plan, reason=why)
        if last and ask is not None and nxt is not None and ((buy and ask <= nxt) or (not buy and ask >= nxt)) and good(ask):
            if ctx.take_accept():
                return self._accept(last, th, plan, reason="her ask already beats our next step")
        if nxt is None:  # we are at our limit
            if buy and ask is not None and ask > plan["hi"]:
                plan["stuck"] = plan.get("stuck", 0) + 1
                if plan["stuck"] >= 2:  # she stays above what it is worth to us: don't buy, move on
                    self._move_on(th, plan, f"her price {ask} P stays above our limit {plan['hi']} P")
            return
        texts = KIND_BUY if buy else KIND_SELL
        text = texts[(plan["k"] + random.randrange(len(texts))) % len(texts)].format(name=plan.get("name", "Carmen"), p=nxt)
        # the safe band around the rule price: always a new price, never past our cap or her ask
        last_ours = plan["offers"][-1] if plan["offers"] else None
        if buy:
            lo_b = max(nxt - 2, (last_ours + 1) if last_ours is not None else 1, 1)
            hi_b = min(nxt + 2, plan["hi"], (ask - 1) if ask is not None else plan["hi"])
        else:
            lo_b = max(nxt - 2, plan["lo"], (ask + 1) if ask is not None else plan["lo"])
            hi_b = min(nxt + 2, (last_ours - 1) if last_ours is not None else nxt + 2)
        situation = {
            "counterparty": f"{dealer.get('name')} (dealer): {dealer.get('title', '')}", "traits": dealer.get("traits"),
            "bio": (dealer.get("bio") or "")[:400], "we_are": "buying" if buy else "selling",
            "item": th.get("topic"), "list_price": plan.get("list"), "her_latest_ask": ask,
            "other_teams_got": plan.get("intel"), "round": plan["k"] + 1,
            "lessons_from_every_conversation": plan.get("lessons"),
            "history": [{"us" if m.get("sender") == ctx.me["id"] else "them": ((m.get("offer") or {}).get("give") or {}).get("cash")
                         or ((m.get("offer") or {}).get("want") or {}).get("cash"),
                         "text": m.get("text") if m.get("sender") == ctx.me["id"] else f"<their_message>{m.get('text') or ''}</their_message>"}
                        for m in th.get("messages", [])[-10:]],
        }
        text, nxt, source = ctx.speak(situation, (lo_b, hi_b), (text, nxt)) if lo_b <= hi_b else (text, nxt, "rules")
        try:
            ctx.api.say(th["id"], text, price=nxt)
            plan["offers"].append(nxt)
            plan["k"] += 1
            ctx.log("haggle", "offer", thread=th["id"], price=nxt, ask=ask, k=plan["k"], by=source, text=text[:200])
        except BazaarError as e:
            if e.code != "wait_for_tick":
                ctx.log("haggle", "say_refused", thread=th["id"], price=nxt, error=str(e))

    def _next_price(self, plan):
        lo, hi, k, offers = plan["lo"], plan["hi"], plan["k"], plan["offers"]
        buy = plan["side"] == "buy"
        if buy:
            S = self.ctx.S
            p = math.floor(boulware(lo, hi, k, plan.get("rounds") or int(S["haggle_rounds"]), plan.get("curve") or S["haggle_curve"]))
            if offers:
                p = max(p, offers[-1] + 1)  # always a new price
            asks = plan.get("asks") or []
            if asks and asks[-1] is not None:
                p = min(p, asks[-1])  # never offer more than she asks
            return p if p <= hi and (not offers or p > offers[-1]) else None
        S = self.ctx.S
        first = next((a for a in (plan.get("asks") or []) if a is not None), None)
        if plan.get("ladder") and first is not None:
            lo = max(lo, first + 2)  # a deal at her opening price captures none of her range
        rounds, curve = plan.get("rounds") or int(S["haggle_rounds"]), plan.get("curve") or S["haggle_curve"]
        p = math.ceil(hi - (hi - lo) * min(1.0, k / rounds) ** curve)
        if offers:
            p = min(p, offers[-1] - 1)
        asks = plan.get("asks") or []
        if asks and asks[-1] is not None:
            p = max(p, asks[-1])
        return p if p >= lo and (not offers or p < offers[-1]) else None

    def _move_on(self, th, plan, why):
        """Leave a conversation that is not worth it, so the slot goes to a better deal."""
        try:
            self.ctx.api.close_thread(th["id"])
            self.ctx.log("haggle", "moved_on", thread=th["id"], key=plan.get("key"), why=why)
        except BazaarError as e:
            self.ctx.log("haggle", "close_refused", thread=th["id"], error=str(e)[:160])

    @staticmethod
    def switched(offer, th) -> str | None:
        """Why this dealer offer is not the deal the thread is about (a card switched in the structure), or None.
        Los Pícaros name one card and sell another; nobody may take a card of ours we did not put on the table."""
        topic = th.get("topic") or {}
        want_card = (topic.get("buy") or {}).get("card")
        g, w = offer.get("give") or {}, offer.get("want") or {}
        given = [x.split(":", 1)[1] for x in g.get("types") or [] if x.startswith("card:")]
        given += [a.get("ref") for a in g.get("assets") or [] if isinstance(a, dict)]
        if want_card and given != [want_card]:
            return f"offer gives {given or 'no card'}, the thread is for {want_card}"
        ours = set((topic.get("sell") or {}).get("assets") or [])
        asked = {a.get("id") if isinstance(a, dict) else a for a in w.get("assets") or []}
        if topic.get("sell") and (not asked or not asked <= ours):
            return f"offer asks for assets {sorted(asked)}, we sell {sorted(ours)}"
        return None

    def _accept(self, offer, th, plan, reason):
        ctx = self.ctx
        why = self.switched(offer, th)
        if why:
            ctx.log("haggle", "switch_refused", thread=th["id"], offer=offer.get("id"), dealer=th.get("with"), why=why)
            return
        try:
            ctx.api.accept(offer["id"])
            price = plan.get("asks", [None])[-1]
            ctx.log("haggle", "accept", thread=th["id"], offer=offer["id"], price=price, reason=reason)
            if plan.get("side") == "buy" and price:
                ctx.record_spend(price, plan.get("key"))
                k = f"{ctx.day_key()}:{plan.get('dealer')}"
                ctx.state.setdefault("dealer_buys", {})[k] = ctx.state["dealer_buys"].get(k, 0) + 1
        except BazaarError as e:
            ctx.log("haggle", "accept_refused", thread=th["id"], error=str(e))

    def finish(self, th, plan):
        ctx = self.ctx
        plan["done"] = True
        if plan.get("visit"):
            ctx.log("haggle", "visit_ended", thread=th["id"], status=th["status"])
            return
        stats = ctx.state.setdefault("dealer_stats", {}).setdefault(plan["key"], {"deals": [], "walked": 0, "rounds": []})
        price = None
        for o in th.get("standing_offers", []):
            if o.get("status") in ("accepted", "settled", "filled", "done"):
                price = (o.get("want") or {}).get("cash") or (o.get("give") or {}).get("cash")
        if th["status"] == "deal":
            price = price or (plan.get("asks") or [None])[-1] or (plan.get("offers") or [None])[-1]
            if price:
                stats["deals"].append(price)
            stats["rounds"].append(plan["k"])
            c = self._counts(plan["dealer"])
            c["deals"] += 1
            if plan.get("pack"):
                c["packs"] += 1
            ctx.log("haggle", "deal", thread=th["id"], price=price, list=plan.get("list"), rounds=plan["k"], key=plan["key"])
            opening = (plan.get("asks") or [None])[0]
            Learner_reward(ctx, plan, price, opening)
            ctx.open_new_packs()
        else:
            stats["walked"] += 1
            counters = [a for a in (plan.get("asks") or []) if a is not None]
            if counters:
                stats["last_counter"] = counters[-1]
            Learner_reward(ctx, plan, None, (plan.get("asks") or [None])[0])
            reason = th.get("closed_reason")
            if reason in ("persona_quota", "cooloff", "sold_out"):
                until = th.get("until_tick") or ctx.clock.get("tick", 0) + 10
                ctx.state.setdefault("dealer_blocked", {})[plan["dealer"]] = until
            ctx.log("haggle", "ended", thread=th["id"], status=th["status"], reason=reason, key=plan["key"])
