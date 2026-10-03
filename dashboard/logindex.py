"""Who sent what with our team key, and what the guard cancelled and why.

Every bot of ours writes a JSONL log on the laptop it runs on (team13/logs/decisions.jsonl for the agent,
logs/hand.jsonl for agent/hand.py). This reads them incrementally and answers two questions for the dashboard:

  origin(thread, text, price) -> "agent" | "manual" | "other"
      "other" means no local log has this message: another program, or another laptop, wrote it.
  guard                      -> the offers team13/guard.py cancelled, each with the rule that fired (`why`).

The logs only exist on the laptop that runs the bots. On any other laptop `status()` reports zero lines, and the
dashboard shows no origin labels rather than calling every message "other".
"""
import json
import threading
from pathlib import Path

# log records that are a message we sent (not a price we merely saw)
SENT = {"agent": {"offer", "haggle_offer", "counter_team", "invited"}, "manual": {"say"}}


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
        """sources: {"agent": path to decisions.jsonl, "manual": path to hand.jsonl}"""
        self.s = {name: {"path": Path(p), "pos": 0, "lines": 0, "last_ts": None, "texts": set(), "pairs": set()}
                  for name, p in sources.items()}
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
                    s.update(pos=0, lines=0, last_ts=None, texts=set(), pairs=set())
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
        s["lines"] += 1
        s["last_ts"] = r.get("ts", s["last_ts"])
        if name == "agent" and r.get("module") == "guard" and r.get("action") == "cancelled":
            self.guard.append({k: r.get(k) for k in ("ts", "tick", "offer", "thread", "why", "give", "want", "loss", "gain")})
            del self.guard[:-self.keep]
            return
        if (r.get("action") or r.get("ev")) not in SENT.get(name, ()):
            return
        if r.get("text"):
            s["texts"].add(_norm(r["text"]))
        price = _price(r)
        if r.get("thread") is not None and price is not None:
            s["pairs"].add((r["thread"], price))

    def origin(self, thread, text, price) -> str:
        """Text first (it is distinctive), then thread + price; the agent before the manual tool."""
        with self.lock:
            t = _norm(text)
            for name, s in self.s.items():
                if t and t in s["texts"]:
                    return name
            for name, s in self.s.items():
                if price is not None and (thread, price) in s["pairs"]:
                    return name
        return "other"

    def status(self) -> dict:
        with self.lock:
            return {name: {"lines": s["lines"], "last_ts": s["last_ts"]} for name, s in self.s.items()}

    def recent_guard(self, n: int = 100) -> list:
        with self.lock:
            return list(self.guard[-n:])


def message_origins(index: LogIndex, threads: dict, me_id: str) -> dict:
    """{message id: origin} for every message of ours in the /api/me/threads payload.
    Empty when this laptop has no agent log: there is nothing to compare against, so nothing is called "other"."""
    if not index.status().get("agent", {}).get("lines"):
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
