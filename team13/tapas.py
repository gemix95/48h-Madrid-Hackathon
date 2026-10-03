"""El Menú: three listings other teams rarely see — último cromo, tapas platters, and public trueque.

Vanilla single-card asks are off (`trade_max_asks`). Common extras stay in reserved.json for hand swaps.
This desk only lists cards `locked_assets()` leaves free (dump first copies, uncommon extras):

  último cromo  private offer of a free spare that would complete another team's page (ledger + lean,
                or they already bid for it). Price takes a slice of the page bonus they would earn.
  tapas         public 2-card platter from one dump neighbourhood, cheaper than buying the two books.
  trueque       public card-for-card: our cheapest dump spare for a high-value wishlist card. Any team
                can take it; directed WTB swaps stay in wtb.py.

Never our own venue (self_venue). One new listing per tick. The guard still cancels a loser.
"""
from __future__ import annotations

import math
import os
import sys

from bazaar_sdk import BazaarError
from trader import Trader
from venues import safe_markets
from workshop import listing_reserve

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent"))
from ledger import build  # noqa: E402
from team_intel import lean  # noqa: E402

EXPIRES_PUBLIC = 80
EXPIRES_CLOSER = 120
TAPAS_SIZE = 2
MIN_PAGE_SEEN = 9          # 9/10 seen in the public ledger: the last card is a real page closer
MIN_PAGE_SEEN_WITH_BID = 5  # they already bid: 5 seen page cards is enough signal


def council_note(topic, text, evidence=None, tick=None):
    try:
        import council
        council.post("tapas", topic, text, evidence, tick=tick)
    except Exception:
        pass


def page_gaps(hold: dict, values, me: str, bids: dict | None = None) -> list:
    """[(team, set_id, missing_ref, seen, why)] teams close to a page, missing a card we might hold.

    The ledger hides starting hands, so 'missing' only means unseen. We require 9 seen page cards
    (or 5 seen plus an open bid for the unseen one) before we treat the gap as real.
    """
    bids = bids or {}
    out = []
    for team, cards in (hold or {}).items():
        if team == me or not (str(team)[:1] == "t" and str(team)[1:].isdigit()):
            continue
        for sid in values.sets:
            page = values.page_cards(sid)
            if len(page) < 6:
                continue
            seen = [r for r in page if (cards.get(r) or [0])[0] > 0]
            miss = [r for r in page if (cards.get(r) or [0])[0] <= 0]
            if len(miss) == 1 and len(seen) >= MIN_PAGE_SEEN:
                out.append((team, sid, miss[0], len(seen), "page"))
            elif len(miss) >= 1 and len(seen) >= MIN_PAGE_SEEN_WITH_BID:
                for ref in miss:
                    if any(t == team for t, _p in bids.get(ref, [])):
                        out.append((team, sid, ref, len(seen), "bid"))
    return out


def closer_price(values, ref: str, set_id: str, min_gain: float, share: float, bid: int | None = None) -> int:
    """Ask a page-closer price: our loss + margin + a slice of the estimated page bonus, never above a live bid."""
    loss = values.loss_of_removing([ref])
    book = values.book(ref)
    floor = max(1, math.ceil(loss + min_gain))
    page_book = sum(values.book(r) for r in values.page_cards(set_id))
    bonus = values.page_bonus_rate * page_book * 1.25  # they collect this set
    fair = math.ceil(max(floor, book * 1.15, loss + min_gain + share * bonus))
    fair = min(fair, max(floor, math.ceil(book * 2.2)))  # naive agents reject 3× book commons
    if bid:
        return max(floor, min(max(fair, bid - 1), bid))
    return max(floor, fair)


def free_spares(ctx) -> list:
    """Spares we may list: not promised, not reserved, not workshop-held, not a near-complete collect page."""
    v, S = ctx.values, ctx.S
    if v is None:
        return []
    locked = ctx.locked_assets() if hasattr(ctx, "locked_assets") else set()
    wr = listing_reserve(S, v, locked)
    hold = {a["id"] for a in v.workshop_held(wr)}
    spent = set(ctx.state.get("workshop_spent") or [])
    trader = Trader(ctx)
    out = []
    for a in v.spares(reserve=wr):
        if a["id"] in locked or a["id"] in hold or a["id"] in spent:
            continue
        if trader.protected(a["ref"]):
            continue
        out.append(a)
    return out


