"""El Menú (tapas.py), offline: page gaps, último-cromo pricing, tapas platters, public trueque,
reserved cards skipped, one listing per tick, never our own venue.

    python3 tests/test_tapas.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import strategy  # noqa: E402
from values import Values  # noqa: E402
import tapas  # noqa: E402


def catalog():
    rarities = ["common"] * 5 + ["uncommon"] * 3 + ["rare"] * 2
    books = [8, 8, 8, 10, 10, 22, 22, 25, 45, 50]

    def cards(sid):
        return [{"id": f"{sid}-{i+1:02d}", "book": books[i], "rarity": rarities[i], "page": True}
                for i in range(10)]

    return {
        "values": {"copy_marginals": [1.0, 0.25, 0.1], "page_bonus": 0.25, "master_bonus": 0.1},
        "sets": [{"id": sid, "released": True, "cards": cards(sid)} for sid in ("LAT", "RET", "SAL")],
    }


def me_with(holdings, cash=200):
    assets, n = [], 1
    for ref, count in holdings.items():
        for _ in range(count):
            assets.append({"id": n, "kind": "card", "ref": ref, "serial": 1000 + n,
                           "rarity": "common" if ref.endswith(("01", "02", "03", "04", "05")) else "uncommon"})
            n += 1
    return {
        "id": "t13", "cash": cash, "level": 2,
        "affinity": {"SAL": 1.6, "MAL": 1.3, "LAV": 1.1, "CHA": 0.9, "RET": 0.7, "LAT": 0.5},
        "assets": assets,
    }


class Api:
    def __init__(self):
        self.posted = []
        self.n = 800

    def list_offer(self, give, want, venue=None, to=None, expires_in_ticks=40):
        self.n += 1
        o = {"id": self.n, "maker": "t13", "give": give, "want": want, "venue": venue, "to": to, "status": "open"}
        self.posted.append(o)
        return o


class Ctx:
    def __init__(self, holdings, hold=None, leans=None, locked=None, boards=None):
        self.me = me_with(holdings)
        self.values = Values(catalog(), self.me)
        self.S = {**strategy.defaults(), "enable_tapas": 1, "trade_min_gain": 3, "tapas_max_open": 4,
                  "tapas_closer_share": 0.15, "workshop_accumulate": 0, "workshop_spares": 0,
                  "enable_workshop": 0, "rival_margin": 6}
        self.state = {"venue": "v24"}
        self.clock = {"tick": 100}
        self.my_offers = []
        self.threads = []
        self.venues = [{"venue": "v24", "owner": "t13", "status": "open", "fee_bps": 0},
                       {"venue": "rastro", "owner": "house", "status": "open", "fee_bps": 500, "fee_per_card": 1}]
        self.leaderboard = [{"team": "t13", "score": 40}, {"team": "t04", "score": 10}]
        self.boards = boards or {}
        self.hold, self.leans = hold or {}, leans or {}
        self.intel = None
        if hold:
            events, eid = [], 1
            for team, cards in hold.items():
                for ref in cards:
                    events.append({"id": eid, "tick": 10, "type": "settlement",
                                   "payload": {"items": [{"kind": "card", "ref": ref, "frm": "t99", "to": team}]}})
                    eid += 1
            makers = {}
            for venue, offers in (boards or {}).items():
                for o in offers or []:
                    if o.get("id") and o.get("maker"):
                        makers[o["id"]] = o["maker"]
            self.intel = type("I", (), {"events": {e["id"]: e for e in events},
                                        "summary": lambda self: {"offer_maker": makers}})()
        self._locked = set(locked or [])
        self.api = Api()
        self.logs = []

    def locked_assets(self, reserved=True):
        return set(self._locked)

    def reserve(self):
        return 0

    def limit(self, name, default):
        return default

    def log(self, *a, **k):
        self.logs.append((a, k))

    def is_untrusted(self, team):
        return team == "t66"


def kinds(rows):
    return {r["kind"] for r in rows}


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    return bool(ok)


def run():
    r = []
    cat = catalog()
    v = Values(cat, me_with({"LAT-10": 1}))
    hold_close = {"t04": {f"LAT-{i:02d}": [1, 10, "got it"] for i in range(1, 10)}}
    gaps = tapas.page_gaps(hold_close, v, "t13")
    r.append(check("9/10 Latina is a page gap on LAT-10",
                   any(g[:4] == ("t04", "LAT", "LAT-10", 9) for g in gaps), gaps))
    thin = {"t04": {f"LAT-{i:02d}": [1, 10, "x"] for i in range(1, 5)}}
    r.append(check("4/10 seen is not a closer (ledger hides the rest)",
                   tapas.page_gaps(thin, v, "t13") == []))

    p = tapas.closer_price(v, "LAT-10", "LAT", 3, 0.15)
    loss = v.loss_of_removing(["LAT-10"])
    r.append(check("closer price beats loss + min gain", p - loss >= 3, f"price={p} loss={loss}"))
    r.append(check("closer price stays near 2× book for naive agents", p <= v.book("LAT-10") * 2.2 + 1, p))
    r.append(check("a live bid between floor and fair becomes the ask",
                   tapas.closer_price(v, "LAT-10", "LAT", 3, 0.15, bid=40) == 40, p))

    ctx = Ctx({"LAT-01": 1, "LAT-02": 1, "LAT-10": 1, "RET-01": 1},
              hold=hold_close, leans={"t04": {"LAT": 4.0, "SAL": -1}})
    rows = tapas.plan(ctx, hold=hold_close, leans={"t04": {"LAT": 4.0}})
    r.append(check("plan has último cromo for t04",
                   any(x["kind"] == "cromo" and x["to"] == "t04" and x["ref"] == "LAT-10" for x in rows), rows))
    r.append(check("plan has a Latina tapas platter",
                   any(x["kind"] == "tapas" and "LAT-01" in x["ref"] and "LAT-02" in x["ref"] for x in rows), rows))
    r.append(check("plan has a public trueque into Salamanca",
                   any(x["kind"] == "trueque" and x["want"]["cards"][0].startswith("SAL") and x["to"] is None
                       for x in rows), rows))
    tap = next(x for x in rows if x["kind"] == "tapas")
    books = sum(ctx.values.book(a["ref"]) for a in tap["assets"])
    r.append(check("tapas platter is cheaper than the two books", tap["want"]["cash"] <= books, tap))

    dumpers = tapas.plan(ctx, hold=hold_close, leans={"t04": {"LAT": -3.0}})
    r.append(check("no closer to a team that dumps the set",
                   not any(x["kind"] == "cromo" and x["to"] == "t04" for x in dumpers)))

    ctx._locked = {a["id"] for a in ctx.values.assets if a["ref"] == "LAT-10"}
    locked_rows = tapas.plan(ctx, hold=hold_close, leans={"t04": {"LAT": 4.0}})
    r.append(check("reserved / locked LAT-10 is never offered",
                   not any(x["ref"] == "LAT-10" or any(a["ref"] == "LAT-10" for a in x["assets"])
                           for x in locked_rows)))
    ctx._locked = set()

    tapas.Tapas(ctx).step()
    r.append(check("one listing per tick", len(ctx.api.posted) == 1, ctx.api.posted))
    r.append(check("never listed on our own venue",
                   all(o.get("venue") != "v24" for o in ctx.api.posted), ctx.api.posted))
    first = ctx.api.posted[0]
    r.append(check("first listing is the page closer (highest score)",
                   first.get("to") == "t04" and (first.get("give") or {}).get("assets"), first))
    ctx.my_offers = list(ctx.api.posted)
    tapas.Tapas(ctx).step()
    r.append(check("second tick lists something else", len(ctx.api.posted) == 2, ctx.api.posted))

    ctx2 = Ctx({"LAT-01": 1})
    ctx2.S["enable_tapas"] = 0
    tapas.Tapas(ctx2).step()
    r.append(check("off when enable_tapas=0", ctx2.api.posted == []))

    # bid-signal closer: only 6 page cards seen, but they already bid for the missing one
    hold_bid = {"t08": {f"LAT-{i:02d}": [1, 10, "x"] for i in range(1, 7)}}
    boards = {"rastro": [{"id": 9, "maker": "t08", "status": "open",
                          "give": {"cash": 16}, "want": {"types": ["card:LAT-10"]}}]}
    ctx3 = Ctx({"LAT-10": 1}, hold=hold_bid, leans={"t08": {"LAT": 2.0}}, boards=boards)
    ctx3.intel = type("I", (), {"summary": lambda self: {"offer_maker": {9: "t08"}}})()
    gaps = tapas.page_gaps(hold_bid, ctx3.values, "t13", tapas._open_bids(ctx3))
    r.append(check("open bid + 6 seen page cards is a closer gap",
                   any(g[0] == "t08" and g[2] == "LAT-10" and g[4] == "bid" for g in gaps), gaps))

    print(f"\n{sum(r)}/{len(r)} passed")
    return all(r)


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
