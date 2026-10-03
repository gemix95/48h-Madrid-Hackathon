"""Matchmaker: finds the missing card for other teams and brings both sides to our market, without telling either
who the other is. Our market scores when two other teams gain on it (organisers, Saturday 20:29: "it finds the
missing card: take want-lists and match them with the teams holding duplicates").

Every MATCH_EVERY ticks, from the public feed (agent/ledger.py holdings, agent/team_intel.py set leans) and the open
boards (makers resolved through offer.listed events):
  - a buyer: a team with an open bid for one card, on a market that is not ours;
  - a seller: another team with a spare copy (x2+) or that dumps the set, or that asks for the card elsewhere.
Then, at most one invitation per tick, each (team, card) at most once per INVITE_COOLDOWN ticks:
  - to the seller: a buyer on our market pays about P for that card; list it on our market and the broker matches it;
  - to the buyer: a seller with a spare is invited; post the same bid on our market and it is matched at once.
Neither message names the other team. A short public announcement lists the wanted cards, without names.
We never trade ourselves (we cannot on our own market) and never put a price in a structured offer.
Off unless enable_matchmaker = 1 (server Strategy tab), so laptops running the same code never spam invitations.
"""
from __future__ import annotations

import collections
import json
import os
import sys

from bazaar_sdk import BazaarError, Broker

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "agent"))
FEED_STORE = os.path.join(HERE, "logs", "feed_events.jsonl")
MATCH_EVERY = 20        # ticks between two match scans
INVITE_COOLDOWN = 120   # ticks before the same team hears about the same card again
CLOSE_AFTER = 6         # ticks an invitation thread stays open (their agent reads it), then we close it
MAX_PER_SCAN = 4        # invitations queued per scan (one goes out per tick)
ANNOUNCE_MAX = 240

SELLER_TEXT = ("Hi from Team 13's market {venue} (0% fee, nothing per card). A buyer on our market is looking for {ref} "
               "and pays about {p} P. You seem to have a spare: list one on {venue} at {p} P and our broker matches it "
               "the same tick. Buyer and seller stay anonymous to each other.")
BUYER_TEXT = ("Hi from Team 13's market {venue} (0% fee, nothing per card). We have invited a team with a spare {ref} "
              "to list it on {venue}. Post your bid for {ref} there too (about {p} P) and our broker matches it the "
              "same tick. Buyer and seller stay anonymous to each other.")
ANNOUNCE = "Matchmaking on {venue}, 0% fee: buyers are waiting for {cards}. Got a spare? List it on {venue} and our broker matches it the same tick."


def team_id(x) -> bool:
    return bool(x) and str(x)[:1] == "t" and str(x)[1:].isdigit()


def find_matches(events: list, boards: dict, me: str, ours: str, hold: dict, leans: dict) -> list:
    """[(ref, buyer, bid, seller, why)] best first: a bid elsewhere against an ask or a spare of another team."""
    maker = {}
    for e in events:
        if e.get("type") == "offer.listed":
            o = (e.get("payload") or {}).get("offer") or {}
            if o.get("id"):
                maker[o["id"]] = o.get("maker")
    asks, bids = collections.defaultdict(list), collections.defaultdict(list)
    for venue, offers in boards.items():
        for o in offers:
            t = maker.get(o.get("id"))
            if not team_id(t) or t == me or o.get("to"):
                continue
            g, w = o.get("give") or {}, o.get("want") or {}
            gave = [a["ref"] for a in g.get("assets") or [] if isinstance(a, dict)]
            wanted = [x[5:] for x in w.get("types") or [] if x.startswith("card:")]
            wanted += [a["ref"] for a in w.get("assets") or [] if isinstance(a, dict)]
            if len(gave) == 1 and not wanted and w.get("cash"):
                asks[gave[0]].append((w["cash"], t, venue))
            elif len(wanted) == 1 and not gave and g.get("cash") and venue != ours:
                bids[wanted[0]].append((g["cash"], t, venue))
    out = []
    for ref, bl in bids.items():
        bid, buyer, _ = max(bl)
        cands = [(0, a, f"asks {p} P elsewhere") for p, a, _v in sorted(asks.get(ref, [])) if a != buyer]
        for team, cards in hold.items():
            c = cards.get(ref)
            if team in (buyer, me) or not c or c[0] <= 0 or any(x[1] == team for x in cands):
                continue
            lean = leans.get(team, {}).get(ref[:3], 0)
            if c[0] >= 2 or lean < 0:
                cands.append((1 if c[0] >= 2 else 2, team, f"x{c[0]}, lean {lean:+d}"))
        if cands:
            _, seller, why = sorted(cands)[0]
            out.append((ref, buyer, bid, seller, why))
    return sorted(out, key=lambda m: -m[2])


