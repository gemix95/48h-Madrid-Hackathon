"""Cards reserved for swap strategies: no module of the agent (trader, haggler, wtb, tapas, market rewards, workshop)
and no dashboard auto-swap sells, offers, swaps or burns them. We trade them by hand (swaps with other teams,
Workshop trios, selling the uncommons that come out).

team13/reserved.json, read whenever it changes (edit, push: the server deploys it within a minute):
  refs           every copy of these cards
  assets         these asset ids (e.g. a card crafted by hand at The Workshop)
  common_spares  every copy of a common we hold twice or more: Workshop and swap material
  crafted        every card The Workshop gave the agent (state["crafted_assets"], written by workshop.py)
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

PATH = Path(__file__).parent / "reserved.json"
_cache: dict = {"mtime": None, "cfg": {}}


def load() -> dict:
    try:
        m = PATH.stat().st_mtime
    except OSError:
        return {}
    if m != _cache["mtime"]:
        try:
            _cache["cfg"] = json.loads(PATH.read_text())
        except ValueError:
            pass  # a typo keeps the last good list instead of releasing every reserved card
        _cache["mtime"] = m
    return _cache["cfg"] or {}


def reserved_ids(assets, state=None) -> set:
    """Asset ids reserved for swap strategies among `assets` (our cards, as /api/me lists them)."""
    cfg = load()
    cards = [a for a in assets or [] if a.get("kind", "card") == "card" and a.get("id") is not None]
    refs = set(cfg.get("refs") or [])
    ids = {int(x) for x in cfg.get("assets") or []}
    ids |= {a["id"] for a in cards if a.get("ref") in refs}
    if cfg.get("common_spares"):
        n = Counter(a.get("ref") for a in cards if a.get("rarity") == "common")
        ids |= {a["id"] for a in cards if a.get("rarity") == "common" and n[a.get("ref")] >= 2}
    if cfg.get("crafted"):
        ids |= {int(x) for x in (state or {}).get("crafted_assets") or []}
    return ids
