"""Cards reserved for swap strategies (reserved.json): which ids are reserved, that the agent treats them as locked
(never given away) but still counts them as Workshop fuel, that Workshop pulls are reserved, and that the dashboard's
auto-swaps skip them. No network.

    python3 tests/test_reserved.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "..", "dashboard"))
import reserved  # noqa: E402

cfg = Path(tempfile.mkdtemp()) / "reserved.json"
reserved.PATH = cfg


def setcfg(**kw):
    cfg.write_text(json.dumps({"refs": [], "assets": [], "common_spares": False, "crafted": False, **kw}))
    os.utime(cfg, (os.path.getmtime(cfg) + 1,) * 2)  # make sure the mtime changes between writes


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    return bool(ok)


ASSETS = [{"id": 1, "kind": "card", "ref": "LAV-04", "rarity": "common"}, {"id": 2, "kind": "card", "ref": "LAV-04", "rarity": "common"},
          {"id": 3, "kind": "card", "ref": "MAL-04", "rarity": "common"},
          {"id": 4, "kind": "card", "ref": "LAT-07", "rarity": "uncommon"}, {"id": 5, "kind": "card", "ref": "LAT-07", "rarity": "uncommon"},
          {"id": 6, "kind": "pack", "ref": "sobre_barrio"}]
r = []
setcfg(common_spares=True)
r.append(check("common spares: every copy of a common we hold twice", reserved.reserved_ids(ASSETS) == {1, 2}, reserved.reserved_ids(ASSETS)))
setcfg(refs=["LAT-07"], assets=[3])
r.append(check("refs reserve every copy, assets reserve those ids", reserved.reserved_ids(ASSETS) == {3, 4, 5}, reserved.reserved_ids(ASSETS)))
setcfg(crafted=True)
r.append(check("Workshop pulls are reserved", reserved.reserved_ids(ASSETS, {"crafted_assets": [4]}) == {4}))
cfg.write_text("{ not json")
os.utime(cfg, (os.path.getmtime(cfg) + 1,) * 2)
r.append(check("a typo keeps the last good list", reserved.reserved_ids(ASSETS, {"crafted_assets": [4]}) == {4}))

# the agent: reserved cards are locked for every module, but not for the Workshop's fuel maths
import agent  # noqa: E402

setcfg(common_spares=True)
fake = type("Ctx", (), {"my_offers": [{"give": {"assets": [{"id": 3}]}}], "threads": [], "values": None,
                        "me": {"assets": ASSETS}, "state": {}, "peer_busy_assets": lambda self: set()})()
r.append(check("locked = promised + reserved", agent.Context.locked_assets(fake) == {1, 2, 3}, agent.Context.locked_assets(fake)))
r.append(check("reserved=False: only what is promised", agent.Context.locked_assets(fake, reserved=False) == {3}))

# the Workshop records what it pulls
import workshop  # noqa: E402
from values import Values  # noqa: E402

CAT = {"values": {"copy_marginals": [1.0, 0.25, 0.1], "page_bonus": 0.25, "master_bonus": 0.1}, "sets": [
    {"id": "LAT", "released": True, "cards": [{"id": f"LAT-0{i}", "book": 10, "rarity": "common", "page": False} for i in (1, 2, 3)]},
    {"id": "SAL", "released": True, "cards": [{"id": "SAL-06", "book": 25, "rarity": "uncommon", "page": False}]}]}
held = [{"id": n, "kind": "card", "ref": ref, "serial": s} for n, (ref, s) in enumerate(
    [("LAT-01", 1), ("LAT-01", 2), ("LAT-02", 1), ("LAT-02", 2), ("LAT-03", 1), ("LAT-03", 2)], start=10)]
ctx = type("Ctx", (), {})()
ctx.values, ctx.S, ctx.state, ctx.clock = Values(CAT, {"affinity": {"LAT": 0.5, "SAL": 1.6}, "assets": held}), {"workshop_edge": 2}, {}, {"tick": 5}
ctx.log = lambda *a, **k: None
ctx.locked_assets = lambda reserved=True: set()
ctx.api = type("Api", (), {"taller": staticmethod(lambda ids: {"card": {"id": 999, "ref": "SAL-06", "rarity": "uncommon"}, "burned": ids})})()
workshop.Workshop(ctx).step()
r.append(check("the card the Workshop gives us is recorded as crafted (reserved)", ctx.state.get("crafted_assets") == [999], ctx.state))

# the dashboard's auto-swaps skip reserved cards
import swaps  # noqa: E402

auto = swaps.Auto(on=True)
auto.reserved = {2}
why = auto.check({"asset": 2, "team": "t05", "want": "RET-01", "ours": 50, "theirs": 50, "both": True}, 0.0, [], "t13", 3)
r.append(check("auto-swap never sends a reserved card", why and "reserved" in why, why))

print(f"\n{sum(r)}/{len(r)} passed")
sys.exit(0 if all(r) else 1)
