"""Concierge: when someone posts a single-card offer on our market and nobody there takes the other side, ask the
teams most likely to: the holders of the card for a bid, the collectors of its set for an ask. Our market scores
when two other teams gain on it, and the offer is already on our book, so one accept by the second team settles it.

Every SCAN_EVERY ticks, from our market's public book and the public feed (agent/ledger.py holdings and
agent/team_intel.py set leans; the feed's offer.listed events say who made each offer, so we never write to the maker):
  - a bid "X for P": up to MAX_PER_OFFER teams that hold X, a spare copy or a dumped set first;
  - an ask "X for P": up to MAX_PER_OFFER teams that collect X's set and are not seen holding X, those bidding for X
    on another market first.
First of all, a team whose own offer elsewhere already crosses ours: the maker of an ask for X on another market at or
below our bid's price, the maker of a bid for X elsewhere at or above our ask's price. They want this exact trade, and
on our market it is better for them (more cash for the seller, less and no fee for the buyer). Cards on an open
auction lot (auctions.py) are left to their auction.
One short thread message each, with the price and the offer id to accept; the maker is never named. Each team hears
about each offer once; at most MAX_OPEN threads open (dealer agents need the rest of the team's six), closed after
CLOSE_AFTER ticks; one message per tick. Off unless enable_concierge = 1.
"""
from __future__ import annotations

import json
import os
import sys

from bazaar_sdk import BazaarError

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "agent"))
FEED_STORE = os.path.join(HERE, "logs", "feed_events.jsonl")
SCAN_EVERY = 4
MAX_PER_OFFER = 3
MAX_OPEN = 2
CLOSE_AFTER = 6

BID_TEXT = ("Hi from Team 13's market {vid}. A buyer there offers {p} P for {ref} ({name}). If you can spare one, accept "
            "offer {oid} on {vid} with your copy (POST /api/offers/{oid}/accept with your asset id) and it settles next "
            "tick, 0% fee. The buyer stays anonymous.")
CROSS_BID_TEXT = ("Hi from Team 13's market {vid}. You offer {ref} ({name}) for {their} P on {where}; a buyer on {vid} pays "
                  "{p} P for it right now. Accept offer {oid} on {vid} with your copy (POST /api/offers/{oid}/accept with "
                  "your asset id; if that copy is tied to your listing {their_id}, cancel it first) and it settles next "
                  "tick, 0% fee. The buyer stays anonymous.")
CROSS_ASK_TEXT = ("Hi from Team 13's market {vid}. You bid {their} P for {ref} ({name}) on {where}; a seller on {vid} asks "
                  "{p} P for it right now, 0% fee. Accept offer {oid} on {vid} (POST /api/offers/{oid}/accept) and it settles "
                  "next tick. The seller stays anonymous.")
ASK_TEXT = ("Hi from Team 13's market {vid}. A seller there offers {ref} ({name}) for {p} P. If you want it, accept offer "
            "{oid} on {vid} (POST /api/offers/{oid}/accept) and it settles next tick, 0% fee. The seller stays anonymous.")


def team_id(x) -> bool:
    return bool(x) and str(x)[:1] == "t" and str(x)[1:].isdigit()


def _refs(side: dict) -> list:
    out = [a["ref"] for a in side.get("assets") or [] if isinstance(a, dict)]
    return out + [x[5:] for x in side.get("types") or [] if x.startswith("card:")]


