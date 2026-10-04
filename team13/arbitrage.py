"""Dealer -> team arbitrage: buy a card from a dealer and sell it at once into a team's open bid for it, when the
two deals together score.

Scoring (organisers' slide, Sat 20:25): a deal = value it adds to our collection - price paid + price received.
  - buying from a dealer: a gain counts on the ladder, a loss (price above our value V) counts in full;
  - selling to a team: a gain counts up to 50, a loss in full.
So for a buy at P and a sale into a bid B (we accept it, so we pay that market's fee f):
  score(P) = min(0, V - P) + min(50, B - f - V)        (B - f - V < 0 counts in full too)
The ladder is deliberately left out of score(P): it keeps the best three deals per dealer level and counts a
missing one as zero, so a fourth, worse deal is simply ignored and an arbitrage buy can never cost us ladder points.
It can only win them, and most at a dealer we have never closed with. arb_ladder_slack (0 by default) is how many
P off arb_min_score that is worth at a dealer whose best three still has an empty slot.
We take a pair only when score(P) >= arb_min_score at the highest price we would pay, the bid lives long enough
for a short haggle, and the cash left keeps the reserve. The bid is re-checked before we buy and we sell into it as
soon as the card arrives. One pair at a time; never a dealer another of our agents is talking to.
Bids addressed to us (offer.listed with "to": us in the feed; not in any public book) count like public ones; one is
alive while the feed shows no offer.cancelled for it and it has not expired.
Speed (Sunday, 15 s ticks: a good bid is taken within minutes): a scan every SCAN_EVERY ticks, the haggle opens near
the price this dealer last sold that rarity at and reaches the cap in two steps.
The other way round: a team's ask below our value is bought at once (+min(50, value - price - fee)); then, if a
dealer buys that rarity, we offer the card to it at our value or more (never less: a dealer loss counts in full),
which fills a ladder slot; a dealer that will not pay leaves the card with us at no loss. Epics are left to epics.py.
Off unless enable_arbitrage = 1.
"""
from __future__ import annotations

import json
import math
import os

from bazaar_sdk import BazaarError

HERE = os.path.dirname(os.path.abspath(__file__))
FEED_STORE = os.path.join(HERE, "logs", "feed_events.jsonl")
SCAN_EVERY = 2         # ticks between scans while idle (a good bid is taken within minutes)
MIN_BID_LIFE = 12      # ticks a bid must still live when we start (the haggle now takes ~3-6 ticks)
RECENT_TICKS = 300     # dealer sale prices this recent set where the haggle opens
RESALE_ROUNDS = 4      # our asks to a dealer before we keep the card
MAX_ROUNDS = 12        # our offers to the dealer before we walk
SELL_TRIES = 20        # ticks we keep trying to sell into the bid (the accept slot is shared by three agents)
START_SHARE = 0.72     # first offer as a share of the dealer's list price


def score(V: float, P: float, net_in: float) -> float:
    gain = net_in - V
    return min(0.0, V - P) + (min(50.0, gain) if gain > 0 else gain)


def max_price(V: float, net_in: float, min_score: float) -> int | None:
    """Highest dealer price at which the pair still scores min_score, or None."""
    gain = net_in - V
    team_part = min(50.0, gain) if gain > 0 else gain
    if team_part < min_score:
        return None
    return math.floor(V + team_part - min_score)  # above V every extra P is a full loss


LADDER_BEST = 3        # the ladder keeps the best three deals per dealer level, a missing one scores zero


def dealer_deals(ctx, did) -> int:
    """Deals our team has closed with this dealer. ctx.threads is the recent window (50 threads, mostly open ones),
    so counting deals in it reads far too low: use ctx.deal_threads, /api/me/threads?status=deal, when it is there."""
    threads = getattr(ctx, "deal_threads", None)
    threads = threads if threads is not None else (getattr(ctx, "threads", None) or [])
    return sum(1 for t in threads
               if t.get("kind") == "persona" and t.get("with") == did and t.get("status") == "deal")


