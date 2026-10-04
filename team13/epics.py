"""Epics: buy the epics worth most to us, from a team or from Pícaros (round 3 on).

An epic is worth its book (180) x our set multiplier: SAL-11 288, MAL-11 234, LAV-11 198 P to us (CHA-11 162), and
no module ever bought one: Values.wishlist() lists commons, uncommons and rares only. Saturday's feed: Pícaros sold
21 epics at 128-167 P and teams resold them at 179-216 (to Pilar, Banco and each other).

Scoring (organisers' slide, Sat 20:25): a team trade scores the value it adds at our values minus the price, a gain
capped at 50 and a loss in full; a dealer purchase scores on the ladder (share of the dealer's range we capture,
best three per level; Pícaros is level 4) and only a loss counts against us. Cash itself never scores. So, for each
epic we lack, best value first:
  - team: one public bid at min(our value - 50, the team cap in agent/caps.json), when that reaches the price teams
    resell epics at (epics_team_min). A filled bid banks the full +50 (SAL-11 at 200 is +88 at our values).
    Unfilled after epics_team_ticks, the card moves to Pícaros;
  - Pícaros: haggle up to min(its list price, our value - epics_dealer_margin, free cash): a deal never costs points
    and fills a level-4 ladder slot.
Before either: a team's ask already on a market (not ours) whose price plus that market's fee leaves us at least
epics_take_min_gain is accepted at once (t18 asked 245 for SAL-11 on El Rastro: 259 with the fee, +29 for us).
One live way per card: a bid is never up while we haggle for the same card, and is cancelled as soon as we hold it
(two fills would leave a second copy worth a quarter). One haggle at a time, never with a dealer another of our
agents is talking to. Cash promised to open bids and to the running haggle stays within free cash.
Off unless enable_epics = 1; nothing before round epics_round (3: Sunday from 10:38, when the round's score starts).
"""
from __future__ import annotations

import math

from bazaar_sdk import BazaarError
from eggs import line as egg_line
from trader import team_caps

TEAM_GAIN_CAP = 50     # a team trade's gain counts up to 50 (organisers' slide)
MAX_ROUNDS = 8         # our offers to the dealer before we walk
FILL_WAIT = 4          # ticks a vanished bid may still be settling before we treat it as gone


def targets(values, min_value: float) -> list:
    """[(ref, our value)] for released, non-hidden epics we do not hold, best value first."""
    out = []
    for ref, c in values.cards.items():
        if c.get("rarity") != "epic" or not c.get("released") or c.get("hidden") or values.held[ref] > 0:
            continue
        v = values.gain_of_adding([ref])
        if v >= min_value:
            out.append((ref, round(v, 1)))
    return sorted(out, key=lambda x: -x[1])


def team_price(value: float, cap: int | None, room: float) -> int:
    """The bid that still banks the full team gain, within the team cap and the cash we can promise."""
    p = value - TEAM_GAIN_CAP
    if cap is not None:
        p = min(p, cap)
    return int(math.floor(min(p, room)))


def epic_dealer(dealers: list, unlocked) -> tuple | None:
    """(dealer id, list price) of an unlocked, active dealer that sells epics of released sets, or None."""
    for d in dealers or []:
        if d.get("status") != "active" or d.get("id") not in (unlocked or []):
            continue
        for s in (d.get("menu") or {}).get("sells", []):
            if s.get("rarity") == "epic" and s.get("list_price"):
                return d["id"], int(s["list_price"])
    return None


