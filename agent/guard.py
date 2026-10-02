"""Guard: cancel any open offer of ours that gives away more value than it brings, at our private values
(team13/values.py, page bonus and near-complete page option included). Closes the thread when the offer lives in one.
A safety net while several agents trade on one key.

    python3 agent/guard.py [poll_seconds]
"""
import json, os, sys, time
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "team13"))
sys.path.insert(0, os.path.join(HERE, "..", "bazaar-kit"))
from bazaar_sdk import Bazaar, BazaarError  # noqa: E402
from values import Values  # noqa: E402

POLL = float(sys.argv[1]) if len(sys.argv) > 1 else 10
LOG = os.path.join(HERE, "..", "logs", "guard.jsonl")
CAPS = os.path.join(HERE, "caps.json")
b = Bazaar(os.environ["BAZAAR_URL"], os.environ["BAZAAR_KEY"], wait_on_tick=False)
cat = b.catalog()


def refs(side, own):
    out = list(side.get("cards") or [])
    out += [t.split(":", 1)[1] for t in side.get("types") or [] if t.startswith("card:")]
    for a in side.get("assets") or []:
        out.append(a["ref"] if isinstance(a, dict) else own.get(a, f"#{a}"))
    return out


while True:
    try:
        me = b.me()
        v = Values(cat, me)
        own = {a["id"]: a["ref"] for a in me["assets"]}
        for o in b.my_offers().get("offers", []):
            if o.get("maker") != me["id"] or o.get("status") != "open":
                continue
            g, w = o.get("give") or {}, o.get("want") or {}
            if any(not t.startswith("card:") for t in (g.get("types") or []) + (w.get("types") or [])):
                continue  # packs etc.: not judged here
            out_refs, in_refs = refs(g, own), refs(w, own)
            try:
                with open(CAPS) as f:
                    caps = json.load(f)  # hand-set price caps per card, e.g. {"SAL-10": 70}
            except (OSError, ValueError):
                caps = {}
            over_cap = (not out_refs and len(in_refs) == 1 and in_refs[0] in caps
                        and (g.get("cash") or 0) > caps[in_refs[0]])
            stale_bid = (not out_refs and in_refs and not over_cap
                         and v.gain_of_adding(in_refs) < (g.get("cash") or 0))  # e.g. a second SAL-10 bid once we own one
            if not out_refs and not over_cap and not stale_bid:
                continue
            loss = v.loss_of_removing(out_refs) + (g.get("cash") or 0)
            gain = v.gain_of_adding(in_refs) + (w.get("cash") or 0)
            if loss > gain or over_cap or stale_bid:
                rec = {"ts": time.time(), "ev": "guard_cancel", "offer": o["id"], "thread": o.get("thread"),
                       "give": out_refs, "want": in_refs or w.get("cash"), "loss": round(loss, 1), "gain": round(gain, 1), "over_cap": over_cap}
                try:
                    b.cancel(o["id"])
                    if o.get("thread"):
                        try:
                            b.close_thread(o["thread"])
                        except BazaarError:
                            pass
                except BazaarError as e:
                    rec["error"] = e.code
                with open(LOG, "a") as f:
                    f.write(json.dumps(rec) + "\n")
                print(json.dumps(rec), flush=True)
    except Exception as e:  # stay alive through network errors
        print(f"GUARD-ERROR {type(e).__name__}: {e}", flush=True)
    time.sleep(POLL)
