"""One-off closer for thread 87 (t04, SAL-09): accept any clean offer <= CAP, else step our bid by 2 up to CAP."""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bazaar-kit"))
from bazaar_sdk import Bazaar, BazaarError

TID, REF, CAP, STEP = 87, "SAL-09", 76, 2
LOG = os.path.join(os.path.dirname(__file__), "..", "logs", "hand.jsonl")
b = Bazaar(os.environ["BAZAAR_URL"], os.environ["BAZAAR_KEY"], wait_on_tick=False)
_t0 = b.thread(TID)
ours = max([(o.get("give") or {}).get("cash") or 0 for o in _t0["standing_offers"] if o["maker"] == "t13"] + [72])
seen = {o["id"] for o in b.thread(TID)["standing_offers"]}  # offers made before our 72: already answered


def log(**k):
    with open(LOG, "a") as f:
        f.write(json.dumps({"ts": time.time(), **k}) + "\n")
    print(json.dumps(k)[:300], flush=True)


def clean(o):
    g, w = o.get("give") or {}, o.get("want") or {}
    return (len(g.get("assets") or []) == 1 and g["assets"][0].get("ref") == REF and not g.get("cash") and not g.get("types")
            and isinstance(w.get("cash"), int) and not w.get("assets") and not w.get("types") and not w.get("cards"))


while True:
    t = b.thread(TID)
    if t["status"] != "open":
        log(ev="sal09_end", status=t["status"], reason=t.get("closed_reason"))
        break
    theirs = [o for o in t["standing_offers"] if o["maker"] == "t04" and o["status"] == "open" and o["id"] not in seen]
    if theirs:
        o = theirs[-1]
        seen.add(o["id"])
        ask = (o.get("want") or {}).get("cash")
        if clean(o) and ask <= CAP:
            try:
                log(ev="sal09_accept", offer=o["id"], price=ask, r=b.accept(o["id"]))
            except BazaarError as e:
                log(ev="sal09_accept_err", code=e.code, message=e.message)
                seen.discard(o["id"])
                time.sleep(10)
            continue
        if not clean(o):
            log(ev="sal09_unclean_offer_ignored", offer=o)
        elif ours < CAP:
            ours = min(CAP, ours + STEP)
            try:
                r = b.say(TID, f"Thank you for meeting us, Team 4. We can stretch to {ours} primas for El Marqués; "
                               "the structured offer is attached.", offer={"give": {"cash": ours}, "want": {"cards": [REF]}})
                log(ev="sal09_counter", price=ours, their=ask, r=r)
            except BazaarError as e:
                log(ev="sal09_counter_err", code=e.code, message=e.message)
        else:
            log(ev="sal09_hold_at_cap", their=ask, ours=ours)
    time.sleep(8)
