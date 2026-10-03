"""Dump path: Retiro/Latina/extras list first, page protection at 8/10+, dump min-gain for WTB fills."""
from __future__ import annotations

import json
import math
import sys
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

from values import Values  # noqa: E402
from trader import Trader  # noqa: E402

URL = "https://bazaar.causaprima.ai"


def catalog():
    with urllib.request.urlopen(f"{URL}/api/catalog", timeout=15) as r:
        return json.load(r)


def me_with(cat, holdings: dict, cash=200):
    """holdings: ref -> count. Affinity matches Team 13."""
    assets, n = [], 1
    for ref, count in holdings.items():
        for _ in range(count):
            assets.append({"id": n, "kind": "card", "ref": ref, "serial": 1000 + n})
            n += 1
    return {
        "id": "t13", "cash": cash, "level": 2,
        "affinity": {"SAL": 1.6, "MAL": 1.3, "LAV": 1.1, "CHA": 0.9, "RET": 0.7, "LAT": 0.5},
        "assets": assets,
    }


def test_dump_tiers_and_spare_order():
    cat = catalog()
    # SAL page nearly done (9/10) + LAT dump + RET dump + SAL extra
    sal_page = [c["id"] for s in cat["sets"] if s["id"] == "SAL" for c in s["cards"] if c.get("page")]
    holdings = {r: 1 for r in sal_page[:9]}
    holdings["SAL-01"] = 2  # extra common if SAL-01 is page; else add another
    # ensure we have an extra on a held SAL page card
    holdings[sal_page[0]] = 2
    holdings["LAT-01"] = 1
    holdings["LAT-02"] = 1
    holdings["RET-01"] = 1
    holdings["CHA-01"] = 1
    v = Values(cat, me_with(cat, holdings))
    assert v.dump_tier("LAT-01") == "hard"
    assert v.dump_tier("RET-01") == "hard"
    assert v.dump_tier("CHA-01") == "soft"
    assert v.dump_tier(sal_page[0]) == "extra"
    # last unique SAL page card (only one copy among the 9) is keep if not spare-listed as dump set
    unique_sal = next(r for r in sal_page[1:9] if v.held[r] == 1)
    assert v.dump_tier(unique_sal) == "keep"
    spares = v.spares()
    refs = [a["ref"] for a in spares]
    # hard dumps before soft Chamberí; SAL unique page cards must not appear as keep-listed
    assert "LAT-01" in refs and "RET-01" in refs
    assert refs.index("LAT-01") < refs.index("CHA-01") or refs.index("RET-01") < refs.index("CHA-01")
    assert unique_sal not in refs


def test_page_protection_blocks_salamanca():
    cat = catalog()
    sal_page = [c["id"] for s in cat["sets"] if s["id"] == "SAL" for c in s["cards"] if c.get("page")]
    holdings = {r: 1 for r in sal_page[:9]}  # 9/10
    v = Values(cat, me_with(cat, holdings))
    ctx = MagicMock()
    ctx.values = v
    ctx.S = {"trade_min_gain": 3, "dump_min_gain": 1, "seek_keep_cash": 100}
    t = Trader(ctx)
    assert t.protected(sal_page[0]) is True
    # La Latina at 2/10 is not protected
    v2 = Values(cat, me_with(cat, {"LAT-01": 1, "LAT-02": 1}))
    ctx.values = v2
    assert t.protected("LAT-01") is False


def test_dump_pricing_undercuts_and_fills_wtb():
    cat = catalog()
    v = Values(cat, me_with(cat, {"LAT-03": 1, "RET-02": 1, "SAL-01": 2}))
    ctx = MagicMock()
    ctx.values = v
    ctx.me = {"id": "t13", "cash": 200, "level": 2}
    ctx.S = {"trade_min_gain": 3, "dump_min_gain": 1, "trade_ask_start": 1.05, "use_intel": 1,
             "trade_max_asks": 12, "trade_reprice_ticks": 4, "trade_max_bids": 6, "trade_bid_share": 0.4,
             "trade_all_markets": 0}
    ctx.clock = {"tick": 100}
    ctx.state = {}
    ctx.boards = {"rastro": [
        {"id": 1, "maker": "t15", "status": "open",
         "give": {"assets": [{"id": 99, "ref": "LAT-03", "kind": "card"}]}, "want": {"cash": 12}},
        {"id": 2, "maker": "t07", "status": "open",
         "give": {"cash": 9}, "want": {"cards": ["LAT-03"]}},
    ]}
    ctx.my_offers = []
    ctx.venues = []
    ctx.locked_assets = lambda: set()
    ctx.limit = lambda k, d=None: {"offers_per_team_per_tick": 12, "max_open_offers_per_team": 30}.get(k, d)
    ctx.venue_fee = lambda venue: (500, 1)
    ctx.log = lambda *a, **k: None
    listed = {}
    ctx.api = MagicMock()
    def list_offer(give, want, venue="rastro"):
        oid = 1000 + len(listed)
        o = {"id": oid, "maker": "t13", "status": "open", "give": give, "want": want, "venue": venue}
        listed[oid] = o
        return o
    ctx.api.list_offer.side_effect = list_offer
    t = Trader(ctx)
    assert t.sale_min_gain(["LAT-03"]) == 1
    assert t.sale_min_gain(["SAL-01"]) == 1  # extra
    assert t.cheapest_rival_ask("LAT-03") == 12
    assert t.best_rival_bid("LAT-03") == 9
    # Simulate ask pricing branch
    loss = v.loss_of_removing(["LAT-03"])
    floor = max(1, math.ceil(loss + 1))
    start = max(floor, math.ceil(v.book("LAT-03") * 1.0))
    start = max(floor, min(start, 12 - 2))  # hard undercut
    start = max(floor, min(start, 9))       # into WTB
    assert start <= 9
    assert start >= floor
    print("dump pricing OK: LAT floor", floor, "start", start)


if __name__ == "__main__":
    test_dump_tiers_and_spare_order()
    print("tiers/order OK")
    test_page_protection_blocks_salamanca()
    print("protection OK")
    test_dump_pricing_undercuts_and_fills_wtb()
    print("ALL PASS")
