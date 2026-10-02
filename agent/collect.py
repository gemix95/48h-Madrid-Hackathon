"""Collect every public feed event (other teams' messages to dealers, offers, settlements) into logs/feed.jsonl,
deduplicated by event id. Safe to restart: it reloads the ids it already has.

    python3 agent/collect.py [poll_seconds]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bazaar-kit"))
from bazaar_sdk import Bazaar  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "logs", "feed.jsonl")
POLL = float(sys.argv[1]) if len(sys.argv) > 1 else 10
b = Bazaar(os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai"), os.environ["BAZAAR_KEY"], wait_on_tick=False)

os.makedirs(os.path.dirname(OUT), exist_ok=True)
seen = set()
if os.path.exists(OUT):
    with open(OUT) as f:
        seen = {json.loads(line)["id"] for line in f if line.strip()}

while True:
    try:
        events = b.feed(1000).get("events", [])
        fresh = [e for e in events if e["id"] not in seen]
        with open(OUT, "a") as f:
            for e in sorted(fresh, key=lambda e: e["id"]):
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
                seen.add(e["id"])
        if events and fresh and len(fresh) == len(events) and len(seen) > len(fresh):
            print(f"GAP: feed window fully new ({len(fresh)} events), some events may be missed; poll faster", flush=True)
    except Exception as e:  # keep collecting through transient errors
        print(f"COLLECT-ERROR {type(e).__name__}: {e}", flush=True)
    time.sleep(POLL)
