"""Sell one card by a Boulware descent with a hard floor: ask `--start` P, concede slowly at first and faster near the
end, never below `--floor` P, at most `--rounds` price steps, then withdraw.

    cd team13 && source ../bazaar.env && python3 ../agent/sell.py MAL-09 --start 200 --floor 140 --rounds 8 --ticks 5
    ... --dry     prints the schedule and what it would do, writes nothing

The hard rules live in code, not in a prompt:
  - a price is never listed below the floor, and the floor must beat what giving the card up costs us at our private
    values (page bonus included) by at least 3 P;
  - the same public ask goes on El Rastro and on one free market whose owner is not a buyer we know is interested;
  - an offer addressed to us for this card is accepted only if its value to us, after that market's fee, is at least
    what the floor would have given us; offers that pay in cards are valued at our private values;
  - the card must still be in our hands before every step; when it leaves we cancel what is still open and stop;
  - offers live `--ticks` + 2 ticks, so a dead script leaves nothing open for long. Prices are never put in a message.
It writes with the team key but only touches this one card's offers. Log: team13/logs/sell.jsonl.
"""
import argparse
import json
import math
import os
import signal
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "team13"))
from bazaar_sdk import Bazaar, BazaarError  # noqa: E402
from trader import fee as venue_fee  # noqa: E402
from values import Values  # noqa: E402

LOG = os.path.join(HERE, "..", "team13", "logs", "sell.jsonl")
MIN_GAIN = 3          # P over the cost of giving the card up
EXPONENT = 2.2        # Boulware: small steps first, big ones near the end (strategy knob haggle_curve)


def log(**rec):
    rec["ts"] = round(time.time(), 1)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str)[:300], flush=True)


def price_at(k: int, start: int, floor: int, rounds: int) -> int:
    """Ask at step k (0 = opening, `rounds` = the floor)."""
    x = min(1.0, k / rounds)
    return max(floor, math.ceil(start - (start - floor) * x ** EXPONENT))


def refs(side: dict) -> list:
    out = [a["ref"] for a in side.get("assets") or [] if isinstance(a, dict)]
    out += [t.split(":", 1)[1] for t in side.get("types") or [] if t.startswith("card:")]
    return out + list(side.get("cards") or [])


