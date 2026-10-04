"""Data for the "Sergio's agent" tab: one teammate's agent (AGENT_NAME) on its own.

From the server's files, read-only:
  - its decisions: the decisions.jsonl lines with "agent": <name>, read incrementally (about the last 12 MB at start);
    the noisy ones (a skipped sale, a bid re-listed) are only counted;
  - what it is doing now: its state-<name>.json (open dealer plans, team haggles, the offers it listed);
  - its settings from the repo: team13/agents.json;
  - the plays done by hand on its behalf: logs/hand.jsonl lines whose "by" starts with "<name>-" (a server script).
"""
from __future__ import annotations

import collections
import json
import threading
import time
from pathlib import Path

TEAM13 = Path(__file__).resolve().parent.parent / "team13"
DECISIONS = TEAM13 / "logs" / "decisions.jsonl"
START_BYTES = 12_000_000
QUIET = {"sale_skipped_below_value", "bid_dropped", "stale_bid_dropped", "skip_foreign_thread", "announced", "egg_line",
         "skip_peer_deal", "listing_gone", "reprice_cancel", "sell_floor_raised", "skip_insolvent", "skip_after_check"}
# what ended: a deal, a walk-away, a conversation closed
OUTCOMES = {"haggle.deal", "haggle.accept", "haggle.ended", "haggle.moved_on", "haggle.final_declined",
            "trade.accept", "trade.accept_team", "trade.haggle_closed", "trade.haggle_ended",
            "arb.bought", "arb.sold", "arb.resold", "arb.gave_up", "arb.dealer_walked", "arb.stuck_with_card",
            "manual.accept", "manual.accepted", "manual.workshop", "manual.walk", "manual.ended"}
HAND = TEAM13.parent / "logs" / "hand.jsonl"


def _hand(name: str) -> list:
    """Plays done by hand for this agent's owner (a server script logging "by": "<name>-..."), as decision records."""
    out = []
    try:
        for line in HAND.read_text().splitlines()[-3000:]:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if str(r.get("by") or "").startswith(f"{name}-"):
                out.append({**r, "module": "manual", "action": r.get("ev"), "agent": name})
    except OSError:
        pass
    return out

_lock = threading.Lock()
_m = {"name": None, "pos": None, "recs": collections.deque(maxlen=300), "outcomes": collections.deque(maxlen=200),
      "quiet": collections.Counter()}


def _read(name: str) -> None:
    m, path = _m, DECISIONS
    size = path.stat().st_size
    if m["name"] != name or m["pos"] is None or size < m["pos"]:  # first read, another agent, or a new log file
        m.update(name=name, pos=max(0, size - START_BYTES))
        m["skip"] = m["pos"] > 0  # we start in the middle of a line
        for k in ("recs", "outcomes", "quiet"):
            m[k].clear()
    if size <= m["pos"]:
        return
    with path.open("rb") as f:
        f.seek(m["pos"])
        data = f.read(size - m["pos"])
    end = data.rfind(b"\n") + 1  # a line still being written waits for the next read
    if not end:
        return
    body, m["pos"] = data[:end], m["pos"] + end
    if m.pop("skip", False):
        body = body[body.find(b"\n") + 1:]
    tag = f'"agent": "{name}"'.encode()
    for line in body.splitlines():
        if tag not in line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("agent") != name:
            continue
        key = f"{r.get('module')}.{r.get('action')}"
        if r.get("action") in QUIET:
            m["quiet"][key] += 1
            continue
        m["recs"].append(r)
        if key in OUTCOMES:
            m["outcomes"].append(r)


def view(name: str) -> dict:
    name = "".join(ch for ch in (name or "").lower() if ch.isalnum() or ch in "-_")[:20] or "sergio"
    with _lock:
        try:
            _read(name)
        except OSError:
            pass
        recs, outcomes, quiet = list(_m["recs"]), list(_m["outcomes"]), dict(_m["quiet"])
    hand = _hand(name)
    by_ts = lambda r: r.get("ts") or 0
    recs = sorted(recs + hand, key=by_ts)[-300:]
    outcomes = sorted(outcomes + [r for r in hand if f"manual.{r['action']}" in OUTCOMES], key=by_ts)[-200:]
    path = TEAM13 / f"state-{name}.json"
    try:
        st, age = json.loads(path.read_text()), round(time.time() - path.stat().st_mtime)
    except (OSError, ValueError):
        st, age = {}, None
    try:
        over = json.loads((TEAM13 / "agents.json").read_text()).get(name) or {}
    except (OSError, ValueError, AttributeError):
        over = {}
    return {"name": name, "at": time.time(), "state_age": age, "overrides": over,
            "decisions": recs, "outcomes": outcomes, "quiet": quiet,
            "plans": {k: v for k, v in (st.get("plans") or {}).items() if not v.get("done")},
            "team_haggles": st.get("team_haggles") or {}, "listings": st.get("listings") or {},
            "arb": (st.get("arb") or {}).get("active")}
