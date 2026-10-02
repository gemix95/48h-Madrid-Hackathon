"""The starter broker's two plans, kept verbatim as our fallback (from bazaar-kit/starter_broker.py)."""
import math


def bench_plan(book: dict) -> list:
    """[(sell id, buy id, price)]: in each bench run, the highest bid against the lowest ask while the bid covers it,
    at the midpoint. sorted() keeps the book's order among equal quotes, as the stall does."""
    plan, runs = [], {}  # run ("b12" in the offer id "b12-7") -> (asks, bids): a match pairs two offers of one run
    for o in book.get("bench_offers") or []:
        asks, bids = runs.setdefault(o["id"].split("-")[0], ([], []))
        if o["want"]["cash"]:  # a bench seller asks for cash, a bench buyer bids it
            asks.append((o["want"]["cash"], o["id"]))
        else:
            bids.append((o["give"]["cash"], o["id"]))
    for asks, bids in runs.values():
        for (ask, sell), (bid, buy) in zip(sorted(asks, key=lambda a: a[0]), sorted(bids, key=lambda b: -b[0])):
            if bid < ask:
                break
            plan.append((sell, buy, (ask + bid) // 2))
    return plan


def public_plan(book: dict) -> list:
    """[(sell id, buy id, price)]: your venue's real offers, card by card, the lowest ask against the highest bid for
    that card, at the midpoint, lowered until the buyer can also pay your fee. At most 10 matches per tick."""
    def fee(price: int) -> int:  # as the venue charges it, rounded up
        return math.ceil(book["fee_bps"] * price / 10000) + book["fee_per_card"]
    plan, offers = [], book.get("offers") or []
    bids = sorted((o for o in offers if o["give"]["cash"] and len(o["want"]["types"]) == 1), key=lambda o: -o["give"]["cash"])
    for s in sorted((o for o in offers if len(o["give"]["assets"]) == 1 and o["want"]["cash"]), key=lambda o: o["want"]["cash"]):
        ask, card = s["want"]["cash"], "{kind}:{ref}".format(**s["give"]["assets"][0])
        b = next((b for b in bids if b["want"]["types"] == [card] and b["maker"] != s["maker"]
                  and ask + fee(ask) <= b["give"]["cash"]), None)  # the highest bid for this card that covers ask + fee
        if b:
            bids.remove(b)
            price = next(p for p in range((ask + b["give"]["cash"]) // 2, ask - 1, -1) if p + fee(p) <= b["give"]["cash"])
            plan.append((s["id"], b["id"], price))
    return plan[:10]


