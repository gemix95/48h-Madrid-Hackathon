"""Automatic haggler for Abuela Carmen, built from what we measured by hand and in the public feed.

What we learned (Fri evening):
- Her offers expire after 2 ticks, so we poll every few seconds and answer inside the tick.
- Two regimes: a fixed "beginner" price (same ask every turn), or a decaying ladder (30 -> 26 -> 25 -> 24 -> 23 -> 22 -> 21)
  whose steps shrink towards her secret floor. Our step size barely matters; a new price every turn does.
- She likes kindness; never repeat a price (repeats earn nothing and can read as spam).

Numbers are decided here, never by the text. Every accept is checked against the offer's structure and our cap.

    python3 agent/abuela_bot.py            # runs the target queue; touch agent/STOP to stop after the current step
"""
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bazaar-kit"))
from bazaar_sdk import Bazaar, BazaarError  # noqa: E402

HERE = os.path.dirname(__file__)
LOG = os.path.join(HERE, "..", "logs", "abuela_bot.jsonl")
STOP = os.path.join(HERE, "STOP")
DEALER = "abuela"
POLL = 3.0
LIST_PRICE = {"common": 10, "uncommon": 25}
ANCHOR = {"common": 4, "uncommon": 11}     # our opening offer
STEP = {"common": 1, "uncommon": 2}        # our normal step
CAP_SHARE = 0.80                           # never pay more than this share of our private value
MAX_TURNS = 14

b = Bazaar(os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai"), os.environ["BAZAAR_KEY"], wait_on_tick=False)

OPENERS = [
    "Buenas, Carmen! We are filling our {set} page, little by little. Would {p} primas be all right for {card}?",
    "Hola, Carmen, so nice to see your stall again! Could we have {card} for our album? {p} primas, if that is kind enough.",
    "Carmen, thank you for everything so far, our album is growing thanks to you. May we ask for {card}? Would {p} work?",
]
COUNTERS = [
    "You are very kind, Carmen. Let us take a small step towards you: {p}?",
    "Gracias, Carmen! We are getting close, what do you say to {p}?",
    "Ay, our little budget! {p} primas, and {card} gets a place of honour in our album.",
    "Thank you for your patience, Carmen. One more step from our side: {p}.",
    "Carmen, you are like family to us. Could we meet at {p}?",
]
ACCEPT_TEXT = "Gracias de corazón, Carmen! It is a deal."


def log(ev: str, **kw) -> None:
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps({"ts": time.time(), "ev": ev, **kw}) + "\n")
    print(f"[{time.strftime('%H:%M:%S')}] {ev} {json.dumps(kw)[:220]}", flush=True)


def her_open_offer(t: dict, ref: str):
    """Her latest open offer selling exactly `ref` for cash only, or None. Structure, never words."""
    for o in reversed(t["standing_offers"]):
        if o["maker"] != DEALER or o["status"] != "open":
            continue
        give, want = o.get("give") or {}, o.get("want") or {}
        if give.get("types") == [f"card:{ref}"] and not give.get("cash") and not give.get("assets") \
                and not want.get("assets") and not want.get("types") and isinstance(want.get("cash"), int):
            return o
    return None


def her_asks(t: dict) -> list:
    """All prices she has asked in this thread, oldest first (from her messages' offers)."""
    out = []
    for m in t["messages"]:
        o = m.get("offer") or {}
        if m.get("sender") == DEALER and o.get("want", {}).get("cash"):
            out.append(o["want"]["cash"])
    return out


def wait_for_her(tid: int, n_before: int) -> dict:
    for _ in range(int(150 / POLL)):
        t = b.thread(tid)
        if t["status"] != "open" or len(her_asks(t)) > n_before:
            return t
        time.sleep(POLL)
    return b.thread(tid)


