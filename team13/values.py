"""Our private value model: what one more (or one less) copy of a card is worth to Team 13.

Mirrors the server's rule from the catalog: value = book x set multiplier x copy factor (1st copy x1, 2nd x0.25,
3rd x0.1), plus a page bonus when a set's commons, uncommons and rares are all held (and a master bonus on top
for the epic and legendary). The server's `your_value` stays the source of truth: `exact_gain` asks it when a
decision is close.
"""
from __future__ import annotations

from collections import Counter


class Values:
    def __init__(self, catalog: dict, me: dict):
        self.catalog = catalog
        vals = catalog.get("values") or {}
        self.marg = vals.get("copy_marginals") or [1.0, 0.25, 0.1]
        self.page_bonus_rate = vals.get("page_bonus", 0.25)
        self.master_bonus_rate = vals.get("master_bonus", 0.1)
        self.cards = {c["id"]: {**c, "set": s["id"], "released": s.get("released", True)}
                      for s in catalog["sets"] for c in s["cards"]}
        self.sets = {s["id"]: s for s in catalog["sets"]}
        self.update(me)

    def update(self, me: dict) -> None:
        self.me = me
        self.aff = me.get("affinity") or {}
        self.assets = [a for a in me.get("assets", []) if a.get("kind") == "card"]
        self.held = Counter(a["ref"] for a in self.assets)

    # ---------------------------------------------------------------- basics
    def m(self, ref: str) -> float:
        return self.aff.get(self.cards[ref]["set"], 1.0) if ref in self.cards else 1.0

    def copy_factor(self, n: int) -> float:
        return self.marg[min(n, len(self.marg) - 1)]

    def page_cards(self, set_id: str) -> list:
        return [c["id"] for c in self.sets[set_id]["cards"] if c.get("page")]

    def page_value(self, set_id: str) -> float:
        return sum(self.cards[r]["book"] * self.m(r) for r in self.page_cards(set_id))

    def page_complete(self, set_id: str, held=None) -> bool:
        held = self.held if held is None else held
        return all(held[r] > 0 for r in self.page_cards(set_id))

    def bonus(self, set_id: str, held) -> float:
        if not self.page_complete(set_id, held):
            return 0.0
        b = self.page_bonus_rate * self.page_value(set_id)
        extras = [c["id"] for c in self.sets[set_id]["cards"] if not c.get("page") and not c.get("hidden")]
        if extras and all(held[r] > 0 for r in extras):
            b += self.master_bonus_rate * self.page_value(set_id)
        return b

    # ---------------------------------------------------------------- marginal values
    def gain_of_adding(self, refs) -> float:
        """Value to us of receiving these cards (duplicates and page completion included)."""
        held = Counter(self.held)
        total = 0.0
        for ref in refs:
            if ref not in self.cards:
                continue
            c = self.cards[ref]
            sid = c["set"]
            before = self.bonus(sid, held)
            total += c["book"] * self.m(ref) * self.copy_factor(held[ref])
            held[ref] += 1
            total += self.bonus(sid, held) - before
        return total

    def loss_of_removing(self, refs) -> float:
        """Value we give up by handing over these cards."""
        held = Counter(self.held)
        total = 0.0
        for ref in refs:
            if held[ref] <= 0 or ref not in self.cards:
                return float("inf")  # we do not hold it: never agree
            c = self.cards[ref]
            sid = c["set"]
            before = self.bonus(sid, held) + self.page_option(sid, held)
            held[ref] -= 1
            total += c["book"] * self.m(ref) * self.copy_factor(held[ref])
            total += before - self.bonus(sid, held) - self.page_option(sid, held)
        return total

    PAGE_OPTION = {1: 0.6, 2: 0.3}  # chance-weighted share of the page bonus while 1 or 2 page cards are missing

    def page_option(self, set_id: str, held) -> float:
        """What a nearly complete page is worth before it is complete: giving away one of its cards (SAL-07 from a
        9/10 Salamanca page) costs the card and part of the bonus the last card would bring."""
        missing = sum(1 for r in self.page_cards(set_id) if held[r] <= 0)
        return self.PAGE_OPTION.get(missing, 0.0) * self.page_bonus_rate * self.page_value(set_id)

    # ---------------------------------------------------------------- helpers for strategies
    def spares(self) -> list:
        """Assets we could give away cheaply: extra copies first, then cards of sets we value least."""
        by_ref: dict = {}
        for a in sorted(self.assets, key=lambda a: a.get("serial", 0)):
            by_ref.setdefault(a["ref"], []).append(a)
        out = []
        for ref, copies in by_ref.items():
            out += copies[1:]  # keep the lowest serial, offer the rest
        low = sorted((s for s, v in self.aff.items() if v < 1.0), key=lambda s: self.aff[s])
        for ref, copies in by_ref.items():
            if self.cards.get(ref, {}).get("set") in low and copies[0] not in out:
                out.append(copies[0])
        return out

    def wishlist(self, limit: int = 12) -> list:
        """Released cards we lack, ranked by what one copy would be worth to us (page completion included)."""
        cands = [r for r, c in self.cards.items() if c["released"] and not c.get("hidden") and self.held[r] == 0
                 and c["rarity"] in ("common", "uncommon", "rare")]
        return sorted(((r, self.gain_of_adding([r])) for r in cands), key=lambda x: -x[1])[:limit]

    def book(self, ref: str) -> float:
        return self.cards.get(ref, {}).get("book", 0)

    def pack_ev(self, pack: dict) -> float:
        def pool(r):
            return [c for c in self.cards.values() if c["released"] and not c.get("hidden") and c["rarity"] == r]
        ev = 0.0
        for slot in pack["slots"]:
            for rarity, p in slot.items():
                cs = pool(rarity)
                if cs:
                    ev += p * sum(c["book"] * self.m(c["id"]) * self.copy_factor(self.held[c["id"]]) for c in cs) / len(cs)
        return ev
