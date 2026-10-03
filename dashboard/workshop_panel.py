"""Workshop tab: fuel, ladder, next craft, and pull history (mirrors team13/workshop.py + Values)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "team13"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from values import Values  # noqa: E402

_spec = importlib.util.spec_from_file_location("agent_workshop", ROOT / "workshop.py")
_agent_ws = importlib.util.module_from_spec(_spec)
assert _spec.loader
_spec.loader.exec_module(_agent_ws)
NEXT, choose, spare_copies = _agent_ws.NEXT, _agent_ws.choose, _agent_ws.spare_copies

RARITIES = ("common", "uncommon", "rare", "epic", "legendary")


def _locked(me_id, offers, threads, agent_state) -> set:
    ids: set = set()
    for o in offers or []:
        st = o.get("status") or o.get("state")
        if st not in (None, "open", "queued"):
            continue
        for a in (o.get("give") or {}).get("assets") or []:
            ids.add(a["id"] if isinstance(a, dict) else a)
    for th in threads or []:
        if th.get("status") != "open":
            continue
        topic = (th.get("topic") or {}).get("sell") or {}
        if topic.get("assets"):
            ids.update(topic["assets"])
        elif topic.get("asset"):
            ids.add(topic["asset"])
    flip = (agent_state or {}).get("flip") or {}
    if flip.get("asset"):
        ids.add(flip["asset"])
    for loan in ((agent_state or {}).get("loans") or {}).values():
        if loan.get("status") in ("active", "collateral_held") and loan.get("asset"):
            ids.add(loan["asset"])
    return ids


def _level(levels_payload) -> dict | None:
    for l in (levels_payload or {}).get("levels") or []:
        if l.get("id") == "taller" or l.get("kind") == "taller":
            return l
    return None


def view(me, catalog, offers, threads, levels, strategy_s: dict, agent_state: dict, decisions: list) -> dict:
    """Everything the Workshop tab needs; recomputed when the dashboard cache changes."""
    if not (isinstance(me, dict) and me.get("assets") is not None and isinstance(catalog, dict) and "sets" in catalog):
        return None
    v = Values(catalog, me)
    locked = _locked(me.get("id"), (offers or {}).get("offers") if isinstance(offers, dict) else offers, threads, agent_state)
    edge = float((strategy_s or {}).get("workshop_edge", 2))
    reserve = int((strategy_s or {}).get("workshop_spares", 0))
    enable = int((strategy_s or {}).get("enable_workshop", 1))

    plan = choose(v, locked, edge) if enable else None

    fuel_assets = spare_copies(v, locked)
    by_rar: dict = {r: [] for r in RARITIES}
    for a in fuel_assets:
        ref = a["ref"]
        if ref not in v.cards:
            continue
        loss = v.loss_of_removing([ref])
        by_rar[v.cards[ref]["rarity"]].append({
            "id": a["id"], "ref": ref, "serial": a.get("serial"), "set": v.cards[ref]["set"],
            "loss": round(loss, 2) if loss != float("inf") else None,
            "listed": a["id"] in locked,
        })

    rungs = []
    for rar in RARITIES[:-1]:
        nxt = NEXT.get(rar)
        pool = [c["id"] for c in v.cards.values()
                if c.get("released", True) and not c.get("hidden") and c["rarity"] == nxt]
        group = by_rar.get(rar, [])
        n = len(group)
        trios = n // 3
        ev = None
        if pool:
            ev = round(sum(v.gain_of_adding([r]) for r in pool) / len(pool), 2)
        loss3 = None
        surplus = None
        if n >= 3:
            pick = sorted(group, key=lambda x: (x["loss"] or 0, -x.get("serial") or 0))[:3]
            refs = [x["ref"] for x in pick]
            loss3 = v.loss_of_removing(refs)
            if loss3 != float("inf") and ev is not None:
                surplus = round(ev - loss3, 2)
        rungs.append({
            "rarity": rar, "next": nxt, "fuel": n, "trios": trios,
            "need": max(0, 3 - n), "ev_next": ev, "loss_three": round(loss3, 2) if loss3 not in (None, float("inf")) else None,
            "surplus": surplus, "craft_ok": surplus is not None and surplus + 1e-9 >= edge,
            "best": plan is not None and plan.get("rarity") == rar,
        })

    stockpile = []
    for a in v.workshop_held(reserve):
        ref = a["ref"]
        stockpile.append({"id": a["id"], "ref": ref, "serial": a.get("serial"),
                          "loss": round(v.loss_of_removing([ref]), 2)})

    logs = []
    for rec in reversed(decisions or []):
        if rec.get("module") != "workshop":
            continue
        logs.append({
            "tick": rec.get("tick"), "action": rec.get("action"),
            "refs": rec.get("refs"), "rarity": rec.get("rarity"), "next": rec.get("next"),
            "loss": rec.get("loss"), "ev": rec.get("ev"), "surplus": rec.get("surplus"),
            "pulled": rec.get("pulled"), "pulled_rarity": rec.get("pulled_rarity"),
            "error": rec.get("error"), "luck": rec.get("luck"),
        })
        if len(logs) >= 25:
            break

    lvl = _level(levels)
    dup_refs = sum(1 for ref, n in v.held.items() if n >= 2)
    dup_assets = sum(max(0, n - 1) for n in v.held.values())

    status = "idle"
    if not enable:
        status = "disabled"
    elif lvl and lvl.get("state") != "active":
        status = "closed"
    elif plan:
        status = "ready"
    elif any(r["fuel"] >= 1 for r in rungs) or (lvl and lvl.get("state") == "active"):
        status = "stocking"

    return {
        "status": status,
        "level": {"name": lvl.get("name") if lvl else "The Workshop", "state": (lvl or {}).get("state"),
                  "teaser": (lvl or {}).get("teaser"), "how": (lvl or {}).get("how")},
        "knobs": {"enable": enable, "edge": edge, "reserve": reserve},
        "plan": plan,
        "rungs": rungs,
        "fuel_by_rarity": by_rar,
        "fuel_total": len(fuel_assets),
        "stockpile": stockpile,
        "duplicates": {"refs": dup_refs, "extra_copies": dup_assets},
        "log": logs,
    }