class Matchmaker:
    def __init__(self, ctx):
        self.ctx = ctx

    def _venue(self):
        st = self.ctx.state
        if st.get("venue"):
            return st["venue"]
        for v in getattr(self.ctx, "venues", None) or []:
            if v.get("owner") == self.ctx.me.get("id") and v.get("status") == "open":
                return v["venue"]
        return None

    def _broker_key(self):
        if self.ctx.state.get("broker_key"):
            return self.ctx.state["broker_key"]
        try:  # the dealers role keeps its own state file: the venue's key lives in the main one
            return json.load(open(os.path.join(HERE, "state.json"))).get("broker_key")
        except (OSError, ValueError):
            return None

    def step(self):
        ctx = self.ctx
        if not ctx.S.get("enable_matchmaker", 0):
            return
        st, tick = ctx.state.setdefault("matchmaker", {"queue": [], "sent": {}, "open": {}, "scan": -999}), ctx.clock.get("tick", 0)
        venue = self._venue()
        if not venue:
            return
        for tid, opened in list(st["open"].items()):  # close invitation threads after a few ticks
            if tick - opened >= CLOSE_AFTER:
                try:
                    ctx.api.close_thread(int(tid))
                except BazaarError:
                    pass
                st["open"].pop(tid)
        if tick - st["scan"] >= MATCH_EVERY:
            st["scan"] = tick
            self._scan(st, tick, venue)
        if st["queue"] and len(st["open"]) < 2:
            team, text, key = st["queue"].pop(0)
            try:
                th = ctx.api.open_thread(team, venue="rastro")
                ctx.api.say(th["id"], text)
                st["open"][str(th["id"])] = tick
                st["sent"][key] = tick
                ctx.log("matchmaker", "invited", team=team, key=key, thread=th["id"])
            except BazaarError as e:
                ctx.log("matchmaker", "invite_refused", team=team, key=key, error=str(e)[:160])

    def _scan(self, st, tick, venue):
        ctx = self.ctx
        try:
            import ledger
            from team_intel import lean
            with open(FEED_STORE) as f:
                events = [json.loads(line) for line in f if line.strip()]
            hold, leans = ledger.build(events, {"teams": getattr(ctx, "leaderboard", [])}), lean(events)
        except (OSError, ValueError, ImportError) as e:
            ctx.log("matchmaker", "scan_failed", error=repr(e)[:160])
            return
        boards = dict(getattr(ctx, "boards", {}) or {})
        if not boards:
            venues = getattr(ctx, "venues", None) or ctx.public_get("/api/venues").get("venues", [])
            for v in [x for x in venues if x.get("status") == "open"]:
                try:
                    boards[v["venue"]] = ctx.public_get(f"/api/venues/{v['venue']}/offers").get("offers", [])
                except Exception:
                    pass
        matches = find_matches(events, boards, ctx.me.get("id"), venue, hold, leans)
        queued = 0
        for ref, buyer, bid, seller, why in matches:
            if queued >= MAX_PER_SCAN:
                break
            for team, text, role in ((seller, SELLER_TEXT, "seller"), (buyer, BUYER_TEXT, "buyer")):
                key = f"{team}:{ref}"
                if tick - st["sent"].get(key, -10 ** 6) < INVITE_COOLDOWN or any(q[2] == key for q in st["queue"]):
                    continue
                st["queue"].append((team, text.format(venue=venue, ref=ref, p=bid), key))
            queued += 1
            ctx.log("matchmaker", "match", ref=ref, bid=bid, why=why)  # who is who stays in our log only
        if matches and tick - st.get("announced", -999) >= MATCH_EVERY:
            cards = ", ".join(sorted({m[0] for m in matches})[:6])
            text = ANNOUNCE.format(venue=venue, cards=cards)[:ANNOUNCE_MAX]
            key = self._broker_key()
            if key:
                try:
                    Broker(ctx.raw.url, key).announce(text)
                    st["announced"] = tick
                    ctx.log("matchmaker", "announced", text=text)
                except BazaarError as e:
                    st["announced"] = tick
                    ctx.log("matchmaker", "announce_refused", error=str(e)[:160])
