"""Manual negotiation helper: show a thread, say a price, accept an offer. Every call is logged to logs/hand.jsonl.

    python3 agent/hand.py show 16
    python3 agent/hand.py say 16 12 "Hola Carmen, would 12 be all right?"
    python3 agent/hand.py accept 123
    python3 agent/hand.py open abuela '{"buy": {"card": "SAL-06"}}'
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bazaar-kit"))
from bazaar_sdk import Bazaar, BazaarError  # noqa: E402

LOG = os.path.join(os.path.dirname(__file__), "..", "logs", "hand.jsonl")
b = Bazaar(os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai"), os.environ["BAZAAR_KEY"], wait_on_tick=False)


def log(ev: str, **kw) -> None:
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps({"ts": time.time(), "ev": ev, **kw}) + "\n")


def show(tid: int) -> None:
    t = b.thread(tid)
    clock = b.clock()
    print(f"thread {t['id']} with {t['with']} topic={t['topic']} status={t['status']} "
          f"closed_reason={t.get('closed_reason')} item={t.get('item')} | tick {clock['tick']} paused={clock['paused']}")
    for m in t["messages"]:
        print(f"  [{m.get('tick')}] {m.get('author') or m.get('from')}: price={m.get('price')} id={m.get('id')} :: {m.get('text')}")
    for o in t["standing_offers"]:
        print(f"  OFFER id={o['id']} maker={o['maker']} status={o['status']} final={o.get('final')} "
              f"give={o.get('give')} want={o.get('want')}")
    log("show", thread=t)


def main() -> None:
    cmd, args = sys.argv[1], sys.argv[2:]
    try:
        if cmd == "show":
            show(int(args[0]))
        elif cmd == "say":
            r = b.say(int(args[0]), args[2], price=int(args[1]))
            log("say", thread=int(args[0]), price=int(args[1]), text=args[2], r=r)
            show(int(args[0]))
        elif cmd == "accept":
            r = b.accept(int(args[0]))
            log("accept", offer=int(args[0]), r=r)
            print(json.dumps(r)[:800])
        elif cmd == "open":
            r = b.open_thread(args[0], topic=json.loads(args[1]))
            log("open", r=r)
            show(r["id"])
        elif cmd == "close":
            log("close", r=b.close_thread(int(args[0])))
    except BazaarError as e:
        log("error", cmd=cmd, args=args, code=e.code, message=e.message)
        print(f"ERROR {e.code}: {e.message}")


if __name__ == "__main__":
    main()
