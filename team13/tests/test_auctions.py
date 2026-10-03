"""Auctions, offline: a lot is opened by card (no ask), only open cash bids for that card at or above the reserve
count, the ranking is taken on the closing tick (best first, ties to the earlier offer), and the lot retires after
the seller's acceptance window.

    python3 tests/test_auctions.py
"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import auctions  # noqa: E402

path = os.path.join(tempfile.mkdtemp(), "lots.json")
refs = {"LAT-10", "RET-07"}
bid = lambda i, cash, ref="LAT-10", **k: {"id": i, "give": {"cash": cash}, "want": {"types": [f"card:{ref}"]}, **k}
assert "error" in auctions.register("XXX-01", 50, 30, "t07", 100, refs, path)       # unknown card
assert "error" in auctions.register("LAT-10", 50, 30, "me", 100, refs, path)        # seller is a team id
assert "error" in auctions.register("LAT-10", -5, 30, "t07", 100, refs, path)       # reserve
assert "error" in auctions.register("LAT-10", "abc", 30, "t07", 100, refs, path)
lot = auctions.register("lat-10", 50, 999, "t07", 100, refs, path)
assert lot["ref"] == "LAT-10" and lot["end"] == 160, lot                             # ticks capped at 60
assert "error" in auctions.register("LAT-10", 60, 30, "t08", 100, refs, path)       # one open lot per card
book = [bid(20, 61), bid(21, 72), bid(19, 72), bid(22, 40), bid(23, 90, "RET-07"), bid(24, 99, to="t07"),
        {"id": 25, "give": {"assets": [{"ref": "LAT-01"}]}, "want": {"types": ["card:LAT-10"]}}]
auctions.update(book, 150, path)
assert auctions.load(path)["1"]["status"] == "open"
auctions.update(book, 160, path)
l = auctions.load(path)["1"]
assert l["status"] == "ended" and l["ranking"] == [{"id": 19, "price": 72}, {"id": 21, "price": 72}, {"id": 20, "price": 61}], l
auctions.update([bid(21, 72), bid(20, 61)], 162, path)
assert auctions.load(path)["1"]["left_book"] == [19]
auctions.update([], 160 + auctions.ACCEPT_TICKS, path)
assert auctions.load(path)["1"]["status"] == "closed"
assert auctions.broker_plan([(1, 2, 3)]) == [(1, 2, 3)]
print("auctions ok: no ask, open bids only, ranking at the close, ties to the earlier offer, window then closed")