class Epics:
    def __init__(self, ctx):
        self.ctx = ctx

    # ------------------------------------------------------------------ helpers
    def _open_bids(self) -> dict:
        """ref -> our open epic bid (cash for one card, no `to`), from ctx.my_offers."""
        me, out = self.ctx.me.get("id"), {}
        for o in self.ctx.my_offers:
            if o.get("maker") != me or o.get("to") or o.get("status", "open") != "open" or o.get("thread"):
                continue
            g, w = o.get("give") or {}, o.get("want") or {}
            refs = [t[5:] for t in w.get("types") or [] if t.startswith("card:")] + list(w.get("cards") or [])
            if len(refs) == 1 and g.get("cash") and not g.get("assets"):
                out[refs[0]] = o
        return out

    def _best_ask(self, ref):
        """(offer, cost with the fee) of the cheapest open ask for one copy of `ref` on a market we may trade on."""
        ctx, me, best = self.ctx, self.ctx.me.get("id"), None
        for vid, offers in (getattr(ctx, "boards", None) or {}).items():
            bps, per_card = ctx.venue_fee(vid) if hasattr(ctx, "venue_fee") else (500, 1)
            for o in offers:
                if o.get("to") and o.get("to") != me or o.get("maker") == me:
                    continue
                g, w = o.get("give") or {}, o.get("want") or {}
                cards = [a for a in g.get("assets") or [] if isinstance(a, dict)]
                if len(cards) != 1 or cards[0].get("ref") != ref or g.get("cash") or w.get("assets") or w.get("types") or not w.get("cash"):
                    continue
                cost = w["cash"] + math.ceil(bps * w["cash"] / 10000) + per_card
                if best is None or cost < best[1]:
                    best = (o, cost)
        return best

    def _cancel(self, o, why):
        try:
            self.ctx.api.cancel(o["id"])
            self.ctx.log("epics", "bid_cancelled", offer=o["id"], why=why)
        except BazaarError as e:
            self.ctx.log("epics", "cancel_refused", offer=o["id"], error=str(e)[:120])

    # ------------------------------------------------------------------ the tick
    def step(self):
        ctx, S = self.ctx, self.ctx.S
        if not S.get("enable_epics", 0) or ctx.values is None:
            return
        if (ctx.clock.get("round") or 0) < int(S.get("epics_round", 3)):
            return
        st = ctx.state.setdefault("epics", {"bids": {}, "team_tried": {}, "haggle": None, "dealer_tried": {}})
        tick, v = ctx.clock.get("tick", 0), ctx.values
        epic_refs = {r for r, c in v.cards.items() if c.get("rarity") == "epic"}
        bids = {r: o for r, o in self._open_bids().items() if r in epic_refs}

        # a card we now hold: no bid may stay up for it (a second fill is a copy worth a quarter)
        for ref, o in list(bids.items()):
            if v.held[ref] > 0:
                self._cancel(o, "held")
                bids.pop(ref)
        for ref in list(st["bids"]):
            if v.held[ref] > 0:
                ctx.log("epics", "bought_from_team", ref=ref, price=st["bids"][ref].get("price"))
                st["bids"].pop(ref)

        h = st.get("haggle")
        if h:
            self._haggle(st, h, tick)

        tgs = targets(v, float(S.get("epics_min_value", 150)))
        if not tgs:
            return
        caps = team_caps()
        promised = sum((o.get("give") or {}).get("cash") or 0 for o in bids.values())
        if st.get("haggle"):
            promised += st["haggle"]["cap"]
        free = ctx.me.get("cash", 0) - ctx.reserve() - promised
        dealer = epic_dealer(ctx.dealers, ctx.me.get("unlocked"))
        busy = {t.get("with") for t in ctx.threads if t.get("kind") == "persona" and t.get("status") == "open"}

        for ref, value in tgs:
            if (st.get("haggle") or {}).get("ref") == ref:
                continue
            o = bids.get(ref)
            rec = st["bids"].get(ref)
            if o:
                # unfilled for long enough, or a team's ask now pays: the bid goes. The record stays FILL_WAIT ticks:
                # a team may have taken the bid in the same tick, and its card arrives next tick
                ask = self._best_ask(ref)
                better = ask and value - ask[1] >= float(S.get("epics_take_min_gain", 20))
                if better or (dealer and tick - (rec or {}).get("tick", tick) >= int(S.get("epics_team_ticks", 40))):
                    self._cancel(o, "ask_to_take" if better else "unfilled")
                    r = st["bids"].setdefault(ref, {"tick": tick, "price": (o.get("give") or {}).get("cash")})
                    r.update(seen=tick, cancelled=True)
                continue
            if rec and tick - rec.get("seen", rec["tick"]) < FILL_WAIT:
                continue  # our bid just left the book: it may be settling, the card arrives next tick
            if rec:
                st["bids"].pop(ref, None)  # expired or cancelled elsewhere
            # 0) a team already asks a price that pays: take it (the record holds the card's other ways off meanwhile)
            ask = self._best_ask(ref)
            if ask and value - ask[1] >= float(S.get("epics_take_min_gain", 20)) and ask[1] <= free and ctx.take_accept():
                try:
                    ctx.api.accept(ask[0]["id"])
                    st["bids"][ref] = {"tick": tick, "seen": tick, "cancelled": True, "taken": ask[0]["id"], "price": ask[1]}
                    st["team_tried"][ref] = tick
                    free -= ask[1]
                    ctx.log("epics", "took_ask", ref=ref, offer=ask[0]["id"], cost=ask[1], value=value,
                            gain=round(min(TEAM_GAIN_CAP, value - ask[1]), 1))
                except BazaarError as e:
                    ctx.log("epics", "take_refused", ref=ref, offer=ask[0]["id"], error=str(e)[:160])
                continue
            # 1) a team: once per card, when the full +50 is reachable at a price teams resell epics at
            if ref not in st["team_tried"]:
                p = team_price(value, caps.get(ref), free)
                if p >= int(S.get("epics_team_min", 190)):
                    try:
                        venue = S.get("epics_venue", "rastro")
                        got = ctx.api.list_offer({"cash": p}, {"cards": [ref]}, venue=venue,
                                                 expires_in_ticks=int(S.get("epics_team_ticks", 40)) + 5)
                        st["bids"][ref] = {"offer": got.get("id"), "price": p, "tick": tick, "seen": tick}
                        st["team_tried"][ref] = tick
                        free -= p
                        ctx.log("epics", "bid", ref=ref, price=p, value=value, venue=venue, offer=got.get("id"),
                                gain=round(min(TEAM_GAIN_CAP, value - p), 1))
                    except BazaarError as e:
                        st["team_tried"][ref] = tick
                        ctx.log("epics", "bid_refused", ref=ref, price=p, error=str(e)[:160])
                    continue
            # 2) the dealer: one haggle at a time, never with a dealer one of our agents is talking to
            if st.get("haggle") or not dealer or dealer[0] in busy:
                continue
            if tick - st["dealer_tried"].get(ref, -10 ** 6) < int(S.get("epics_retry_ticks", 30)):
                continue
            did, list_price = dealer
            cap = int(min(list_price, value - float(S.get("epics_dealer_margin", 5)), free))
            start = int(math.floor(list_price * float(S.get("epics_start_share", 0.74))))
            if cap < start:
                continue
            try:
                th = ctx.api.open_thread(did, topic={"buy": {"card": ref}})
            except BazaarError as e:
                st["dealer_tried"][ref] = tick
                ctx.log("epics", "open_refused", dealer=did, ref=ref, error=str(e)[:120])
                continue
            st["dealer_tried"][ref] = tick
            st["haggle"] = {"ref": ref, "dealer": did, "thread": th["id"], "price": start, "cap": cap, "list": list_price,
                            "value": value, "rounds": 0, "last_tick": tick, "phase": "haggle", "held_before": v.held[ref]}
            extra = egg_line(ctx.state, did)  # one line of Madrid lore, once ever (eggs.py)
            try:
                ctx.api.say(th["id"], f"Buenas. I'm after {ref}. {start} primas, cash in hand." + (f" {extra}" if extra else ""),
                            price=start)
            except BazaarError:
                pass
            ctx.log("epics", "haggle_start", ref=ref, dealer=did, price=start, cap=cap, value=value, thread=th["id"])
            free -= cap

        for ref, rec in st["bids"].items():
            if ref in bids and not rec.get("cancelled"):
                rec["seen"] = tick

    # ------------------------------------------------------------------ the dealer haggle
    def _haggle(self, st, h, tick):
        ctx = self.ctx
        if h["phase"] == "wait_card":
            if ctx.values.held[h["ref"]] > h["held_before"]:
                ctx.log("epics", "bought_from_dealer", ref=h["ref"], dealer=h["dealer"], price=h.get("paid"), value=h["value"])
                st["haggle"] = None
            elif tick - h["last_tick"] > 6:
                ctx.log("epics", "card_not_arrived", ref=h["ref"])
                st["haggle"] = None
            return
        if tick == h["last_tick"]:
            return
        h["last_tick"] = tick
        try:
            th = ctx.api.thread(h["thread"])
        except BazaarError:
            return
        if th.get("status") != "open":
            if th.get("status") == "deal":  # the dealer took our standing price
                h.update(phase="wait_card", paid=h["price"])
            else:
                ctx.log("epics", "dealer_walked", ref=h["ref"], dealer=h["dealer"], price=h["price"], status=th.get("status"))
                st["haggle"] = None
            return
        theirs = [o for o in th.get("standing_offers", []) if o.get("maker") == h["dealer"] and o.get("status") == "open"]
        o = theirs[-1] if theirs else None
        if o:
            g = o.get("give") or {}
            given = [x[5:] for x in g.get("types") or [] if x.startswith("card:")] + \
                    [x.get("ref") for x in g.get("assets") or [] if isinstance(x, dict)]
            ask = (o.get("want") or {}).get("cash")
            # take it inside our cap once it is close to our own price, or when the talk has run long
            near = ask is not None and (ask <= h["price"] * 1.06 + 2 or h["rounds"] >= 4 or o.get("final"))
            if given == [h["ref"]] and ask is not None and ask <= h["cap"] and near and ctx.take_accept():
                try:
                    ctx.api.accept(o["id"])
                    h.update(phase="wait_card", paid=ask)
                    ctx.log("epics", "accepted", ref=h["ref"], dealer=h["dealer"], price=ask, cap=h["cap"])
                except BazaarError as e:
                    ctx.log("epics", "accept_refused", ref=h["ref"], error=str(e)[:160])
                return
        if h["rounds"] >= MAX_ROUNDS or h["price"] >= h["cap"]:
            try:
                ctx.api.close_thread(h["thread"])
            except BazaarError:
                pass
            ctx.log("epics", "gave_up", ref=h["ref"], dealer=h["dealer"], last_price=h["price"], cap=h["cap"])
            st["haggle"] = None
            return
        step = max(2, math.ceil((h["cap"] - h["price"]) / 4))
        h["price"] = min(h["cap"], h["price"] + step)
        h["rounds"] += 1
        try:
            ctx.api.say(h["thread"], f"I can go to {h['price']} for {h['ref']}.", price=h["price"])
        except BazaarError:
            pass
