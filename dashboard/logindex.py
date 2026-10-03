"""Who sent what with our team key, and what the guard cancelled and why.

Every bot of ours writes a JSONL log on the machine it runs on: on the server, team13/logs/decisions.jsonl for the
three agents (each line says which one, `"agent": "sergio"`), team13/logs/sell.jsonl and injection.jsonl for the
scripts; logs/hand.jsonl for agent/hand.py. This reads them incrementally and answers two questions for the dashboard:

  origin(thread, text, price) -> "agent:<name>" | "agent" | "script" | "manual" | "other"
      "other" means no log here has this message: something else wrote it with our key.
  guard                      -> the offers team13/guard.py cancelled, each with the rule that fired (`why`).

The logs only exist where the bots run (the server). A laptop dashboard reads the server's answers instead
(DASHBOARD_REMOTE, see dashboard/server.py); with no live agent log here it shows no origin labels rather than
calling every message "other".
"""
import json
import threading
import time
from pathlib import Path

# log records that are a message we sent (not a price we merely saw)
SENT = {"agent": {"offer", "haggle_offer", "counter_team", "invited"}, "manual": {"say"},
        "script": {"thread_offer", "thread_note", "sent"}}
STALE = 1800  # seconds: an agent log with no line for this long belongs to no running agent (an old laptop log)


def _norm(text) -> str:
    """The server cleans control characters; compare the first words with whitespace squeezed."""
    return " ".join(str(text or "").split())[:60]


def _price(r: dict):
    if isinstance(r.get("price"), (int, float)):
        return r["price"]
    offer = r.get("offer")  # counter_team logs the structured offer instead of a price
    if isinstance(offer, dict):
        return (offer.get("want") or {}).get("cash") or (offer.get("give") or {}).get("cash") or None
    return None


class LogIndex:
    def __init__(self, sources: dict, keep: int = 200):
        """sources: {"agent": path to decisions.jsonl, "manual": path to hand.jsonl, "script": [paths], ...}
        A source may list several files; each remembers who sent what: {text: who}, {(thread, price): who}."""
        self.s = {}
        for name, paths in sources.items():
            for i, p in enumerate(paths if isinstance(paths, (list, tuple)) else [paths]):
                self.s[name if not i else f"{name}#{i}"] = {"name": name, "path": Path(p), "pos": 0, "lines": 0,
                                                            "last_ts": None, "texts": {}, "pairs": {}, "bare": {}}
        self.guard, self.keep, self.lock = [], keep, threading.Lock()

    def refresh(self) -> None:
        """Read what each log gained since the last call (the whole file the first time)."""
        with self.lock:
            for name, s in self.s.items():
                try:
                    size = s["path"].stat().st_size
                except OSError:
                    continue  # no log on this laptop
                if size < s["pos"]:  # truncated or replaced: start over
                    s.update(pos=0, lines=0, last_ts=None, texts={}, pairs={}, bare={})
                    if name == "agent":
                        self.guard.clear()
                if size == s["pos"]:
                    continue
                with s["path"].open("rb") as f:
                    f.seek(s["pos"])
                    chunk = f.read(size - s["pos"])
                end = chunk.rfind(b"\n") + 1  # a half-written last line waits for the next call
                s["pos"] += end
                for line in chunk[:end].splitlines():
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(r, dict):
                        self._add(name, s, r)

    def _add(self, name: str, s: dict, r: dict) -> None:
        name = s["name"]
        s["lines"] += 1
        s["last_ts"] = r.get("ts", s["last_ts"])
        if name == "agent" and r.get("module") == "guard" and r.get("action") == "cancelled":
            self.guard.append({k: r.get(k) for k in ("ts", "tick", "offer", "thread", "why", "give", "want", "loss", "gain")})
            del self.guard[:-self.keep]
            return
        if (r.get("action") or r.get("ev") or r.get("event")) not in SENT.get(name, ()):
            return
        who = f"{name}:{r['agent']}" if name == "agent" and r.get("agent") else name  # which of the three agents
        if r.get("text"):
            s["texts"][_norm(r["text"])] = who
        price = _price(r)
        if r.get("thread") is not None and price is not None:
            s["pairs"][(r["thread"], price)] = who
        elif r.get("thread") is not None and not r.get("text"):
            s["bare"][r["thread"]] = who  # e.g. the matchmaker's invitation: its own thread, no price or text logged

    def origin(self, thread, text, price) -> str:
        """Text first (it is distinctive), then thread + price, then a thread only one sender of ours opened."""
        with self.lock:
            t = _norm(text)
            for key in ("texts", "pairs", "bare"):
                probe = t if key == "texts" else (thread, price) if key == "pairs" else thread
                if (key == "texts" and not t) or (key == "pairs" and price is None):
                    continue
                for s in self.s.values():
                    if probe in s[key]:
                        return s[key][probe]
        return "other"

    def live(self) -> bool:
        """An agent log written to lately: the agents run here (the server), so a message missing from it is "other"."""
        with self.lock:
            return any(s["name"] == "agent" and s["lines"] and (s["last_ts"] or 0) > time.time() - STALE
                       for s in self.s.values())

    def status(self) -> dict:
        with self.lock:
            return {name: {"lines": s["lines"], "last_ts": s["last_ts"]} for name, s in self.s.items()}

    def recent_guard(self, n: int = 100) -> list:
        with self.lock:
            return list(self.guard[-n:])


def message_origins(index: LogIndex, threads: dict, me_id: str) -> dict:
    """{message id: origin} for every message of ours in the /api/me/threads payload.
    Empty when no agent writes its log here (a laptop with no log, or an old one from before the agents moved to the
    server): there is nothing current to compare against, so nothing is called "other"."""
    if not index.live():
        return {}
    out = {}
    for th in (threads or {}).get("threads", []):
        for m in th.get("messages", []):
            if m.get("sender") != me_id or m.get("id") is None:
                continue
            o = m.get("offer") or {}
            price = m.get("price") or (o.get("give") or {}).get("cash") or (o.get("want") or {}).get("cash") or None
            out[m["id"]] = index.origin(th.get("id"), m.get("text"), price)
    return out
