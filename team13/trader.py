"""Trading with other teams: this scores the value we gain at OUR private values.

- Take offers on the board that gain us value after fees (best first, one accept per tick).
- List our spares (duplicates, low-multiplier sets) at prices that decay toward a floor that still gains us value.
- Bid for the cards we value most (high-multiplier sets, page completers) below what they are worth to us.
- Answer team conversations by reading only the structured offer, never the words.
"""
from __future__ import annotations

import math

from bazaar_sdk import BazaarError

MIN_GAIN = 3.0          # P of private value an accepted trade must create for us
RASTRO_BPS, RASTRO_PER_CARD = 500, 1
MAX_ASKS, MAX_BIDS = 8, 6
BID_SHARE = 0.4         # at most this share of our free cash sits in open bids (the rest is for dealer deals)
REPRICE_TICKS = 8


def fee(price: float, n_cards: int, bps=RASTRO_BPS, per_card=RASTRO_PER_CARD) -> float:
    return math.ceil(bps * price / 10000) + per_card * n_cards


def refs_of(side: dict, ctx) -> list:
    """Card refs on one side of an offer, whatever shape the server uses."""
    if not side:
        return []
    out = list(side.get("cards") or [])
    for t in side.get("types") or []:
        out.append(t.split(":", 1)[-1])
    for a in side.get("assets") or []:
        if isinstance(a, dict):
            if a.get("kind", "card") == "card" and a.get("ref"):
                out.append(a["ref"])
        else:
            own = next((x for x in ctx.values.assets if x["id"] == a), None)
            out.append(own["ref"] if own else f"#{a}")
    return out