def fee(price, venue) -> int:
    return math.ceil((venue.get("fee_bps") or 0) * price / 10000) + (venue.get("fee_per_card") or 0)


def candidates(ctx, venues, boards, makers, dealers, min_score, ladder_slack=0, min_reach=0.0, recent=None) -> list:
    """[(score at our max price, plan)] best first, from open team bids and dealer menus."""
    v, me = ctx.values, ctx.me.get("id")
    sells = [(d["id"], s["rarity"], s.get("sets"), s["list_price"], d.get("level")) for d in dealers if d.get("status") == "active"
             and d["id"] in (ctx.me.get("unlocked") or []) for s in (d.get("menu") or {}).get("sells", []) if s.get("rarity")]
    # an empty best-three slot at a dealer is free ladder points: that pair may pay a little more for the card
    closed = {did: dealer_deals(ctx, did) for did, *_ in sells}
    by_id = {x["venue"]: x for x in venues}
    out = []
    for vid, offers in boards.items():
        ven = by_id.get(vid) or {}
        if ven.get("owner") == me:
            continue  # we cannot trade on our own market
        for o in offers:
            if o.get("to") and o.get("to") != me or makers.get(o.get("id")) == me:
                continue
            g, w = o.get("give") or {}, o.get("want") or {}
            wanted = [x[5:] for x in w.get("types") or [] if x.startswith("card:")] + [a["ref"] for a in w.get("assets") or [] if isinstance(a, dict)]
            if len(wanted) != 1 or g.get("assets") or not g.get("cash") or wanted[0] not in v.cards:
                continue
            ref, B = wanted[0], g["cash"]
            if (o.get("expires_tick") or 10 ** 9) - ctx.clock.get("tick", 0) < MIN_BID_LIFE:
                continue
            c = v.cards[ref]
            V, net_in = v.gain_of_adding([ref]), B - fee(B, ven)
            if max_price(V, net_in, min_score - ladder_slack) is None:
                continue  # no dealer price can make this bid pay, however empty its ladder is
            for did, rar, sets, L, level in sells:
                if rar != c.get("rarity") or (isinstance(sets, list) and c["set"] not in sets):
                    continue
                slot = closed.get(did, 0) < LADDER_BEST
                bar = min_score - (ladder_slack if slot else 0)
                pmax = max_price(V, net_in, bar)
                if pmax is None:
                    continue
                cap = min(pmax, L)  # never above the dealer's list price
                start = math.floor(L * START_SHARE)
                if cap < start or cap < L * min_reach:
                    continue  # the dealer walks long before such a bar (Pícaros walked from 46 of 63, three times)
                seen = (recent or {}).get((did, rar))
                if seen:  # open near what this dealer just took from another team: two steps to the cap, not four
                    start = min(cap, max(start, min(seen) - 2))
                plan = {"ref": ref, "dealer": did, "bid": o["id"], "bid_price": B, "bid_venue": vid, "net_in": net_in,
                        "value": round(V, 1), "list": L, "cap": cap, "price": start, "level": level,
                        "dealer_deals": closed.get(did, 0), "ladder_slot": slot, "bar": round(bar, 1),
                        "addressed": o.get("to") == me, "bid_expires": o.get("expires_tick")}
                out.append((round(score(V, cap, net_in), 1), plan))
    return sorted(out, key=lambda x: -x[0])


