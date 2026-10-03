"""Who sent each of our messages: the agent by name, a server script, by hand, or something else.

    python3 tests/test_logindex.py      (no network)
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "dashboard"))
from logindex import LogIndex, message_origins  # noqa: E402


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    return ok


def write(path, recs):
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))


tmp, now = Path(tempfile.mkdtemp()), time.time()
write(tmp / "decisions.jsonl", [
    {"ts": now - 60, "module": "haggle", "action": "offer", "thread": 1888, "price": 31, "text": "Qué detalle tan bonito", "agent": "sergio"},
    {"ts": now - 50, "module": "trade", "action": "counter_team", "thread": 1900, "offer": {"want": {"cash": 40}}, "agent": "emmanuele"},
    {"ts": now - 40, "module": "matchmaker", "action": "invited", "team": "t03", "thread": 1911, "agent": "anton"},
    {"ts": now - 30, "module": "haggle", "action": "offer", "thread": 5, "price": 9, "text": "old laptop line"},
])
write(tmp / "sell.jsonl", [{"ts": now - 20, "event": "thread_offer", "thread": 1700, "price": 150}])
idx = LogIndex({"agent": tmp / "decisions.jsonl", "manual": tmp / "hand.jsonl", "script": [tmp / "sell.jsonl", tmp / "none.jsonl"]})
idx.refresh()
threads = {"threads": [
    {"id": 1888, "messages": [{"id": 1, "sender": "t13", "text": "Qué  detalle tan bonito", "price": 31}]},
    {"id": 1900, "messages": [{"id": 2, "sender": "t13", "text": "", "offer": {"want": {"cash": 40}}}]},
    {"id": 1911, "messages": [{"id": 3, "sender": "t13", "text": "Hi from Team 13's market"}]},
    {"id": 1700, "messages": [{"id": 4, "sender": "t13", "text": "", "price": 150}]},
    {"id": 7, "messages": [{"id": 5, "sender": "t13", "text": "nobody logged this", "price": 12}, {"id": 6, "sender": "abuela", "text": "hola"}]},
    {"id": 5, "messages": [{"id": 7, "sender": "t13", "text": "old laptop line", "price": 9}]},
]}
o = message_origins(idx, threads, "t13")
results = [
    check("the agent by name, from the text", o.get(1) == "agent:sergio", o.get(1)),
    check("the agent by name, from thread + price", o.get(2) == "agent:emmanuele", o.get(2)),
    check("the matchmaker's invitation, from its own thread", o.get(3) == "agent:anton", o.get(3)),
    check("a server script", o.get(4) == "script", o.get(4)),
    check("nobody's log: other", o.get(5) == "other", o.get(5)),
    check("an agent line without a name stays 'agent'", o.get(7) == "agent", o.get(7)),
    check("dealer messages get no label", 6 not in o),
]

# an old laptop log (no line for an hour): no labels at all, rather than "not the agent" on everything
write(tmp / "old.jsonl", [{"ts": now - 3600, "module": "haggle", "action": "offer", "thread": 5, "price": 9, "text": "x"}])
old = LogIndex({"agent": tmp / "old.jsonl"})
old.refresh()
results.append(check("stale agent log: no origins", message_origins(old, threads, "t13") == {}))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
