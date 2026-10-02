"""Dealer negotiation: the ladder scores the share of each dealer's price range we capture (our best three deals per
level), and three negotiated deals unlock the next dealer early.

Strategy, from the rules and the kickoff deck:
- Dealers move only when we move, small steps earn small steps, the same price twice earns nothing. So we use a
  Boulware schedule: open low, concede slowly at first and faster near our cap, a new price every message.
- Abuela likes kindness: every message is polite, and the words vary so no message repeats.
- A final offer ("final": true) is take-it-or-walk: we take it when it is inside our cap.
- If the dealer's standing ask is already at or below what we would offer next, we take it instead of overpaying.
- After every conversation we remember the outcome per dealer and item, and open the next one lower if we did well.
"""
from __future__ import annotations

import math
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
]
KIND_SELL = [
    "¡Hola, {name}! Tengo una carta preciosa para usted. ¿{p} primas le parece bien?",
    "Thank you, {name}. This one is in lovely condition. Would {p} primas be fair?",
    "{name}, for you I can come down a little: {p} primas. Muchas gracias.",
    "Gracias, {name}. ¿Lo dejamos en {p}? Me haría muy feliz.",
    "You are very kind, {name}. {p} primas and it is yours.",
]


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
        for s in menu.get("sells", []):  # 1) packs: the cleanest price range to capture
            if S["haggle_buy_packs"] and "pack" in s and counts["packs"] < s.get("per_team_per_hour", 3):
                key = f"{dealer['id']}:buy:{s['pack']}"
                hi = min(math.floor((s.get("list_price") or s.get("opening_ask", 30)) * S["haggle_cap"]), cash)
                if hi < 5 or key in unsupported:
                    continue
                lo = self._opening(stats.get(key), s, side="buy")
                return {"buy": {"pack": s["pack"]}}, {"side": "buy", "key": key, "lo": lo, "hi": hi, "pack": s["pack"],
                                                       "list": s.get("list_price"), "opening": s.get("opening_ask")}
        buys = {b.get("rarity") for b in menu.get("buys", [])}
        for a in ctx.values.spares() if S["haggle_sell_spares"] else []:  # 2) sell a spare, never below its value to us
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
            return {"sell": {"asset": a["id"]}}, {"side": "sell", "key": key, "lo": floor, "hi": hi_ask,
                                                   "list": book, "asset": a["id"], "ref": a["ref"]}
        for s in menu.get("sells", []):  # 3) buy single cards we want, cheaply
            if S["haggle_buy_cards"] and s.get("rarity") and cash > 15:
                for ref, gain in ctx.values.wishlist():
                    if ctx.values.cards[ref]["rarity"] != s["rarity"]:
                        continue
                    key = f"{dealer['id']}:buy:{s['rarity']}"
                    if key in unsupported:
                        break
                    hi = min(math.floor(s.get("list_price", 10) * S["haggle_cap"]), cash, math.floor(gain))
                    if hi < 3:
                        break
                    lo = self._opening(stats.get(key), s, side="buy")
                    return {"buy": {"card": ref}}, {"side": "buy", "key": key, "lo": min(lo, hi), "hi": hi,
                                                    "list": s.get("list_price"), "ref": ref}
        return None

    def _opening(self, st, s, side):
        lst = s.get("list_price") or s.get("opening_ask") or 10
        if side == "buy":
            lo = self.ctx.S["haggle_open"] * lst
            if st and st.get("deals"):
                lo = min(lo, 0.85 * min(st["deals"]))  # she went that low before: start below it
            return max(1, math.floor(lo))
        hi = (2 - self.ctx.S["haggle_open"]) * 1.1 * lst  # mirror of the buy opening, for selling
        if st and st.get("deals"):
            hi = max(hi, 1.15 * max(st["deals"]))
        return math.ceil(hi)

    # ------------------------------------------------------------------ the loop
    def step(self):
        ctx = self.ctx
        dealers = [d for d in ctx.dealers if d.get("status") == "active" and d.get("id") in ctx.me.get("unlocked", [])]
        open_threads = {t["with"]: t for t in ctx.threads if t.get("kind") == "persona" and t["status"] == "open"}
        for d in dealers:
            th = open_threads.get(d["id"])
            if th is None:
                if len([t for t in ctx.threads if t["status"] == "open"]) >= ctx.limit("max_open_threads_per_team", 6):
                    continue
                choice = self.choose_topic(d)
                if not choice:
                    continue
                topic, plan = choice
                try:
                    th = ctx.api.open_thread(d["id"], topic=topic)
                except BazaarError as e:
                    ctx.log("haggle", "open_refused", dealer=d["id"], topic=topic, error=str(e))
                    if e.code in ("invalid", "bad_topic", "http_400", "http_422") or e.status in (400, 422):
                        ctx.state["unsupported_topics"] = sorted(set(ctx.state.get("unsupported_topics", [])) | {plan["key"]})
                    elif e.code in ("persona_quota", "cooloff", "locked", "sold_out"):
                        ctx.state.setdefault("dealer_blocked", {})[d["id"]] = ctx.clock.get("tick", 0) + 5
                    continue
                plan.update(k=0, offers=[], dealer=d["id"], name=d.get("name", d["id"]).split()[0])
                ctx.state.setdefault("plans", {})[str(th["id"])] = plan
                self._counts(d["id"])["opened"] += 1
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

        def good(p):
            return p is not None and ((buy and p <= plan["hi"]) or (not buy and p >= plan["lo"]))

        if last and last.get("final"):
            if good(ask) and ctx.take_accept():
                self._accept(last, th, plan, reason="final offer inside our cap")
            else:
                ctx.log("haggle", "final_declined", thread=th["id"], ask=ask, plan=plan)
            return
        if last and ask is not None and nxt is not None and ((buy and ask <= nxt) or (not buy and ask >= nxt)) and good(ask):
            if ctx.take_accept():
                return self._accept(last, th, plan, reason="her ask already beats our next step")
        if nxt is None:
            return  # at our cap: wait for her to move or name a final offer
        texts = KIND_BUY if buy else KIND_SELL
        text = texts[(plan["k"] + random.randrange(len(texts))) % len(texts)].format(name=plan.get("name", "Carmen"), p=nxt)
        try:
            ctx.api.say(th["id"], text, price=nxt)
            plan["offers"].append(nxt)
            plan["k"] += 1
            ctx.log("haggle", "offer", thread=th["id"], price=nxt, ask=ask, k=plan["k"])
        except BazaarError as e:
            if e.code != "wait_for_tick":
                ctx.log("haggle", "say_refused", thread=th["id"], price=nxt, error=str(e))

    def _next_price(self, plan):
        lo, hi, k, offers = plan["lo"], plan["hi"], plan["k"], plan["offers"]
        buy = plan["side"] == "buy"
        if buy:
            S = self.ctx.S
            p = math.floor(boulware(lo, hi, k, int(S["haggle_rounds"]), S["haggle_curve"]))
            if offers:
                p = max(p, offers[-1] + 1)  # always a new price
            asks = plan.get("asks") or []
            if asks and asks[-1] is not None:
                p = min(p, asks[-1])  # never offer more than she asks
            return p if p <= hi and (not offers or p > offers[-1]) else None
        S = self.ctx.S
        p = math.ceil(hi - (hi - lo) * min(1.0, k / S["haggle_rounds"]) ** S["haggle_curve"])
        if offers:
            p = min(p, offers[-1] - 1)
        asks = plan.get("asks") or []
        if asks and asks[-1] is not None:
            p = max(p, asks[-1])
        return p if p >= lo and (not offers or p < offers[-1]) else None

    def _accept(self, offer, th, plan, reason):
        ctx = self.ctx
        try:
            ctx.api.accept(offer["id"])
            ctx.log("haggle", "accept", thread=th["id"], offer=offer["id"], price=plan.get("asks", [None])[-1], reason=reason)
        except BazaarError as e:
            ctx.log("haggle", "accept_refused", thread=th["id"], error=str(e))

    def finish(self, th, plan):
        ctx = self.ctx
        plan["done"] = True
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
            ctx.open_new_packs()
        else:
            stats["walked"] += 1
            reason = th.get("closed_reason")
            if reason in ("persona_quota", "cooloff", "sold_out"):
                until = th.get("until_tick") or ctx.clock.get("tick", 0) + 10
                ctx.state.setdefault("dealer_blocked", {})[plan["dealer"]] = until
            ctx.log("haggle", "ended", thread=th["id"], status=th["status"], reason=reason, key=plan["key"])