def haggle(ref: str, rarity: str, set_name: str, value: float) -> str:
    cap = min(int(value * CAP_SHARE), LIST_PRICE[rarity])
    cap = max(cap, 1)
    # One open conversation per dealer, and a teammate's agent may hold it: wait, never close a thread we did not open.
    while any(t.get("with") == DEALER for t in b.my_threads("open").get("threads", [])):
        log("dealer_busy", ref=ref)
        time.sleep(30)
    t = b.open_thread(DEALER, topic={"buy": {"card": ref}})
    tid = t["id"]
    log("open", ref=ref, thread=tid, value=value, cap=cap)
    ours = []
    price = ANCHOR[rarity]
    text = random.choice(OPENERS).format(set=set_name, p=price, card=ref)
    for turn in range(MAX_TURNS):
        n_before = len(her_asks(b.thread(tid)))
        try:
            b.say(tid, text, price=price)
        except BazaarError as e:
            log("say_error", ref=ref, code=e.code, message=e.message)
            if e.code in ("wait_for_tick", "rate_limited"):
                time.sleep(5)
                continue
            return e.code
        ours.append(price)
        log("say", ref=ref, thread=tid, price=price)
        t = wait_for_her(tid, n_before)
        if t["status"] != "open":
            log("closed", ref=ref, thread=tid, status=t["status"], reason=t.get("closed_reason"))
            return t["status"]
        asks = her_asks(t)
        offer = her_open_offer(t, ref)
        if not offer:
            log("no_offer", ref=ref, thread=tid, asks=asks)
            continue
        ask = offer["want"]["cash"]
        her_step = asks[-2] - asks[-1] if len(asks) >= 2 else None
        log("her", ref=ref, ask=ask, step=her_step, final=offer.get("final"), asks=asks)
        take = ask <= cap and (
            offer.get("final")
            or ask <= price + STEP[rarity]                      # she is within one of our steps
            or (len(asks) >= 4 and asks[-3] - asks[-2] <= 1 and asks[-2] - asks[-1] <= 1)  # two tiny steps: at her floor
            or (len(asks) >= 3 and asks[-1] == asks[-2] == asks[-3])         # fixed price regime, she will not move
        )
        if take:
            if b.value(ref)["your_value"] < ask:  # re-check: still worth it to us now
                log("skip_value_changed", ref=ref, ask=ask)
                b.close_thread(tid)
                return "skipped"
            r = b.accept(offer["id"])
            log("accept", ref=ref, thread=tid, price=ask, r=r)
            for _ in range(int(150 / POLL)):  # the thread stays open until the deal settles on the next tick
                if b.thread(tid)["status"] != "open":
                    break
                time.sleep(POLL)
            return b.thread(tid)["status"]
        if offer.get("final"):
            log("walk_final_above_cap", ref=ref, ask=ask, cap=cap)
            b.close_thread(tid)
            return "walked"
        nxt = min(cap, ask - 1, price + STEP[rarity])
        if nxt <= price:   # nothing new to offer inside our cap: one tiny step if possible, else leave politely
            nxt = price + 1
            if nxt > cap or nxt >= ask:
                log("leave_at_cap", ref=ref, ask=ask, cap=cap)
                b.close_thread(tid)
                return "left"
        price = nxt
        text = random.choice(COUNTERS).format(p=price, card=ref)
        if os.path.exists(STOP):
            log("stop_requested", ref=ref)
            b.close_thread(tid)
            return "stopped"
    b.close_thread(tid)
    return "max_turns"


def targets() -> list:
    """Cards worth buying from her: released commons/uncommons whose marginal value to us beats the list price."""
    cat = b.catalog()
    out = []
    for s in cat["sets"]:
        if not s.get("released"):
            continue
        for c in s["cards"]:
            if c["rarity"] not in LIST_PRICE:
                continue
            v = b.value(c["id"])["your_value"]
            if v >= LIST_PRICE[c["rarity"]] * 1.2:
                out.append((v - LIST_PRICE[c["rarity"]], c["id"], c["rarity"], s["name"], v))
            time.sleep(0.25)
    return sorted(out, reverse=True)


def main() -> None:
    queue = targets()
    log("targets", items=[(r, v) for _, r, _, _, v in queue])
    for _, ref, rarity, set_name, value in queue:
        if os.path.exists(STOP):
            log("stopped")
            return
        if b.me()["cash"] < LIST_PRICE[rarity] + 5:
            log("low_cash")
            return
        try:
            res = haggle(ref, rarity, set_name, value)
        except BazaarError as e:
            res = e.code
            log("error", ref=ref, code=e.code, message=e.message)
        log("result", ref=ref, result=res)
        if res in ("persona_quota", "cooloff"):
            log("pause", reason=res)
            time.sleep(600)
        time.sleep(2)
    log("done")


if __name__ == "__main__":
    main()
