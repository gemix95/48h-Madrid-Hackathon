"""El Consejo without git or network: notes say which agent wrote them, deals are announced, every laptop's file is
read back once, and a new agent never takes an id someone used in the last day.

    python3 tests/test_council.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import council  # noqa: E402

tmp = Path(tempfile.mkdtemp())
council.SPOOL, council.NOTES, council.BOARD = tmp / "spool", tmp / "board" / "notes", tmp / "legacy.jsonl"
council.sync = lambda pull_only=False: True  # no git here
council.NOTES.mkdir(parents=True)


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    return ok


results = []
# another laptop's agent posted yesterday-ish (Fugger, 2 h ago) and long ago (Medici, 3 days ago)
(council.NOTES / "Fugger@laptop2.jsonl").write_text(json.dumps(
    {"ts": time.time() - 7200, "author": "haggler", "topic": "deal", "lesson": "x", "agent": "Fugger", "role": "dealers"}) + "\n")
(council.NOTES / "Medici@laptop3.jsonl").write_text(json.dumps(
    {"ts": time.time() - 3 * 86400, "author": "trader", "topic": "deal", "lesson": "y", "agent": "Medici", "role": "market"}) + "\n")
council.BOARD.write_text(json.dumps({"ts": 1, "author": "tuner", "topic": "health", "lesson": "old main-branch note"}) + "\n")

for _ in range(30):  # ids are random: never one used in the last day
    name = council.identify("market")
    if name == "Fugger":
        break
results.append(check("a new id is never one used in the last day", name != "Fugger", name))
results.append(check("ids are famous businesspeople", name in council.NAMES, name))

council.WHO.update(writer="Rockefeller", agent="Rockefeller", role="dealers")
n = council.post("Admin", "rivals", "t04 opens at 18")
results.append(check("note says agent, role and host", n["agent"] == "Rockefeller" and n["role"] == "dealers" and n["host"] == council.HOST, n))
results.append(check("spooled in this writer's own file", (council.SPOOL / f"Rockefeller@{council.HOST}.jsonl").exists()))

council.announce({"module": "haggle", "action": "opened", "tick": 400, "dealer": "abuela",
                  "plan": {"side": "buy", "key": "abuela:buy:sobre_barrio", "lo": 5, "hi": 10}}, {"abuela": "Abuela"})
council.announce({"module": "haggle", "action": "deal", "tick": 405, "key": "abuela:buy:sobre_barrio", "price": 9, "rounds": 5}, {"abuela": "Abuela"})
council.announce({"module": "trade", "action": "list_ask", "tick": 405, "ref": "LAT-02"})  # not a deal: silent
notes = council.read(n=50)
deals = [x for x in notes if x["topic"] == "deal" and x.get("agent") == "Rockefeller"]
results.append(check("opening a dealer haggle is announced", any("Negotiating with Abuela: buy sobre barrio, opening 5 P, cap 10 P" in x["lesson"] for x in deals), [x["lesson"] for x in deals]))
results.append(check("a closed deal is announced", any("Deal with Abuela: sobre barrio for 9 P after 5 offers" in x["lesson"] for x in deals)))
results.append(check("listings are not announced", len(deals) == 2, len(deals)))
results.append(check("old main-branch notes still read", any(x["lesson"] == "old main-branch note" for x in notes)))
results.append(check("every laptop's file read, oldest first", [x["ts"] for x in notes] == sorted(x["ts"] for x in notes)))
results.append(check("each note read once", len(notes) == len({(x["ts"], x["lesson"]) for x in notes}), len(notes)))

# peer coordination: another host's open haggle blocks the same plan key
other = council.SPOOL / "Walton@MacBook-Pro-de-Sergio.jsonl"
other.write_text(json.dumps({"ts": time.time(), "author": "haggler", "topic": "deal", "host": "MacBook-Pro-de-Sergio",
                             "lesson": "Negotiating with Chato: buy rare, opening 40 P, cap 60 P.",
                             "evidence": {"key": "chato:buy:rare"}, "agent": "Walton"}) + "\n")
results.append(check("peer_claims sees another agent's open deal",
                     "chato:buy:rare" in council.peer_claims(exclude_agent="Rockefeller")))
other.write_text(other.read_text() + json.dumps({"ts": time.time() + 1, "author": "haggler", "topic": "deal",
                                                 "host": "MacBook-Pro-de-Sergio",
                                                 "lesson": "Deal with Chato: rare for 45 P after 3 offers.",
                                                 "evidence": {"key": "chato:buy:rare"}, "agent": "Walton"}) + "\n")
results.append(check("peer_claims clears after deal note",
                     "chato:buy:rare" not in council.peer_claims(exclude_agent="Rockefeller")))
(council.SPOOL / "Walton@MacBook-Pro-de-Sergio.jsonl").write_text(json.dumps(
    {"ts": time.time(), "author": "haggler", "topic": "deal", "host": "PPFL2214GH",
     "lesson": "Negotiating with Chato: buy rare, opening 40 P, cap 60 P.",
     "evidence": {"key": "chato:buy:rare"}, "agent": "Walton", "role": "market"}) + "\n")
results.append(check("same host, other role: dealers sees market agent's claim",
                     "chato:buy:rare" in council.peer_claims(exclude_agent="Pulitzer")))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
