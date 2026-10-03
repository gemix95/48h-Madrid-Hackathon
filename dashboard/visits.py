"""Our own visit counter for the public board, instead of Google Analytics: no cookies, no third party, no IP kept.
A visitor is a salted hash of IP + user agent that changes every day, enough to count people, not to follow them.

Events the page sends (navigator.sendBeacon to /board/ping): view (with phone or desktop), click (a trade button:
card, side, label), copy (the request was copied: card, side). One JSON line each in team13/logs/board_hits.jsonl.
"""
from __future__ import annotations

import collections
import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path

HITS = Path(os.environ.get("BOARD_HITS", Path(__file__).resolve().parent.parent / "team13" / "logs" / "board_hits.jsonl"))
EVENTS = {"view", "click", "copy"}
SALT = secrets.token_hex(16)  # new at every restart: the hashes cannot be matched to anything outside this process
_lock = threading.Lock()


def _who(ip: str, ua: str) -> str:
    day = time.strftime("%Y-%m-%d")
    return hashlib.sha256(f"{SALT}|{day}|{ip}|{ua}".encode()).hexdigest()[:12]


def record(q: dict, ip: str, ua: str) -> bool:
    """Append one event from the page's query. Returns False when it is not one we count."""
    ev = q.get("e")
    if ev not in EVENTS:
        return False
    rec = {"ts": round(time.time(), 1), "e": ev, "v": _who(ip, ua),
           "phone": q.get("m") == "1", "ref": str(q.get("ref") or "")[:12] or None,
           "side": q.get("side") if q.get("side") in ("buy", "sell", "lot", "auction") else None,
           "label": str(q.get("l") or "")[:24] or None}
    with _lock, open(HITS, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return True


def summary(since: float = 0) -> dict:
    """Visitors, views, clicks and copies, in total and per hour, the phone share, and the cards people click most."""
    try:
        rows = [json.loads(line) for line in open(HITS) if line.strip()]
    except OSError:
        rows = []
    rows = [r for r in rows if r["ts"] >= since]
    per_hour = collections.defaultdict(lambda: {"visitors": set(), "view": 0, "click": 0, "copy": 0})
    for r in rows:
        h = per_hour[int(r["ts"] // 3600)]
        h["visitors"].add(r["v"])
        h[r["e"]] += 1
    views = [r for r in rows if r["e"] == "view"]
    cards = collections.Counter(f'{r["ref"]} {r["side"]}' for r in rows if r["e"] in ("click", "copy") and r["ref"])
    return {"visitors": len({r["v"] for r in rows}), "views": len(views),
            "clicks": sum(r["e"] == "click" for r in rows), "copies": sum(r["e"] == "copy" for r in rows),
            "phone_share": round(sum(r["phone"] for r in views) / len(views), 2) if views else None,
            "top_cards": cards.most_common(10),
            "per_hour": {time.strftime("%a %H:00", time.localtime(k * 3600)): {**v, "visitors": len(v["visitors"])}
                         for k, v in sorted(per_hour.items())}}
