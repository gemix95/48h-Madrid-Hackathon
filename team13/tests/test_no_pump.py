"""No sell-and-buy-back pump, offline: giving up a card from a complete page costs exactly what getting it back is
worth (both count the option value of a nearly complete page), and the trader keeps its margin on both sides.

    python3 tests/test_no_pump.py
"""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from values import Values  # noqa: E402

cards = [{"id": f"MAL-{i:02d}", "book": 10 if i <= 5 else 25 if i <= 8 else 70, "page": True,
          "rarity": "common" if i <= 5 else "uncommon" if i <= 8 else "rare"} for i in range(1, 11)]
catalog = {"sets": [{"id": "MAL", "released": True, "cards": cards}], "values": {"page_bonus": 0.25}}


def me(refs):
    return {"affinity": {"MAL": 1.5}, "assets": [{"id": i, "ref": r, "kind": "card"} for i, r in enumerate(refs)]}


full = [c["id"] for c in cards]
v_full = Values(catalog, me(full))
v_nine = Values(catalog, me([r for r in full if r != "MAL-06"]))
sell, buy_back = v_full.loss_of_removing(["MAL-06"]), v_nine.gain_of_adding(["MAL-06"])
assert abs(sell - buy_back) < 1e-9, (sell, buy_back)
# two cards short: still symmetric, through the 0.3 option
v_eight = Values(catalog, me([r for r in full if r not in ("MAL-06", "MAL-07")]))
assert abs(v_nine.loss_of_removing(["MAL-07"]) - v_eight.gain_of_adding(["MAL-07"])) < 1e-9

# the trader's margins are back: no line lowers the required gain to the actual one
src = open(os.path.join(os.path.dirname(__file__), "..", "trader.py")).read()
assert "min_g = max(0.0, min(min_g, gain))" not in src
assert 'cap = min(value - S["trade_min_gain"], self.buy_cap(value))' in src
assert "worth_cap = math.floor(min(gain - MIN_GAIN, self.buy_cap(gain)))" in src
print(f"no pump: MAL-06 out of a full page costs {sell:.1f}, buying it back is worth {buy_back:.1f}; margins kept")