def _open_bids(ctx) -> dict:
    """ref -> [(team, price)] cash bids on other teams' boards."""
    out = {}
    me = ctx.me.get("id")
    intel = getattr(ctx, "intel", None)
    makers = (intel.summary().get("offer_maker", {}) if intel else {}) or {}
    for venue, offers in (getattr(ctx, "boards", None) or {}).items():
        if venue == ctx.state.get("venue"):
            continue
        for o in offers or []:
            if o.get("status", "open") != "open" or o.get("to"):
                continue
            g, w = o.get("give") or {}, o.get("want") or {}
            if not g.get("cash") or g.get("assets") or w.get("cash"):
                continue
            refs = [t[5:] for t in (w.get("types") or []) if t.startswith("card:")]
            refs += [a["ref"] for a in (w.get("assets") or []) if isinstance(a, dict) and a.get("ref")]
            refs += list(w.get("cards") or [])
            if len(refs) != 1:
                continue
            team = makers.get(o.get("id")) or o.get("maker")
            if not team or team == me or not (str(team)[:1] == "t" and str(team)[1:].isdigit()):
                continue
            out.setdefault(refs[0], []).append((team, int(g["cash"])))
    return out


def _pending(ctx) -> tuple[set, set, set]:
    """(asset ids already promised, card refs we already want, closer keys team:ref)."""
    me = ctx.me.get("id")
    assets, wants, closers = set(), set(), set()
    for o in ctx.my_offers or []:
        if o.get("maker") != me or o.get("status", "open") != "open":
            continue
        g, w = o.get("give") or {}, o.get("want") or {}
        for a in g.get("assets") or []:
            assets.add(a["id"] if isinstance(a, dict) else a)
        for t in w.get("types") or []:
            if t.startswith("card:"):
                wants.add(t[5:])
        wants.update(w.get("cards") or [])
        if o.get("to") and (w.get("cash") or 0) and g.get("assets"):
            refs = []
            for a in g.get("assets") or []:
                if isinstance(a, dict) and a.get("ref"):
                    refs.append(a["ref"])
            info = (ctx.state.get("menu") or {}).get(str(o.get("id"))) or {}
            if info.get("ref"):
                closers.add(f"{o.get('to')}:{info['ref']}")
            for r in refs:
                closers.add(f"{o.get('to')}:{r}")
    return assets, wants, closers


def plan(ctx, hold=None, leans=None) -> list:
    """Ranked listings: dicts with kind, score, give assets, want, to, expires, note. No API writes."""
    v, S = ctx.values, ctx.S
    if v is None:
        return []
    me = ctx.me.get("id")
    intel = getattr(ctx, "intel", None)
    if hold is None and intel is not None:
        events = sorted(intel.events.values(), key=lambda e: e.get("id", 0))
        board = {"teams": getattr(ctx, "leaderboard", None) or []}
        hold, leans = build(events, board), lean(events)
    hold, leans = hold or {}, leans or {}
    bids = _open_bids(ctx)
    promised, pending_wants, closer_keys = _pending(ctx)
    spares = [a for a in free_spares(ctx) if a["id"] not in promised]
    by_ref = {}
    for a in spares:
        by_ref.setdefault(a["ref"], []).append(a)
    min_gain = float(S.get("trade_min_gain", 3))
    share = float(S.get("tapas_closer_share", 0.15))
    out = []

    # 1) último cromo
    for team, sid, ref, seen, why in page_gaps(hold, v, me, bids):
        if leans.get(team, {}).get(sid, 0) <= 0:
            continue  # they dump this set: they will not pay a page-closer premium
        if hasattr(ctx, "is_untrusted") and ctx.is_untrusted(team):
            continue
        if f"{team}:{ref}" in closer_keys or ref not in by_ref:
            continue
        asset = by_ref[ref][0]
        their_bid = max((p for t, p in bids.get(ref, []) if t == team), default=None)
        price = closer_price(v, ref, sid, min_gain, share, their_bid)
        loss = v.loss_of_removing([ref])
        if price - loss < min_gain:
            continue
        out.append({"kind": "cromo", "score": price - loss + 20, "assets": [asset], "want": {"cash": price},
                    "to": team, "expires": EXPIRES_CLOSER, "ref": ref,
                    "note": f"último cromo: {team} {seen}/page {sid} missing {ref} ({why}) at {price} P"})
    closer_refs = {x["ref"] for x in out if x["kind"] == "cromo"}

    # 2) trueque: one public swap per wanted card, each dump spare used at most once
    dump = [a for a in spares if v.dump_tier(a["ref"]) in ("hard", "extra", "soft")
            and a["ref"] not in closer_refs]
    dump.sort(key=lambda a: (0 if v.dump_tier(a["ref"]) == "hard" else 1, v.loss_of_removing([a["ref"]])))
    used_give = set()
    for ref, gain in v.wishlist(limit=12):
        if ref in pending_wants:
            continue
        for a in dump:
            if a["id"] in used_give or a["ref"] == ref:
                continue
            net = gain - v.loss_of_removing([a["ref"]])
            if net < min_gain:
                continue
            out.append({"kind": "trueque", "score": net, "assets": [a], "want": {"cards": [ref]},
                        "to": None, "expires": EXPIRES_PUBLIC, "ref": ref,
                        "note": f"trueque: {a['ref']} for {ref} (we gain {net:.0f} P)"})
            used_give.add(a["id"])
            break

    # 3) tapas platters: two dump cards from the same neighbourhood
    dump_sets = {}
    for a in dump:
        sid = v.cards.get(a["ref"], {}).get("set")
        if not sid or v.m(a["ref"]) >= 1.0:
            continue  # only neighbourhoods we dump
        dump_sets.setdefault(sid, []).append(a)
    for sid, cards in dump_sets.items():
        seen = set()
        unique = []
        for a in cards:
            if a["ref"] in seen:
                continue
            seen.add(a["ref"])
            unique.append(a)
        if len(unique) < TAPAS_SIZE:
            continue
        pair = unique[:TAPAS_SIZE]
        refs = [a["ref"] for a in pair]
        loss = v.loss_of_removing(refs)
        books = sum(v.book(r) for r in refs)
        price = max(math.ceil(loss + min_gain + 1), math.ceil(0.85 * books))
        if price - loss < min_gain:
            continue
        out.append({"kind": "tapas", "score": price - loss, "assets": pair, "want": {"cash": price},
                    "to": None, "expires": EXPIRES_PUBLIC, "ref": "+".join(refs),
                    "note": f"{sid} tapas: {'+'.join(refs)} at {price} P (books {books:.0f})"})

    rank = {"cromo": 0, "trueque": 1, "tapas": 2}
    out.sort(key=lambda x: (rank.get(x["kind"], 9), -x["score"]))
    return out


