"""Auctions, offline: registration checks the ask, the broker holds a lot back until its last tick, then sells it to
the best bid at the second bid + 1 (never below the reserve, never above the winner, never to the seller itself).

    python3 tests/test_auctions.py
"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import auctions  # noqa: E402

path = os.path.join(tempfile.mkdtemp(), "lots.json")
ask = {"id": 10, "maker": "m1", "give": {"assets": [{"id": 5, "kind": "card", "ref": "LAT-10"}]}, "want": {"cash": 50}, "expires_tick": 200}
bid = lambda i, cash, m: {"id": i, "maker": m, "give": {"cash": cash}, "want": {"types": ["card:LAT-10"]}}
book = [ask, bid(20, 61, "m2"), bid(21, 72, "m3"), bid(22, 40, "m4"), bid(23, 99, "m1")]
assert "error" in auctions.register(99, book, 100, 30, path)
assert "error" in auctions.register(20, book, 100, 30, path)          # a bid is not a lot
lot = auctions.register(10, book, 100, 30, path)
assert lot["end"] == 130 and lot["reserve"] == 50, lot
# before the end: the broker's crossing match for the lot is held back
plan = [(10, 21, 61)]
assert auctions.broker_plan(plan, book, 120, path) == []
# last tick: best bid 72 (m3) wins at the second bid 61 + 1; the seller's own 99 never counts; 40 is under the reserve
assert auctions.broker_plan([], book, 129, path) == [(10, 21, 62)]
assert auctions.load(path)["10"]["status"] == "closing"
# a lot with one bid sells at the reserve; a lot without a bid closes unsold; a lot whose ask left closes as gone
lot2 = auctions.register(10, [ask, bid(30, 80, "m2")], 100, 10, path)
assert auctions.broker_plan([], [ask, bid(30, 80, "m2")], 109, path) == [(10, 30, 50)]
auctions.register(10, [ask], 100, 10, path); assert auctions.broker_plan([], [ask], 109, path) == [] and auctions.load(path)["10"]["status"] == "unsold"
auctions.register(10, [ask], 100, 10, path); assert auctions.broker_plan([], [bid(30, 80, "m2")], 105, path) == [] and auctions.load(path)["10"]["status"] == "gone"
print("auctions ok: held until the end, second price + 1, reserve kept, seller's own bid ignored")