def plan(book: list, events: list, boards: dict, me: str, ours: str, hold: dict, leans: dict, skip=()) -> list:
    """[(offer id, side, ref, price, [teams to ask, best first], {team: (market, their price)})] for single-card
    offers on our market with no counterparty there. The dict holds the teams whose own offer elsewhere crosses ours
    (they come first); `skip` holds cards left alone (an open auction lot)."""
    makers = {}
    for e in events:
        if e.get("type") == "offer.listed":
            o = (e.get("payload") or {}).get("offer") or {}
            if o.get("id"):
                makers[o["id"]] = o.get("maker")
    bids, asks = {}, {}
    for o in book:
        if o.get("to"):
            continue
        g, w = o.get("give") or {}, o.get("want") or {}
        gave, wanted = _refs(g), _refs(w)
        if len(wanted) == 1 and not gave and g.get("cash"):
            bids.setdefault(wanted[0], []).append(o)
        elif len(gave) == 1 and not wanted and w.get("cash"):
            asks.setdefault(gave[0], []).append(o)
    elsewhere = {}  # ref -> teams bidding for it on another market (the strongest sign of a buyer)
    bid_at, ask_at = {}, {}  # ref -> {team: (market, their best price)} for single-card cash offers elsewhere
    for vid, offers in boards.items():
        if vid == ours:
            continue
        for o in offers:
            if o.get("to"):
                continue
            g, w = o.get("give") or {}, o.get("want") or {}
            wanted, gave, maker = _refs(w), _refs(g), makers.get(o.get("id"))
            if not team_id(maker) or maker == me:
                continue
            if len(wanted) == 1 and not gave and g.get("cash"):
                elsewhere.setdefault(wanted[0], set()).add(maker)
                cur = bid_at.setdefault(wanted[0], {}).get(maker)
                if cur is None or g["cash"] > cur[1]:
                    bid_at[wanted[0]][maker] = (vid, g["cash"], o.get("id"))
            elif len(gave) == 1 and not wanted and w.get("cash"):
                cur = ask_at.setdefault(gave[0], {}).get(maker)
                if cur is None or w["cash"] < cur[1]:
                    ask_at[gave[0]][maker] = (vid, w["cash"], o.get("id"))
    out = []
    for ref, os_ in bids.items():
        if ref in asks or ref in skip:
            continue  # both sides already on our book: the broker matches them; or an auction lot runs
        for o in sorted(os_, key=lambda x: -(x["give"].get("cash") or 0))[:1]:
            maker = makers.get(o.get("id"))
            p = o["give"]["cash"]
            cross = {t: x for t, x in (ask_at.get(ref) or {}).items() if t not in (me, maker) and x[1] <= p}
            ranked = [(-1, x[1], t) for t, x in cross.items()]
            for team, cards in hold.items():
                c = cards.get(ref)
                if not team_id(team) or team in (me, maker) or not c or c[0] <= 0 or team in cross:
                    continue
                lean = leans.get(team, {}).get(ref[:3], 0)
                ranked.append(((0 if c[0] >= 2 else 1 if lean < 0 else 2), lean, team))
            teams = [t for *_, t in sorted(ranked)][:MAX_PER_OFFER]
            if teams:
                out.append((o["id"], "bid", ref, p, teams, {t: cross[t] for t in teams if t in cross}))
    for ref, os_ in asks.items():
        if ref in bids or ref in skip:
            continue
        for o in sorted(os_, key=lambda x: x["want"].get("cash") or 0)[:1]:
            maker = makers.get(o.get("id"))
            p = o["want"]["cash"]
            cross = {t: x for t, x in (bid_at.get(ref) or {}).items() if t not in (me, maker) and x[1] >= p}
            ranked = [(-1, -x[1], t) for t, x in cross.items()]
            for team, sets in leans.items():
                if not team_id(team) or team in (me, maker) or team in cross or hold.get(team, {}).get(ref, [0])[0] > 0:
                    continue
                lean = sets.get(ref[:3], 0)
                if team in elsewhere.get(ref, ()):
                    ranked.append((0, -lean, team))
                elif lean > 0:
                    ranked.append((1, -lean, team))
            teams = [t for *_, t in sorted(ranked)][:MAX_PER_OFFER]
            if teams:
                out.append((o["id"], "ask", ref, p, teams, {t: cross[t] for t in teams if t in cross}))
    return out


