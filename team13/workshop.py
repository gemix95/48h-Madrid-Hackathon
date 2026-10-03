"""The Workshop (El Taller): three spare copies of one rarity become one card of the next.

POST /api/taller {"assets": [a, b, c]}. The three assets must be different cards of one rarity, and
we have to keep at least one copy of each card. The card that comes back is luck: it is shown and
never scored. We still craft when that card is worth more to us than the three copies we burn,
because the copy we keep can complete a page or be traded, and both of those do score.

Only duplicates are fuel (never a last copy, never a card already in an offer). The cheapest three
of one rarity go in, and only when the average value of the next rarity beats them by workshop_edge.
"""
from __future__ import annotations

from bazaar_sdk import BazaarError

NEXT = {"common": "uncommon", "uncommon": "rare", "rare": "epic", "epic": "legendary"}
RARITY_LADDER = ("common", "uncommon", "rare", "epic")
HOLD_ALL_RESERVE = 9999  # spares(reserve=…) returns nothing listable


def spare_copies(values, locked) -> list:
    """Duplicate assets we can hand over and still keep one of that card. Locked copies stay put,
    and they do not count as the copy we keep: a listed duplicate is already promised away."""
    by_ref: dict = {}
    for a in values.assets:
        if a.get("ref") in values.cards:
            by_ref.setdefault(a["ref"], []).append(a)
    out = []
    locked = set(locked)
    for copies in by_ref.values():
        committed = [a for a in copies if a["id"] in locked]
        free = [a for a in copies if a["id"] not in locked]
        n_give = max(0, len(copies) - 1 - len(committed))
        free.sort(key=lambda a: (a.get("serial", 0), a["id"]))
        out.extend(free[-n_give:] if n_give else [])
    return out


def fuel_by_rarity(values, locked) -> dict:
    by: dict = {}
    for a in spare_copies(values, locked):
        rar = values.cards[a["ref"]]["rarity"]
        by.setdefault(rar, []).append(a)
    return by


def accumulating(values, locked, target: int = 3) -> tuple[bool, str, int]:
    """True while we do not yet have `target` spare copies of any one tier (workshop fuel).

    Returns (still_accumulating, tier closest to a trio, how many spares we have there).
    """
    if target < 1:
        return False, "", 0
    by = fuel_by_rarity(values, locked)
    best_rar, best_n = "common", 0
    for rar in RARITY_LADDER:
        n = len(by.get(rar, []))
        if n >= target:
            return False, rar, n
        if n > best_n:
            best_rar, best_n = rar, n
    return True, best_rar, best_n


def listing_reserve(strategy: dict, values, locked) -> int:
    """How many cheapest spares stay off El Rastro. While accumulating a trio, hold every duplicate."""
    if not int(strategy.get("enable_workshop", 1)):
        return int(strategy.get("workshop_spares", 0))
    if int(strategy.get("workshop_accumulate", 1)):
        accum, _, _ = accumulating(values, locked, int(strategy.get("workshop_trio_target", 3)))
        if accum:
            return HOLD_ALL_RESERVE
    return int(strategy.get("workshop_spares", 0))


def restock_packs(strategy: dict, values, locked) -> bool:
    """Whether the haggler should buy neighbourhood packs to grow the duplicate pool."""
    if int(strategy.get("enable_workshop", 1)) and int(strategy.get("workshop_accumulate", 1)):
        accum, _, _ = accumulating(values, locked, int(strategy.get("workshop_trio_target", 3)))
        if accum:
            return True
    if len(spare_copies(values, locked)) < int(strategy.get("workshop_spares", 0)):
        return True
    return bool(strategy.get("haggle_buy_packs"))


def choose(values, locked, edge: float) -> dict | None:
    """The best trio to send, or None. Best means the largest gap between the expected next card
    and the private value of the three duplicates."""
    by_rar: dict = {}
    for a in spare_copies(values, locked):
        rar = values.cards[a["ref"]]["rarity"]
        by_rar.setdefault(rar, []).append(a)
    best = None
    for rar, group in by_rar.items():
        nxt = NEXT.get(rar)
        if not nxt or len(group) < 3:
            continue
        group.sort(key=lambda a: (values.loss_of_removing([a["ref"]]), -a.get("serial", 0), a["id"]))
        pick = group[:3]
        refs = [a["ref"] for a in pick]
        loss = values.loss_of_removing(refs)
        if loss == float("inf"):
            continue
        pool = [c["id"] for c in values.cards.values()
                if c.get("released", True) and not c.get("hidden") and c["rarity"] == nxt]
        if not pool:
            continue
        ev = sum(values.gain_of_adding([r]) for r in pool) / len(pool)
        surplus = ev - loss
        if surplus + 1e-9 < edge:
            continue
        if best is None or surplus > best["surplus"]:
            best = {"ids": [a["id"] for a in pick], "refs": refs, "rarity": rar, "next": nxt,
                    "loss": round(loss, 2), "ev": round(ev, 2), "surplus": round(surplus, 2)}
    return best


def pulled_card(res: dict) -> dict:
    if not isinstance(res, dict):
        return {}
    for key in ("card", "result", "pull"):
        if isinstance(res.get(key), dict):
            return res[key]
    cards = res.get("cards")
    if isinstance(cards, list) and cards and isinstance(cards[0], dict):
        return cards[0]
    return {}


class Workshop:
    def __init__(self, ctx):
        self.ctx = ctx

    def _locked(self) -> set:
        ctx = self.ctx
        locked = set(ctx.locked_assets())
        flip = ctx.state.get("flip") or {}
        if flip.get("asset"):
            locked.add(flip["asset"])
        for loan in (ctx.state.get("loans") or {}).values():
            if loan.get("status") in ("active", "collateral_held") and loan.get("asset"):
                locked.add(loan["asset"])
        return locked

    def step(self):
        ctx = self.ctx
        ctx.state["workshop_spent"] = []
        if ctx.values is None:
            return
        plan = choose(ctx.values, self._locked(), float(ctx.S.get("workshop_edge", 2)))
        if not plan:
            return
        key = ",".join(str(i) for i in sorted(plan["ids"]))
        failed = ctx.state.setdefault("workshop_failed", {})
        tick = ctx.clock.get("tick") or 0
        if tick - failed.get(key, -999) < 10:
            return
        ctx.state["workshop_spent"] = list(plan["ids"])  # trader must not list these on this tick
        try:
            res = ctx.api.taller(plan["ids"])
        except BazaarError as e:
            ctx.state["workshop_spent"] = []
            failed[key] = tick
            ctx.log("workshop", "refused", error=str(e)[:200], refs=plan["refs"], rarity=plan["rarity"],
                    next=plan["next"], loss=plan["loss"], ev=plan["ev"])
            return
        card = pulled_card(res)
        if card.get("id") is not None:  # reserved.json "crafted": what comes out is kept for swap strategies
            ctx.state.setdefault("crafted_assets", []).append(card["id"])
        ctx.log("workshop", "crafted", refs=plan["refs"], ids=plan["ids"], rarity=plan["rarity"],
                next=plan["next"], loss=plan["loss"], ev=plan["ev"], surplus=plan["surplus"],
                pulled=card.get("ref"), pulled_rarity=card.get("rarity"), luck=res.get("luck") if isinstance(res, dict) else None)