def value_buys(ctx, venues, boards, makers, min_score) -> list:
    """[(score, plan)] best first: a team's ask for one card (not an epic) whose price plus the market's fee is below
    what the card is worth to us by at least min_score. The team trade scores min(50, value - cost)."""
    v, me = ctx.values, ctx.me.get("id")
    by_id = {x["venue"]: x for x in venues}
    out = []
    for vid, offers in boards.items():
        ven = by_id.get(vid) or {}
        if ven.get("owner") == me:
            continue
        for o in offers:
            if o.get("to") and o.get("to") != me or makers.get(o.get("id")) == me:
                continue
            g, w = o.get("give") or {}, o.get("want") or {}
            cards = [a for a in g.get("assets") or [] if isinstance(a, dict)]
            if len(cards) != 1 or g.get("cash") or w.get("assets") or w.get("types") or w.get("cards") or not w.get("cash"):
                continue
            ref = cards[0].get("ref")
            if ref not in v.cards or v.cards[ref].get("rarity") in ("epic", "legendary"):
                continue
            cost = w["cash"] + fee(w["cash"], ven)
            V = v.gain_of_adding([ref])
            if V - cost >= min_score:
                out.append((round(min(50.0, V - cost), 1), {"ref": ref, "offer": o["id"], "venue": vid, "cost": cost,
                                                            "value": round(V, 1), "rarity": v.cards[ref].get("rarity"),
                                                            "set": v.cards[ref].get("set")}))
    return sorted(out, key=lambda x: -x[0])


def resale_dealer(ctx, rarity, set_id, busy):
    """An unlocked dealer that buys this rarity of this set and is free; the collector who favours the set first."""
    best = None
    for d in ctx.dealers or []:
        if d.get("status") != "active" or d.get("id") not in (ctx.me.get("unlocked") or []) or d.get("id") in busy:
            continue
        for b in (d.get("menu") or {}).get("buys", []):
            sets = b.get("sets")
            if b.get("rarity") == rarity and (sets == "released" or (isinstance(sets, list) and set_id in sets)):
                rank = 0 if isinstance(sets, list) else 1  # Pilar lists the sets she loves
                if best is None or rank < best[0]:
                    best = (rank, d["id"])
    return best[1] if best else None