class Tapas:
    def __init__(self, ctx):
        self.ctx = ctx

    def step(self):
        ctx, S = self.ctx, self.ctx.S
        if not S.get("enable_tapas", 1) or ctx.values is None:
            return
        menu = ctx.state.setdefault("menu", {})
        mine = {str(o["id"]): o for o in ctx.my_offers if o.get("status", "open") == "open"}
        for oid in list(menu):
            if oid not in mine:
                ctx.log("tapas", "listing_gone", offer=oid, info=menu.pop(oid))
        if len(menu) >= int(S.get("tapas_max_open", 4)):
            return
        if len(mine) >= ctx.limit("max_open_offers_per_team", 30) - 2:
            return
        rows = plan(ctx)
        busy = set()
        for L in menu.values():
            busy.update(L.get("assets") or [])
            if L.get("kind") == "cromo" and L.get("to") and L.get("ref"):
                busy.add(f"{L['to']}:{L['ref']}")
        for row in rows:
            ids = [a["id"] for a in row["assets"]]
            if any(i in busy for i in ids):
                continue
            if row["kind"] == "cromo" and f"{row['to']}:{row['ref']}" in busy:
                continue
            self._post(row)
            return

    def _post(self, row):
        ctx = self.ctx
        venue = safe_markets(ctx, row.get("to"))[0]
        give = {"assets": [a["id"] for a in row["assets"]]}
        try:
            o = ctx.api.list_offer(give, row["want"], venue=venue, to=row.get("to"),
                                   expires_in_ticks=row["expires"])
        except BazaarError as e:
            ctx.log("tapas", "list_refused", kind=row["kind"], error=str(e)[:160], note=row["note"])
            return
        ctx.state.setdefault("menu", {})[str(o.get("id"))] = {
            "kind": row["kind"], "ref": row.get("ref"), "to": row.get("to"), "venue": venue,
            "assets": give["assets"], "want": row["want"], "tick": ctx.clock.get("tick", 0),
        }
        ctx.log("tapas", "listed", kind=row["kind"], offer=o.get("id"), venue=venue, to=row.get("to"),
                give=[a["ref"] for a in row["assets"]], want=row["want"], note=row["note"])
        council_note("menu", row["note"] + f" on {venue}.",
                     {"kind": row["kind"], "offer": o.get("id"), "venue": venue, "to": row.get("to")},
                     tick=ctx.clock.get("tick"))