def free_venue(venues: list, leaderboard: dict, me: str, avoid: set):
    """Open market with the lowest fee, preferring one where trades have happened, then the lowest-scoring owner (a
    trade there counts toward its market-making); not ours and not an owner in `avoid` (a team cannot trade on its
    own market, so the interested buyers we know could never take the ask there)."""
    score = {t["team"]: t.get("score") or 0 for t in (leaderboard or {}).get("teams", [])}
    best = None
    for v in venues:
        if v.get("status") != "open" or v.get("owner") in avoid | {me}:
            continue
        key = (v.get("fee_bps") or 0, v.get("fee_per_card") or 0, 0 if (v.get("trades") or 0) else 1, score.get(v.get("owner"), 0))
        if best is None or key < best[0]:
            best = (key, v)
    return best[1] if best else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ref")
    ap.add_argument("--start", type=int, required=True)
    ap.add_argument("--floor", type=int, required=True)
    ap.add_argument("--rounds", type=int, default=8, help="price steps before the floor")
    ap.add_argument("--ticks", type=int, default=5, help="ticks each step lasts")
    ap.add_argument("--hold", type=int, default=1, help="extra steps at the floor before withdrawing")
    ap.add_argument("--avoid", default="t14,t10", help="teams that cannot take an ask on their own market")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    api = Bazaar(os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai"), os.environ["BAZAAR_KEY"], wait_on_tick=False)
    me = api.me()
    me_id, cat = me["id"], api.catalog()
    cards = [x for x in me["assets"] if x.get("kind") == "card" and x["ref"] == a.ref]
    if len(cards) != 1:
        sys.exit(f"we hold {len(cards)} copies of {a.ref}; this script sells exactly one")
    asset = cards[0]["id"]
    v = Values(cat, me)
    cost = v.loss_of_removing([a.ref])
    need = a.floor - cost  # value over the cost we require from any deal
    if need < MIN_GAIN or a.start <= a.floor:
        sys.exit(f"refused: floor {a.floor} P leaves {need:.1f} P over the {cost:.1f} P it costs us to give {a.ref} up (min {MIN_GAIN})")
    venues = api.venues()["venues"]
    by_id = {x["venue"]: x for x in venues}
    free = free_venue(venues, api.leaderboard(), me_id, set(filter(None, a.avoid.split(","))))
    where = ["rastro"] + ([free["venue"]] if free else [])
    steps = []
    for k in range(a.rounds + 1):  # a step that would repeat the last price is skipped: it would only churn the offer
        p = price_at(k, a.start, a.floor, a.rounds)
        if not steps or p < steps[-1]:
            steps.append(p)
    steps += [a.floor] * a.hold
    log(event="plan", ref=a.ref, asset=asset, cost=round(cost, 1), floor=a.floor, need_net=round(need, 1), venues=where,
        steps=steps, ticks_per_step=a.ticks, minutes=round(len(steps) * a.ticks * 0.5, 1))

    def incoming():
        """Offers addressed to us that want exactly this card and are worth at least the floor to us."""
        out = []
        for o in api.my_offers().get("offers", []):
            if o.get("to") != me_id or o.get("maker") == me_id or o.get("status") != "open":
                continue
            give, want = o.get("give") or {}, o.get("want") or {}
            if refs(want) != [a.ref] or want.get("cash"):
                continue
            ven = by_id.get(o.get("venue")) or {}
            cash = give.get("cash") or 0
            f = venue_fee(cash, len(refs(give)) + 1, ven.get("fee_bps") or 0, ven.get("fee_per_card") or 0) if o.get("venue") else 0
            net = v.gain_of_adding(refs(give)) - cost + cash - f
            out.append({"offer": o["id"], "maker": o["maker"], "net": round(net, 1), "ok": net >= need,
                        "give": refs(give), "cash": cash, "fee": f})
        return out

    for x in incoming():
        log(event="incoming_checked", **x)
    if a.dry:
        return
    open_offers, last_ticks, k, started = {}, None, 0, None

    def withdraw(why):
        for vid, oid in list(open_offers.items()):
            try:
                api.cancel(oid)
            except BazaarError:
                pass
            open_offers.pop(vid)
        log(event="withdrawn", why=why)

    def stop(*_):
        withdraw("interrupted")
        sys.exit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while True:
        try:
            clock = api.clock()
            tick = clock["tick"]
            if tick == last_ticks:
                time.sleep(3)
                continue
            last_ticks = tick
            if not any(x["id"] == asset for x in api.me()["assets"]):
                log(event="card_left", tick=tick, asset=asset)
                withdraw("card left our hands: sold")
                return
            for x in incoming():
                if x["ok"]:
                    api.accept(x["offer"], assets=[asset])
                    log(event="accepted_incoming", tick=tick, **x)
                    time.sleep(2)
                    break
            if started is None or tick - started >= a.ticks:
                if k >= len(steps):
                    withdraw("end of the descent: not sold")
                    return
                p = steps[k]
                assert p >= a.floor and p - cost >= MIN_GAIN, "hard rule: never below the floor"
                fresh = {}
                for vid in where:
                    try:
                        o = api.list_offer({"assets": [asset]}, {"cash": p}, venue=vid, expires_in_ticks=a.ticks + 2)
                        fresh[vid] = o["id"]
                        log(event="ask", tick=tick, step=k, price=p, venue=vid, offer=o["id"], expires=o.get("expires_tick"))
                    except BazaarError as e:
                        log(event="ask_refused", tick=tick, step=k, price=p, venue=vid, error=str(e)[:160])
                for vid, oid in open_offers.items():  # the lower ask is up: take the higher one down
                    try:
                        api.cancel(oid)
                    except BazaarError:
                        pass
                open_offers = fresh
                started, k = tick, k + 1
        except BazaarError as e:
            log(event="api_error", error=str(e)[:160])
            time.sleep(5)
        except Exception as e:  # never die with an offer open without saying so
            log(event="crash", error=repr(e)[:200])
            withdraw("crash")
            raise


if __name__ == "__main__":
    main()