class Trader:
    def __init__(self, ctx):
        self.ctx = ctx

    # ------------------------------------------------------------------ evaluation
    def fee_at(self, venue, price, n_cards):
        bps, per = self.ctx.venue_fee(venue or "rastro") if hasattr(self.ctx, "venue_fee") else (RASTRO_BPS, RASTRO_PER_CARD)
        return fee(price, n_cards, bps, per)

    def all_offers(self):
        """Other teams' open offers on every market (just El Rastro if 'trade on every market' is off)."""
        boards = getattr(self.ctx, "boards", None) or {"rastro": self.ctx.board}
        if not self.ctx.S.get("trade_all_markets", 1):
            boards = {"rastro": boards.get("rastro", self.ctx.board)}
        for venue, offers in boards.items():
            for o in offers:
                if o.get("maker") != self.ctx.me["id"] and o.get("status", "open") == "open":
                    yield venue, o

    def evaluate(self, offer: dict) -> dict:
        """What accepting `offer` would gain us, at our values, after that market's fees."""
        ctx, v = self.ctx, self.ctx.values
        give, want = offer.get("give") or {}, offer.get("want") or {}
        they_give, they_want = refs_of(give, ctx), refs_of(want, ctx)
        cash_in, cash_out = give.get("cash") or 0, want.get("cash") or 0
        if any(r.startswith("#") for r in they_give) or any(r not in v.cards for r in they_give + they_want):
            return {"gain": None, "why": "unknown items"}
        if len(they_want) > 0 and any(v.held[r] == 0 for r in they_want):
            return {"gain": None, "why": "they want cards we do not hold"}
        if any(self.protected(r) for r in they_want):
            return {"gain": None, "why": "would break a page we are about to complete"}
        f = self.fee_at(offer.get("venue"), cash_in or cash_out, len(they_give) + len(they_want))
        gain = v.gain_of_adding(they_give) - v.loss_of_removing(they_want) + cash_in - cash_out - f
        keep = ctx.reserve()
        if they_give and any(v.gain_of_adding([r]) > v.book(r) * v.m(r) * 1.2 for r in they_give):
            keep = min(keep, ctx.S.get("seek_keep_cash", 100))  # a page completer may use the bond reserve, not below this
        if cash_out + f > ctx.me["cash"] - keep:
            return {"gain": None, "why": "cash reserved"}
        return {"gain": gain, "give": they_give, "want": they_want, "cash_in": cash_in, "cash_out": cash_out, "fee": f}

    def cheapest_rival_ask(self, ref: str):
        """Lowest price another team asks on El Rastro for a single card of the same ref (else same rarity)."""
        v, mine = self.ctx.values, self.ctx.me["id"]
        rar = v.cards.get(ref, {}).get("rarity")
        same, similar = [], []
        for _, o in self.all_offers():
            give, want = o.get("give") or {}, o.get("want") or {}
            assets = give.get("assets") or []
            if o.get("maker") == mine or len(assets) != 1 or not want.get("cash") or give.get("cash"):
                continue
            r = assets[0].get("ref") if isinstance(assets[0], dict) else None
            if r == ref:
                same.append(want["cash"])
            elif r and v.cards.get(r, {}).get("rarity") == rar:
                similar.append(want["cash"])
        pool = same or similar
        if not pool and getattr(self.ctx, "intel", None):  # board empty: what teams listed recently (public feed)
            tick = self.ctx.clock.get("tick", 0)
            for l in self.ctx.intel.summary().get("listings", []):
                g, w = l.get("give") or {}, l.get("want") or {}
                if l.get("maker") != mine and len(g.get("assets") or []) == 1 and w.get("cash") and tick - l["tick"] <= 30:
                    r = g["assets"][0].get("ref")
                    (same if r == ref else similar if v.cards.get(r, {}).get("rarity") == rar else []).append(w["cash"])
            pool = same or similar
            return sorted(pool)[len(pool) // 2] if pool else None  # median of recent listings, not one outlier
        return min(pool) if pool else None

    def assets_for(self, refs: list) -> list:
        """Which of our copies to hand over: the highest serial first (keep low serials, they are nicer)."""
        out, locked = [], self.ctx.locked_assets()
        for r in refs:
            cands = sorted((a for a in self.ctx.values.assets if a["ref"] == r and a["id"] not in locked and a["id"] not in out),
                           key=lambda a: -a.get("serial", 0))
            if not cands:
                return []
            out.append(cands[0]["id"])
        return out

    # ------------------------------------------------------------------ loop
    def step(self):
        self.take_board()
        self.answer_teams()
        self.haggle_with_makers()
        self.maintain_listings()

    def take_board(self):
        ctx = self.ctx
        if not ctx.accepts_left():
            return
        best = None
        for venue, o in self.all_offers():
            ev = self.evaluate(o)
            if ev.get("gain") is not None and ev["gain"] >= ctx.S["trade_min_gain"] and (best is None or ev["gain"] > best[1]["gain"]):
                best = (o, ev)
        if not best:
            return
        o, ev = best
        # double-check with the server's own value of what we receive (page bonus included)
        try:
            exact = sum(ctx.api.value(r)["your_value"] for r in ev["give"])
            if ev["give"] and exact - ev["cash_out"] - ev["fee"] - ctx.values.loss_of_removing(ev["want"]) < ctx.S["trade_min_gain"] - 1:
                ctx.log("trade", "skip_after_check", offer=o["id"], est=ev["gain"], exact=exact)
                return
        except BazaarError:
            pass
        assets = self.assets_for(ev["want"]) if ev["want"] else None
        if ev["want"] and not assets:
            return
        if ctx.take_accept():
            try:
                ctx.api.accept(o["id"], assets=assets)
                ctx.log("trade", "accept", offer=o["id"], venue=o.get("venue"), gain=round(ev["gain"], 1), detail=ev)
            except BazaarError as e:
                ctx.log("trade", "accept_refused", offer=o["id"], error=str(e))

    def answer_teams(self):
        ctx = self.ctx
        for th in ctx.threads:
            if th.get("kind") == "persona" or th["status"] != "open":
                continue
            if str(th["id"]) not in ctx.state.get("team_threads_ours", []) and any(
                    m.get("sender") == ctx.me["id"] for m in th.get("messages", [])) and not ctx.S.get("adopt_threads", 0):
                continue  # a teammate is already talking in this conversation
            if th.get("venue") and th.get("venue") == ctx.state.get("venue"):
                continue  # our own market: we may not trade there (self_venue); market.py handles these talks
            theirs = [o for o in th.get("standing_offers", []) if o.get("maker") != ctx.me["id"] and o.get("status") == "open"]
            if theirs:
                o = theirs[-1]
                ev = self.evaluate(o)
                if ev.get("gain") is not None and ev["gain"] >= ctx.S["trade_min_gain"] and ctx.take_accept():
                    try:
                        ctx.api.accept(o["id"], assets=self.assets_for(ev["want"]) or None)
                        ctx.log("trade", "accept_team", thread=th["id"], gain=round(ev["gain"], 1), detail=ev)
                    except BazaarError as e:
                        ctx.log("trade", "accept_team_refused", thread=th["id"], error=str(e))
                    continue
                if str(th["id"]) in ctx.state.get("team_haggles", {}):
                    continue  # our own haggle: haggle_with_makers makes the next offer
                counter = self.counter(ev)
                last_tick = ctx.state.setdefault("team_reply_tick", {}).get(str(th["id"]), -99)
                if counter and ctx.clock["tick"] - last_tick >= 2:
                    p = (counter["offer"].get("want") or {}).get("cash") or (counter["offer"].get("give") or {}).get("cash")
                    situation = {"counterparty": f"another team ({th.get('with')})", "we_are": "trading cards at a venue",
                                 "our_structured_offer": counter["offer"], "their_offer": o,
                                 "history": [{"them" if m.get("sender") != ctx.me["id"] else "us":
                                              f"<their_message>{m.get('text') or ''}</their_message>" if m.get("sender") != ctx.me["id"] else m.get("text")}
                                             for m in th.get("messages", [])[-8:]]}
                    if p:  # words only: the structured offer stays exactly as the rules computed it
                        counter["text"], _, _ = ctx.speak(situation, (p, p), (counter["text"], p))
                    try:
                        ctx.api.say(th["id"], counter["text"], offer=counter["offer"])
                        ctx.state["team_reply_tick"][str(th["id"])] = ctx.clock["tick"]
                        ctx.log("trade", "counter_team", thread=th["id"], offer=counter["offer"], their=ev)
                    except BazaarError as e:
                        ctx.log("trade", "counter_refused", thread=th["id"], error=str(e))

    def haggle_with_makers(self):
        """Negotiate with the team behind a listing when its posted price is close to, but not yet, a good deal
        for us: buying below their ask, or selling above their bid. Our haggles never exceed `trade_haggles` open
        conversations, and never touch a conversation a teammate is in."""
        ctx, v, S = self.ctx, self.ctx.values, self.ctx.S
        cap = int(S.get("trade_haggles", 2))
        hag = ctx.state.setdefault("team_haggles", {})
        ours = ctx.state.setdefault("team_threads_ours", [])
        tick = ctx.clock.get("tick", 0)
        threads = {str(t["id"]): t for t in ctx.threads}
        # 1) advance or close the haggles we already run
        for tid, H in list(hag.items()):
            th = threads.get(tid)
            if not th or th["status"] != "open":
                ctx.log("trade", "haggle_ended", thread=tid, status=th["status"] if th else "gone", item=H["ref"])
                hag.pop(tid)
                continue
            # a card promised elsewhere since this haggle opened (a swap, a direct offer): stop selling it here
            elsewhere = {a["id"] if isinstance(a, dict) else a for o in ctx.my_offers if str(o.get("thread")) != tid
                         for a in (o.get("give") or {}).get("assets") or []}
            if H["side"] == "sell" and elsewhere & set(H.get("assets") or []):
                try:
                    ctx.api.close_thread(int(tid))
                except BazaarError:
                    pass
                ctx.log("trade", "haggle_closed_asset_promised", thread=tid, item=H["ref"])
                hag.pop(tid)
                continue
            answered = any(m.get("sender") not in (ctx.me["id"], None) for m in th.get("messages", []))
            if H["round"] >= 5 or (not answered and tick - H["opened"] >= 8):
                try:
                    ctx.api.close_thread(int(tid))
                except BazaarError:
                    pass
                ctx.log("trade", "haggle_closed", thread=tid, rounds=H["round"], answered=answered)
                hag.pop(tid)
                continue
            if tick - H["tick"] >= 2:
                self._haggle_say(th, H)
        # 2) open new haggles on the most promising listings
        if cap <= len(hag) or len([t for t in ctx.threads if t["status"] == "open"]) >= ctx.limit("max_open_threads_per_team", 6) - 1:
            return
        busy = {(H["maker"], H["ref"]) for H in hag.values()}
        tried = ctx.state.setdefault("haggle_tried", {})
        cands = []
        for venue, o in self.all_offers():
            give, want = o.get("give") or {}, o.get("want") or {}
            maker = o.get("maker")
            # boards show makers as pseudonyms; the public feed tells us which team listed each offer
            if getattr(ctx, "intel", None):
                maker = ctx.intel.summary().get("offer_maker", {}).get(o.get("id"), maker)
            if not maker or not (maker[0] == "t" and maker[1:].isdigit()) or maker == ctx.me["id"] or venue == ctx.state.get("venue"):
                continue
            g_refs, w_refs = refs_of(give, ctx), refs_of(want, ctx)
            if len(g_refs) == 1 and want.get("cash") and not w_refs and g_refs[0] in v.cards:   # they sell one card
                ref, ask = g_refs[0], want["cash"]
                value = v.gain_of_adding([ref])
                bps, per = ctx.venue_fee(venue)
                p_max = math.floor((value - S["trade_min_gain"] - per) / (1 + bps / 10000))
                free = ctx.me["cash"] - ctx.reserve() - sum(L["price"] for L in ctx.state.get("listings", {}).values() if L["kind"] == "bid")
                p_max = min(p_max, math.floor(free / (1 + bps / 10000)) - per)  # never promise cash we keep back
                if 1 <= p_max < ask and p_max >= 0.5 * ask:
                    cands.append((value - ask, "buy", venue, maker, ref, ask, p_max, o))
            elif len(w_refs) == 1 and give.get("cash") and not g_refs and v.held[w_refs[0]] > 0 and not self.protected(w_refs[0]):  # they buy one card
                ref, bid = w_refs[0], give["cash"]
                loss = v.loss_of_removing([ref])
                bps, per = ctx.venue_fee(venue)
                p_min = math.ceil(loss + S["trade_min_gain"] + per + bps * bid / 10000)
                if bid < p_min <= 2 * bid:
                    cands.append((bid - loss, "sell", venue, maker, ref, bid, p_min, o))
        if S.get("trade_seek_needed", 1):
            cands += self.seek_candidates(busy, tried, tick)  # ranked with everything else: big page completers win
        for score, side, venue, maker, ref, posted, limit, o in sorted(cands, key=lambda c: -c[0]):
            if (maker, ref) in busy or tried.get(f"{maker}:{ref}", -99) > tick - 30:
                continue
            assets = self.assets_for([ref]) if side == "sell" else []
            if side == "sell" and not assets:
                continue
            try:
                th = ctx.api.open_thread(maker, venue=venue)
            except BazaarError as e:
                ctx.log("trade", "haggle_open_refused", maker=maker, venue=venue, error=str(e)[:160])
                tried[f"{maker}:{ref}"] = tick
                continue
            tid = str(th["id"])
            if o is None:  # asking a holder for a card they did not list: start just above what they paid
                first = min(limit, max(1, math.ceil(posted * 1.05)))
            else:
                first = (min(limit, math.floor(posted * 0.75)) if side == "buy" else max(limit, math.ceil(posted * 1.35)))
            hag[tid] = {"side": side, "venue": venue, "maker": maker, "ref": ref, "posted": posted, "limit": limit,
                        "price": first, "round": 0, "opened": tick, "tick": -99, "assets": assets}
            ours.append(tid)
            tried[f"{maker}:{ref}"] = tick
            ctx.log("trade", "haggle_opened", thread=tid, side=side, ref=ref, venue=venue, maker=maker, posted=posted, our_limit=limit,
                    seek=o is None)
            self._haggle_say({"id": int(tid), "messages": []}, hag[tid])
            break  # one new conversation per tick

    def protected(self, ref) -> bool:
        """Our only copy of a card from a page that is 8/10 or more: never sell it, it is worth a page bonus soon."""
        v = self.ctx.values
        if v.held[ref] > 1 or ref not in v.cards or not v.cards[ref].get("page"):
            return False
        sid = v.cards[ref]["set"]
        page = v.page_cards(sid)
        return sum(1 for r in page if v.held[r] > 0) >= len(page) - 2

    def seek_candidates(self, busy, tried, tick):
        """Cards we need that nobody lists: ask a team the public feed shows recently received or listed one.
        Page completers may use the market-bond reserve (never below seek_keep_cash); everything else may not."""
        ctx, v, S = self.ctx, self.ctx.values, self.ctx.S
        if not getattr(ctx, "intel", None):
            return []
        summ = ctx.intel.summary()
        holders = {}
        for l in summ.get("listings", []):
            for a in (l.get("give") or {}).get("assets") or []:
                if l.get("maker"):
                    holders.setdefault(a.get("ref"), {})[l["maker"]] = (l["tick"], (l.get("want") or {}).get("cash"))
        for e in ctx.intel.events.values():
            if e.get("type") == "settlement":
                for it in (e.get("payload") or {}).get("items", []):
                    to = it.get("to") or ""
                    if it.get("kind") == "card" and to[:1] == "t" and to[1:].isdigit():
                        holders.setdefault(it["ref"], {})[to] = (e["tick"], e["payload"].get("price"))
        out = []
        for ref, gain in v.wishlist(limit=15):
            base = v.book(ref) * v.m(ref)
            completer = gain > base * 1.2
            if not (completer or gain >= 60):
                continue
            keep = S.get("seek_keep_cash", 100) if completer else ctx.reserve()
            cash_ok = ctx.me["cash"] - keep
            limit = math.floor(min(gain * (0.6 if completer else 0.75), gain - 2 * S["trade_min_gain"], cash_ok))
            for team, (seen, paid) in sorted(holders.get(ref, {}).items(), key=lambda x: -x[1][0]):
                if team == ctx.me["id"] or (team, ref) in busy or tried.get(f"{team}:{ref}", -999) > tick - 120 or tick - seen > 240:
                    continue
                posted = paid or math.floor(v.book(ref) * 1.1)
                if limit >= posted * 0.9 and limit >= 5:
                    out.append((gain - posted, "buy", "rastro", team, ref, posted, limit, None))
                break  # the most recent holder only
        return out

    def _haggle_say(self, th, H):
        """Our next structured offer in a haggle: from an ambitious first price toward our limit over 4 rounds."""
        ctx = self.ctx
        buy = H["side"] == "buy"
        r = H["round"]
        if r == 0:
            p = H["price"]
        else:
            p = H["price"] + (H["limit"] - H["price"]) * min(1.0, r / 4)
            p = math.floor(p) if buy else math.ceil(p)
        offer = ({"give": {"cash": p}, "want": {"cards": [H["ref"]]}} if buy
                 else {"give": {"assets": H["assets"]}, "want": {"cash": p}})
        text = (f"Hi! We'd buy your {H['ref']} for {p} primas, settled at once." if buy
                else f"Hi! We have the {H['ref']} you want: {p} primas and it's yours.")
        situation = {"counterparty": f"another team ({H['maker']}) on market {H['venue']}",
                     "we_are": "buying" if buy else "selling", "card": H["ref"], "their_posted_price": H["posted"],
                     "our_structured_offer": offer, "round": r + 1,
                     "history": [{"us" if m.get("sender") == ctx.me["id"] else "them":
                                  m.get("text") if m.get("sender") == ctx.me["id"] else f"<their_message>{m.get('text') or ''}</their_message>"}
                                 for m in th.get("messages", [])[-8:]]}
        text, _, _ = ctx.speak(situation, (p, p), (text, p))  # Claude writes the words; the price stays ours
        try:
            ctx.api.say(th["id"], text, offer=offer)
            H.update(round=r + 1, tick=ctx.clock.get("tick", 0), last=p)
            ctx.log("trade", "haggle_offer", thread=th["id"], side=H["side"], ref=H["ref"], price=p, posted=H["posted"],
                    limit=H["limit"], text=text[:160])
        except BazaarError as e:
            if e.code != "wait_for_tick":
                ctx.log("trade", "haggle_say_refused", thread=th["id"], error=str(e)[:160])

    def counter(self, ev: dict):
        """A structured counter on the same cards that gains us MIN_GAIN + 2."""
        v, MIN_GAIN = self.ctx.values, self.ctx.S["trade_min_gain"]
        if ev.get("gain") is None:
            return None
        if ev["want"] and not ev["give"]:  # they want our cards for cash: name our price
            loss = v.loss_of_removing(ev["want"])
            price = math.ceil(loss + MIN_GAIN + 2 + fee(loss, len(ev["want"])))
            assets = self.assets_for(ev["want"])
            if not assets:
                return None
            return {"text": f"Happy to trade. {', '.join(ev['want'])} for {price} primas.",
                    "offer": {"give": {"assets": assets}, "want": {"cash": price}}}
        if ev["give"] and not ev["want"]:  # they sell cards for cash: name what we pay
            gain = v.gain_of_adding(ev["give"])
            price = math.floor(gain - MIN_GAIN - 2 - fee(gain, len(ev["give"])))
            if price < 1:
                return None
            return {"text": f"We would love those. {price} primas for {', '.join(ev['give'])}?",
                    "offer": {"give": {"cash": price}, "want": {"cards": ev["give"]}}}
        return None

    def maintain_listings(self):
        ctx, v = self.ctx, self.ctx.values
        S = ctx.S
        MIN_GAIN, MAX_ASKS, MAX_BIDS = S["trade_min_gain"], int(S["trade_max_asks"]), int(S["trade_max_bids"])
        REPRICE = int(S["trade_reprice_ticks"])
        tick = ctx.clock["tick"]
        listed = ctx.state.setdefault("listings", {})  # offer id -> {"kind", "ref", "price", "tick", "start"}
        mine = {str(o["id"]): o for o in ctx.my_offers if o.get("status", "open") == "open"}
        for oid in list(listed):
            if oid not in mine:
                ctx.log("trade", "listing_gone", offer=oid, info=listed.pop(oid))
        budget = ctx.limit("offers_per_team_per_tick", 12) - 1
        open_total = len(mine)

        # drop bids that stopped paying: our holdings change under them (a dealer deal, a pack, a trade)
        for oid, L in list(listed.items()):
            if L["kind"] != "bid":
                continue
            gain = v.gain_of_adding([L["ref"]])
            if gain - L["price"] - fee(L["price"], 1) < MIN_GAIN:
                try:
                    ctx.api.cancel(int(oid))
                    listed.pop(oid)
                    open_total -= 1
                    ctx.log("trade", "bid_dropped", offer=oid, ref=L["ref"], price=L["price"], our_value=round(gain, 1))
                except BazaarError as e:
                    ctx.log("trade", "cancel_refused", offer=oid, error=str(e))
        # the same for every other cash-for-one-card bid we have open (thread counters, hand-placed bids): drop it
        # once the card is worth less to us than we would pay (as maker we pay no fee, the accepting side does)
        for oid, o in list(mine.items()):
            give, want = o.get("give") or {}, o.get("want") or {}
            types = want.get("types") or []
            if any(not t.startswith("card:") for t in types):
                continue  # packs and other items: not ours to judge here (dealer haggles bid for packs)
            refs = refs_of(want, ctx)
            if oid in listed or o.get("maker") != ctx.me["id"] or not give.get("cash") or give.get("assets") \
                    or want.get("cash") or len(refs) != 1 or refs[0] not in v.cards:
                continue
            gain = v.gain_of_adding(refs)
            if gain < give["cash"]:
                try:
                    ctx.api.cancel(int(oid))
                    mine.pop(oid)
                    open_total -= 1
                    ctx.log("trade", "stale_bid_dropped", offer=oid, ref=refs[0], price=give["cash"], our_value=round(gain, 1))
                except BazaarError as e:
                    ctx.log("trade", "cancel_refused", offer=oid, error=str(e))

        # reprice stale listings toward their floor
        for oid, L in list(listed.items()):
            if budget <= 1 or tick - L["tick"] < REPRICE:
                continue
            new = self._price(L, tick, REPRICE)
            if new == L["price"]:
                continue
            try:
                ctx.api.cancel(int(oid))
                listed.pop(oid)
                budget -= 1
                open_total -= 1
                ctx.log("trade", "reprice_cancel", offer=oid, old=L["price"], new=new, ref=L["ref"])
            except BazaarError as e:
                ctx.log("trade", "cancel_refused", offer=oid, error=str(e))

        markets = self.listing_markets()
        locked = ctx.locked_assets()
        asks = [L for L in listed.values() if L["kind"] == "ask"]
        bids = [L for L in listed.values() if L["kind"] == "bid"]
        # asks: spares, priced from book (what others may value) down to a floor that still gains us MIN_GAIN
        for a in v.spares():
            if len(asks) >= MAX_ASKS or budget <= 0 or open_total >= ctx.limit("max_open_offers_per_team", 30):
                break
            if a["id"] in locked or any(L.get("asset") == a["id"] for L in asks) or self.protected(a["ref"]):
                continue
            loss = v.loss_of_removing([a["ref"]])
            book = v.book(a["ref"])
            venue = markets[len(asks) % min(3, len(markets))]  # rotate over the 3 best markets: more buyers see us
            floor = math.ceil(loss + MIN_GAIN + self.fee_at(venue, book, 1))
            start = max(floor, math.ceil(book * S["trade_ask_start"]))
            rival = self.cheapest_rival_ask(a["ref"])
            if S.get("use_intel", 1) and rival is not None and rival - 1 < start:
                start = max(floor, rival - 1)  # undercut the cheapest competing listing, never below our floor
            L = {"kind": "ask", "ref": a["ref"], "asset": a["id"], "start": start, "floor": floor, "born": tick, "venue": venue}
            L["price"] = self._price(L, tick, REPRICE)
            try:
                o = ctx.api.list_offer({"assets": [a["id"]]}, {"cash": L["price"]}, venue=venue)
                L["tick"] = tick
                listed[str(o.get("id"))] = L
                asks.append(L)
                budget -= 1
                open_total += 1
                ctx.log("trade", "list_ask", ref=a["ref"], price=L["price"], floor=floor, our_value=round(loss, 1), venue=venue)
            except BazaarError as e:
                ctx.log("trade", "list_refused", ref=a["ref"], error=str(e))
                break
        # bids: cards worth a lot to us, offered below that value; total committed stays inside free cash
        free = S["trade_bid_share"] * (ctx.me["cash"] - ctx.reserve()) - sum(L["price"] for L in bids)
        # best value per prima committed first: cheap cards from our high-multiplier sets and page completers
        wants = sorted(v.wishlist(limit=30), key=lambda x: (-round(x[1] / max(1, v.book(x[0])), 2), v.book(x[0])))
        for ref, gain in wants:
            if len(bids) >= MAX_BIDS or budget <= 0 or open_total >= ctx.limit("max_open_offers_per_team", 30):
                break
            if any(L["ref"] == ref for L in bids):
                continue
            book = v.book(ref)
            venue = markets[len(bids) % min(2, len(markets))]
            ceiling = math.floor(gain - MIN_GAIN - self.fee_at(venue, book, 1))
            start = min(ceiling, math.floor(book * S["trade_bid_start"]))
            base = book * v.m(ref)
            if gain > base * 1.2:  # this card completes a page: its bonus makes it worth fighting for
                start = min(ceiling, math.floor(book * max(S["trade_bid_start"], 0.95)))
            if ceiling < 2 or start < 1:
                continue
            L = {"kind": "bid", "ref": ref, "start": start, "floor": min(ceiling, math.floor(book * 1.1)), "born": tick, "venue": venue}
            L["price"] = self._price(L, tick, REPRICE)
            if L["price"] > free:
                continue
            try:
                o = ctx.api.list_offer({"cash": L["price"]}, {"cards": [ref]}, venue=venue)
                L["tick"] = tick
                listed[str(o.get("id"))] = L
                bids.append(L)
                free -= L["price"]
                budget -= 1
                open_total += 1
                ctx.log("trade", "list_bid", ref=ref, price=L["price"], our_value=round(gain, 1), venue=venue)
            except BazaarError as e:
                ctx.log("trade", "bid_refused", ref=ref, error=str(e))
                break

    def listing_markets(self):
        """Markets to list on, best first: busy (trades, traders, open offers) and cheap (fee), never our own,
        and only those whose rules admit us (min level)."""
        ctx = self.ctx
        if not ctx.S.get("trade_all_markets", 1) or not getattr(ctx, "venues", None):
            return ["rastro"]
        scored = []
        for v in ctx.venues:
            if v["venue"] == ctx.state.get("venue") or v.get("status") != "open":
                continue
            if (v.get("rules") or {}).get("min_level", 0) > ctx.me.get("level", 1):
                continue
            activity = 1 + (v.get("trades") or 0) + (v.get("traders") or 0) + 0.5 * len(ctx.boards.get(v["venue"], []))
            net = 1 - (v.get("fee_bps") or 0) / 10000
            scored.append((activity * net, v["venue"]))
        return [vid for _, vid in sorted(scored, reverse=True)] or ["rastro"]

    @staticmethod
    def _price(L, tick, every=REPRICE_TICKS):
        """Walk from the start price toward the floor (asks down, bids up) over ~10 reprices."""
        steps = max(0, (tick - L["born"]) // every)
        x = min(1.0, steps / 10)
        p = L["start"] + (L["floor"] - L["start"]) * x
        return int(math.ceil(p) if L["kind"] == "ask" else math.floor(p))