class Arbitrage:
    def __init__(self, ctx):
        self.ctx = ctx

    def _bid_open(self, plan) -> bool:
        if plan.get("addressed"):  # not in any public book: alive until the feed shows it cancelled, or it expires
            if (plan.get("bid_expires") or 0) <= self.ctx.clock.get("tick", 0):
                return False
            try:
                with open(FEED_STORE) as f:
                    return not any('"offer.cancelled"' in line and f'"offer": {plan["bid"]},' in line for line in f)
            except OSError:
                return False
        try:
            return any(o.get("id") == plan["bid"] for o in self.ctx.public_get(f"/api/venues/{plan['bid_venue']}/offers").get("offers", []))
        except Exception:
            return False

    def step(self):
        ctx, S = self.ctx, self.ctx.S
        if not S.get("enable_arbitrage", 0) or ctx.values is None:
            return
        st = ctx.state.setdefault("arb", {"active": None, "scan": -999, "done": {}})
        tick = ctx.clock.get("tick", 0)
        if st.get("resale"):
            self._resale(st, st["resale"], tick)
        a = st["active"]
        if a:
            return self._progress(st, a, tick)
        if tick - st["scan"] < SCAN_EVERY:
            return
        st["scan"] = tick
        day = ctx.day_key()
        if st["done"].get(day, 0) >= S.get("arb_max_per_day", 6):
            return
        try:
            venues = [x for x in ctx.public_get("/api/venues").get("venues", []) if x.get("status") == "open"]
            boards = {x["venue"]: ctx.public_get(f"/api/venues/{x['venue']}/offers").get("offers", []) for x in venues}
            makers, addressed, cancelled, recent = {}, {}, set(), {}
            me = ctx.me.get("id")
            with open(FEED_STORE) as f:
                for line in f:
                    if '"offer.listed"' in line:
                        o = (json.loads(line).get("payload") or {}).get("offer") or {}
                        makers[o.get("id")] = o.get("maker")
                        if o.get("to") == me and o.get("id") is not None:
                            addressed[o["id"]] = o
                    elif '"offer.cancelled"' in line:
                        cancelled.add((json.loads(line).get("payload") or {}).get("offer"))
                    elif '"settlement"' in line and '"persona"' in line:
                        e = json.loads(line)
                        p = e.get("payload") or {}
                        items = [i for i in p.get("items") or [] if i.get("kind") == "card"]
                        if p.get("persona") and len(items) == 1 and items[0].get("frm") == p["persona"] and p.get("price") \
                                and tick - (e.get("tick") or 0) <= RECENT_TICKS:
                            recent.setdefault((p["persona"], items[0].get("rarity")), []).append(p["price"])
            for oid, o in addressed.items():  # bids addressed to us: no public book shows them
                if oid not in cancelled and (o.get("expires_tick") or 0) - tick >= MIN_BID_LIFE and o.get("venue") in boards:
                    boards[o["venue"]].append(o)
            dealers = ctx.dealers or ctx.public_get("/api/dealers").get("personas", [])
            if S.get("arb_ladder_slack", 0):  # one keyed read per scan, only when an empty ladder slot may move the bar
                ctx.deal_threads = ctx.raw.my_threads(status="deal").get("threads", [])
        except Exception as e:
            ctx.log("arb", "scan_failed", error=repr(e)[:160])
            return
        busy = {t.get("with") for t in ctx.threads if t.get("kind") == "persona" and t.get("status") == "open"}
        reserve = ctx.reserve()
        for sc, plan in candidates(ctx, venues, boards, makers, dealers, S.get("arb_min_score", 8),
                                   S.get("arb_ladder_slack", 0), S.get("arb_min_reach", 0.85), recent):
            if plan["dealer"] in busy or ctx.me.get("cash", 0) - plan["cap"] < reserve:
                continue
            try:
                th = ctx.api.open_thread(plan["dealer"], topic={"buy": {"card": plan["ref"]}})
            except BazaarError as e:
                ctx.log("arb", "open_refused", dealer=plan["dealer"], ref=plan["ref"], error=str(e)[:120])
                continue
            plan.update(thread=th["id"], rounds=0, last_tick=tick, phase="haggle", held_before=ctx.values.held[plan["ref"]], score=sc)
            ctx.api.say(th["id"], f"Buenas. I'm after {plan['ref']}. {plan['price']} primas, cash ready.", price=plan["price"])
            st["active"] = plan
            ctx.log("arb", "start", thread=th["id"],
                    **{k: plan[k] for k in ("ref", "dealer", "bid", "bid_price", "value", "cap", "price", "score",
                                            "level", "dealer_deals", "ladder_slot", "bar", "addressed")})
            return
        # the other way round: a team's ask below our value (one at a time, never while a resale is running)
        if st.get("resale"):
            return
        for sc, vb in value_buys(ctx, venues, boards, makers, S.get("arb_min_score", 8)):
            if ctx.me.get("cash", 0) - vb["cost"] < reserve or not ctx.take_accept():
                continue
            try:
                ctx.api.accept(vb["offer"])
            except BazaarError as e:
                ctx.log("arb", "value_buy_refused", ref=vb["ref"], offer=vb["offer"], error=str(e)[:160])
                continue
            st["resale"] = {**vb, "phase": "wait_card", "held_before": ctx.values.held[vb["ref"]], "last_tick": tick}
            st["done"][day] = st["done"].get(day, 0) + 1
            ctx.log("arb", "value_buy", score=sc, **vb)
            return

    def _progress(self, st, a, tick):
        ctx = self.ctx
        if a["phase"] == "haggle":
            if tick == a["last_tick"]:
                return
            a["last_tick"] = tick
            try:
                th = ctx.api.thread(a["thread"])
            except BazaarError:
                return
            if th.get("status") != "open":
                if th.get("status") == "deal":  # the dealer took our standing price
                    a.update(phase="wait_card", paid=a["price"])
                else:
                    ctx.log("arb", "dealer_walked", ref=a["ref"], dealer=a["dealer"], status=th.get("status"))
                    st["active"] = None
                return
            mine = [o for o in th.get("standing_offers", []) if o.get("maker") == a["dealer"] and o.get("status") == "open"]
            o = mine[-1] if mine else None
            if o:
                g = o.get("give") or {}
                given = [x[5:] for x in g.get("types") or [] if x.startswith("card:")] + [x.get("ref") for x in g.get("assets") or [] if isinstance(x, dict)]
                w = o.get("want") or {}
                ask = w.get("cash") if not (w.get("assets") or w.get("types") or w.get("cards")) else None  # cash only
                if given == [a["ref"]] and ask is not None and ask <= a["cap"] and ctx.take_accept():
                    if not self._bid_open(a):  # the buyer left: do not buy a card we only wanted to pass on
                        ctx.log("arb", "bid_gone_before_buy", ref=a["ref"], bid=a["bid"])
                        return self._abort(st, a)
                    try:
                        ctx.api.accept(o["id"])
                        a.update(phase="wait_card", paid=ask)
                        ctx.log("arb", "bought", ref=a["ref"], dealer=a["dealer"], price=ask, cap=a["cap"])
                    except BazaarError as e:
                        ctx.log("arb", "buy_refused", error=str(e)[:160])
                    return
            if a["rounds"] >= MAX_ROUNDS:
                return self._abort(st, a)
            step = max(1, math.ceil((a["cap"] - a["price"]) / 2))
            a["price"] = min(a["cap"], a["price"] + step)
            a["rounds"] += 1
            try:
                ctx.api.say(a["thread"], f"I can stretch to {a['price']} primas for {a['ref']}.", price=a["price"])
            except BazaarError:
                pass
            return
        if a["phase"] == "wait_card":
            copies = [x for x in ctx.values.assets if x["ref"] == a["ref"]]
            if len(copies) <= a["held_before"]:
                if tick - a["last_tick"] > 6:
                    ctx.log("arb", "card_not_arrived", ref=a["ref"])
                    st["active"] = None
                return
            if tick == a.get("sell_tick"):
                return
            a["sell_tick"] = tick
            asset = max(x["id"] for x in copies)  # the copy we just bought
            promised = ctx.locked_assets(reserved=False) if hasattr(ctx, "locked_assets") else set()
            if asset in promised:  # a teammate's agent is selling it to a dealer or a team
                a["sell_tries"] = a.get("sell_tries", 0) + 1
                ctx.log("arb", "copy_promised", ref=a["ref"], asset=asset, tries=a["sell_tries"])
                if a["sell_tries"] >= SELL_TRIES:
                    st["active"] = None
                return
            if not ctx.take_accept():
                return
            try:
                ctx.api.accept(a["bid"], assets=[asset])
                ctx.log("arb", "sold", ref=a["ref"], bid=a["bid"], price=a["bid_price"], paid=a.get("paid"),
                        value=a["value"], score=round(score(a["value"], a.get("paid") or a["cap"], a["net_in"]), 1))
                day = ctx.day_key()
                st["done"][day] = st["done"].get(day, 0) + 1
                st["active"] = None
            except BazaarError as e:
                # the team's one accept per tick is shared by our three agents, and the server may be busy: retry while
                # the bid lives; only a vanished bid ends the pair, and then we say so (the card stays with us)
                a["sell_tries"] = a.get("sell_tries", 0) + 1
                ctx.log("arb", "sell_retry", ref=a["ref"], bid=a["bid"], tries=a["sell_tries"], error=str(e)[:160])
                if a["sell_tries"] >= SELL_TRIES or not self._bid_open(a):
                    ctx.log("arb", "stuck_with_card", ref=a["ref"], bid=a["bid"], paid=a.get("paid"), value=a["value"])
                    st["active"] = None

    def _resale(self, st, r, tick):
        """After a value buy: the card arrives, then a dealer may take it at our value or more (a ladder slot)."""
        ctx = self.ctx
        if tick == r.get("last_tick"):
            return
        if r["phase"] == "wait_card":
            copies = [x for x in ctx.values.assets if x["ref"] == r["ref"]]
            if len(copies) <= r["held_before"]:
                if tick - r["last_tick"] > 6:
                    ctx.log("arb", "value_buy_no_card", ref=r["ref"])
                    st["resale"] = None
                return
            busy = {t.get("with") for t in ctx.threads if t.get("kind") == "persona" and t.get("status") == "open"}
            dealer = resale_dealer(ctx, r.get("rarity"), r.get("set"), busy)
            floor = math.ceil(ctx.values.loss_of_removing([r["ref"]])) + 1
            if not dealer or not math.isfinite(floor):
                ctx.log("arb", "kept", ref=r["ref"], why="no free dealer buys it")
                st["resale"] = None
                return
            asset = max(x["id"] for x in copies)
            hi = max(floor + 10, math.ceil(floor * 1.8))
            try:
                th = ctx.api.open_thread(dealer, topic={"sell": {"assets": [asset]}})
                ctx.api.say(th["id"], f"Buenas. {r['ref']}, a fine copy. {hi} primas.", price=hi)
            except BazaarError as e:
                ctx.log("arb", "kept", ref=r["ref"], why=str(e)[:120])
                st["resale"] = None
                return
            r.update(phase="sell", dealer=dealer, thread=th["id"], asset=asset, floor=floor, price=hi, rounds=0, last_tick=tick)
            ctx.log("arb", "resale_start", ref=r["ref"], dealer=dealer, ask=hi, floor=floor)
            return
        r["last_tick"] = tick
        try:
            th = ctx.api.thread(r["thread"])
        except BazaarError:
            return
        if th.get("status") != "open":
            ctx.log("arb", "resold" if th.get("status") == "deal" else "kept", ref=r["ref"], dealer=r["dealer"],
                    price=r["price"], status=th.get("status"))
            st["resale"] = None
            return
        theirs = [o for o in th.get("standing_offers", []) if o.get("maker") == r["dealer"] and o.get("status") == "open"]
        o = theirs[-1] if theirs else None
        if o:
            g, w = o.get("give") or {}, o.get("want") or {}
            wants = {a.get("id") if isinstance(a, dict) else a for a in w.get("assets") or []}
            pays = g.get("cash") if not (g.get("assets") or g.get("types")) else None
            if pays is not None and pays >= r["floor"] and wants == {r["asset"]} and not w.get("cash") and ctx.take_accept():
                try:
                    ctx.api.accept(o["id"])
                    ctx.log("arb", "resold", ref=r["ref"], dealer=r["dealer"], price=pays, floor=r["floor"])
                    st["resale"] = None
                    return
                except BazaarError as e:
                    ctx.log("arb", "resale_accept_refused", error=str(e)[:120])
        if r["rounds"] >= RESALE_ROUNDS or r["price"] <= r["floor"]:
            try:
                ctx.api.close_thread(r["thread"])
            except BazaarError:
                pass
            ctx.log("arb", "kept", ref=r["ref"], dealer=r["dealer"], why="dealer will not pay our value")
            st["resale"] = None
            return
        r["price"] = max(r["floor"], r["price"] - max(1, math.ceil((r["price"] - r["floor"]) / 2)))
        r["rounds"] += 1
        try:
            ctx.api.say(r["thread"], f"I can let it go for {r['price']}.", price=r["price"])
        except BazaarError:
            pass

    def _abort(self, st, a):
        try:
            self.ctx.api.close_thread(a["thread"])
        except BazaarError:
            pass
        self.ctx.log("arb", "gave_up", ref=a["ref"], dealer=a["dealer"], last_price=a["price"])
        st["active"] = None
