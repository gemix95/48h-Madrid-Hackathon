"""One-off haggle with El Chato for one card, cap from argv. Decisions use the offer structure only.

    python3 agent/chato_once.py MAL-09 75 58
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bazaar-kit"))
from bazaar_sdk import Bazaar, BazaarError

REF, CAP, OPEN = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
DEALER, POLL = "chato", 3
LOG = os.path.join(os.path.dirname(__file__), "..", "logs", "hand.jsonl")
b = Bazaar(os.environ["BAZAAR_URL"], os.environ["BAZAAR_KEY"], wait_on_tick=False)
TEXTS = ["Good evening. We would like {ref} for our Malasaña page. Would you take {p} primas?",
         "Understood. We can come up to {p}.",
         "Fair enough. {p} primas, then.",
         "We are close. {p}?",
         "{p} is as far as our budget goes tonight."]


def log(**k):
    with open(LOG, "a") as f:
        f.write(json.dumps({"ts": time.time(), "who": "chato_once", **k}) + "\n")
    print(json.dumps(k)[:260], flush=True)


def his_offers(t):
    out = []
    for m in t["messages"]:
        o = m.get("offer") or {}
        if m.get("sender") == DEALER and o:
            out.append(o)
    return out


def clean(o):
    g, w = o.get("give") or {}, o.get("want") or {}
    gives = (g.get("types") == [f"card:{REF}"] and not g.get("assets")) or \
            (len(g.get("assets") or []) == 1 and not g.get("types") and (g["assets"][0].get("ref") if isinstance(g["assets"][0], dict) else None) == REF)
    return gives and not g.get("cash") and isinstance(w.get("cash"), int) and not w.get("assets") and not w.get("types")


t = b.open_thread(DEALER, topic={"buy": {"card": REF}})
tid, ours, turn = t["id"], OPEN, 0
log(ev="open", thread=tid, ref=REF, cap=CAP)
while True:
    n_before = len(his_offers(b.thread(tid)))
    try:
        b.say(tid, TEXTS[min(turn, len(TEXTS) - 1)].format(ref=REF, p=ours), price=ours)
        log(ev="say", price=ours)
    except BazaarError as e:
        log(ev="say_error", code=e.code, message=e.message)
        if e.code in ("wait_for_tick", "rate_limited"):
            time.sleep(5)
            continue
        break
    turn += 1
    for _ in range(int(150 / POLL)):
        t = b.thread(tid)
        if t["status"] != "open" or len(his_offers(t)) > n_before:
            break
        time.sleep(POLL)
    if t["status"] != "open":
        log(ev="closed", status=t["status"], reason=t.get("closed_reason"))
        break
    hs = his_offers(t)
    o = hs[-1]
    ask, final = (o.get("want") or {}).get("cash"), bool(o.get("final"))
    step = (hs[-2]["want"]["cash"] - ask) if len(hs) >= 2 and (hs[-2].get("want") or {}).get("cash") else None
    log(ev="his", ask=ask, step=step, final=final, status=o.get("status"), clean=clean(o))
    if not clean(o) or o.get("status") != "open":
        continue
    nxt = min(CAP, ours + max(2, round(0.5 * step)) if step else ours + 3)
    if ask <= CAP and (ask <= nxt or final):
        r = b.accept(o["id"])
        log(ev="accept", price=ask, r=r)
        break
    if final or ours >= CAP:
        log(ev="walk", ask=ask, cap=CAP)
        try:
            b.say(tid, "Thank you, but that is beyond our budget tonight. Have a good evening.")
        except BazaarError:
            pass
        b.close_thread(tid)
        break
    ours = min(nxt, ask - 1)