class Concierge:
    def __init__(self, ctx):
        self.ctx = ctx

    def _venue(self):
        if self.ctx.state.get("venue"):
            return self.ctx.state["venue"]
        try:
            for v in self.ctx.public_get("/api/venues").get("venues", []):
                if v.get("owner") == self.ctx.me.get("id") and v.get("status") == "open":
                    return v["venue"]
        except Exception:
            pass
        return None

    def step(self):
        ctx = self.ctx
        if not ctx.S.get("enable_concierge", 0):
            return
        st = ctx.state.setdefault("concierge", {"queue": [], "sent": {}, "open": {}, "scan": -999})
        tick = ctx.clock.get("tick", 0)
        for tid, opened in list(st["open"].items()):
            if tick - opened >= CLOSE_AFTER:
                try:
                    ctx.api.close_thread(int(tid))
                except BazaarError:
                    pass
                st["open"].pop(tid)
        if tick - st["scan"] >= SCAN_EVERY:
            st["scan"] = tick
            self._scan(st)
        if st["queue"] and len(st["open"]) < MAX_OPEN:
            team, text, key = st["queue"].pop(0)
            try:
                th = ctx.api.open_thread(team, venue="rastro")
                ctx.api.say(th["id"], text)
                st["open"][str(th["id"])] = tick
                st["sent"][key] = tick
                ctx.log("concierge", "asked", team=team, key=key, thread=th["id"])
            except BazaarError as e:
                ctx.log("concierge", "ask_refused", team=team, key=key, error=str(e)[:160])
                if "too_many_threads" in str(e):
                    st["queue"].insert(0, (team, text, key))  # retry when a thread frees up

    def _scan(self, st):
        ctx = self.ctx
        vid = self._venue()
        if not vid:
            return
        try:
            import ledger
            from team_intel import lean
            book = ctx.public_get(f"/api/venues/{vid}/offers").get("offers", [])
            if not book:
                return
            with open(FEED_STORE) as f:
                events = [json.loads(line) for line in f if line.strip()]
            hold, leans = ledger.build(events, {"teams": getattr(ctx, "leaderboard", []) or []}), lean(events)
            venues = [v for v in ctx.public_get("/api/venues").get("venues", []) if v.get("status") == "open"]
            boards = {v["venue"]: ctx.public_get(f"/api/venues/{v['venue']}/offers").get("offers", []) for v in venues}
        except Exception as e:
            ctx.log("concierge", "scan_failed", error=repr(e)[:160])
            return
        names = {c: (v or {}).get("name", c) for c, v in (getattr(ctx.values, "cards", {}) or {}).items()} if ctx.values else {}
        vnames = {x["venue"]: ("El Rastro" if x["venue"] == "rastro" else x.get("name") or x["venue"]) for x in venues}
        try:
            import auctions
            lots = {l["ref"] for l in auctions.load().values() if l.get("status") == "open"}
        except Exception:
            lots = set()
        live = {o.get("id") for o in book}
        st["sent"] = {k: t for k, t in st["sent"].items() if int(k.split(":")[1]) in live}  # forget offers that are gone
        st["queue"] = [q for q in st["queue"] if int(q[2].split(":")[1]) in live]
        for oid, side, ref, price, teams, cross in plan(book, events, boards, ctx.me.get("id"), vid, hold, leans, skip=lots):
            for team in teams:
                key = f"{team}:{oid}"
                if key in st["sent"] or any(q[2] == key for q in st["queue"]):
                    continue
                if team in cross:
                    where, their, their_id = cross[team]
                    text = (CROSS_BID_TEXT if side == "bid" else CROSS_ASK_TEXT).format(
                        vid=vid, p=price, ref=ref, name=names.get(ref, ref), oid=oid, their=their, their_id=their_id,
                        where=vnames.get(where, where))
                    # a crossing maker goes to the front of the queue: it wants this exact trade
                    st["queue"].insert(0, (team, text, key))
                else:
                    text = (BID_TEXT if side == "bid" else ASK_TEXT).format(vid=vid, p=price, ref=ref, name=names.get(ref, ref), oid=oid)
                    st["queue"].append((team, text, key))
                ctx.log("concierge", "planned", side=side, ref=ref, price=price, offer=oid, team=team, crossing=team in cross)
