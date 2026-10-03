"""Team 13's broker for our venue. Market-making is 30 points: Market Test efficiency, plus value other teams
create by trading on our book.

    source ../bazaar.env && python3 smart_broker.py     # reads the broker key from state.json (or BROKER_KEY)

What the board actually did (Saturday session 1): the free stall and every broker that crossed like it sit on one
score, and the teams above that (t12 El Duende, t05's stall, t06) are the ones where OTHER teams settled trades.
We scored last: the broker matched two bench pairs at tick 204 and then was not running for the rest of the
16-tick session, while the fee was still 1% (a 1 P fee blocks a thin cross the 0% stall takes).

The live plan is therefore the stall's own cross, for every tick, at whatever fee the book currently has:
lowest ask against highest bid, midpoint, walked down until the buyer can pay the fee. Pairing "smarter" first
and then filling leftovers scores below that floor, because the first match consumes the trader the stall would
have given a better partner. Real offers are crossed the same way, card by card, so a bid and an ask that meet
on our book settle here instead of on a rival's venue.
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

from bazaar_sdk import BazaarError, Broker
import starter_plans
try:
    import auctions  # a copy next to the broker, when the deploy puts one there
except ImportError:  # otherwise the app's copy: the deploy updates /home/bazaar/app on every push
    import sys as _sys
    _sys.path.append("/home/bazaar/app/team13")
    try:
        import auctions
    except ImportError:
        auctions = None

HERE = Path(__file__).parent
LOG = HERE / "logs" / "broker.jsonl"
SESSION_TICKS = 16      # Market Test length; refreshed from /api/schedule ("ticks") by refresh_session_ticks()
LATE = 0.6              # after this share of the session, cross greedily like the stall
DEFAULT_SHADE = 0.12


def log(**rec):
    LOG.parent.mkdir(exist_ok=True)
    rec["ts"] = round(time.time(), 1)
    with LOG.open("a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    print(json.dumps(rec, default=str)[:300], flush=True)


class Tracker:
    def __init__(self):
        self.seen: dict = {}   # offer id -> {"first": tick, "quotes": [..], "side": "ask"|"bid", "run": "b12"}
        self.run_start: dict = {}
        self.hist: dict = {}   # run -> offer id -> {"side", "quotes", "first", "last"}: every trader ever seen, kept

    def update(self, tick, offers):
        live = set()
        for o in offers:
            oid = o["id"]
            live.add(oid)
            ask = (o.get("want") or {}).get("cash")
            side, q = ("ask", ask) if ask else ("bid", (o.get("give") or {}).get("cash"))
            run = str(oid).split("-")[0]
            self.run_start.setdefault(run, tick)
            s = self.seen.setdefault(oid, {"first": tick, "quotes": [], "side": side, "run": run, "raw": o})
            if not s["quotes"] or s["quotes"][-1] != q:
                s["quotes"].append(q)
            s["raw"] = o
            h = self.hist.setdefault(run, {}).setdefault(oid, {"side": side, "quotes": [], "first": tick})
            h["quotes"], h["last"] = s["quotes"], tick
        for oid in list(self.seen):
            if oid not in live:
                self.seen.pop(oid)
        return live

    def shade(self, run):
        """Learned shading: how far quotes in this run have relaxed so far, never below the default guess."""
        rel = [abs(s["quotes"][0] - s["quotes"][-1]) / max(1, s["quotes"][0]) for s in self.seen.values()
               if s["run"] == run and len(s["quotes"]) > 1]
        return max(DEFAULT_SHADE, 1.3 * (sum(rel) / len(rel))) if rel else DEFAULT_SHADE

    def clearing_price(self, run):
        """Estimated competitive price from every trader seen this session (also the ones gone or matched)."""
        sh = self.shade(run)
        hs = self.hist.get(run, {}).values()
        costs = sorted(h["quotes"][-1] * (1 - sh) for h in hs if h["side"] == "ask")
        vals = sorted((h["quotes"][-1] * (1 + sh) for h in hs if h["side"] == "bid"), reverse=True)
        k = 0
        while k < min(len(costs), len(vals)) and vals[k] >= costs[k]:
            k += 1
        if k == 0:
            return None
        lo = max(costs[k - 1], vals[k] if k < len(vals) else 0)
        hi = min(vals[k - 1], costs[k] if k < len(costs) else float("inf"))
        return (lo + hi) / 2 if hi != float("inf") else lo

    def lifespan(self, run):
        """Median ticks a trader stayed before leaving, from the ones already gone (None until we know)."""
        gone = sorted(h["last"] - h["first"] for oid, h in self.hist.get(run, {}).items() if oid not in self.seen)
        return gone[len(gone) // 2] if len(gone) >= 3 else None

    def leaving(self, s, tick):
        o = s["raw"]
        for k in ("expires_tick", "leaves_at", "until_tick", "deadline"):
            if o.get(k) is not None:
                return o[k] - tick <= 1
        for k in ("ttl", "patience_left", "ticks_left"):
            if o.get(k) is not None:
                return o[k] <= 1
        return False


def _price_with_fee(ask, bid, fee):
    """Midpoint, walked down until the buyer also covers the venue fee. None if no price works."""
    if bid < ask:
        return None
    return next((p for p in range((ask + bid) // 2, ask - 1, -1) if p + fee(p) <= bid), None)


def smart_bench_plan(book, tracker, tick, fee):
    plan = []
    runs: dict = {}
    for oid, s in tracker.seen.items():
        runs.setdefault(s["run"], {"ask": [], "bid": []})[s["side"]].append((oid, s))
    for run, sides in runs.items():
        sh = tracker.shade(run)
        progress = (tick - tracker.run_start.get(run, tick)) / SESSION_TICKS
        late = progress >= LATE
        sellers = sorted(((oid, s, s["quotes"][-1], s["quotes"][-1] * (1 - sh)) for oid, s in sides["ask"]), key=lambda x: x[3])
        buyers = sorted(((oid, s, s["quotes"][-1], s["quotes"][-1] * (1 + sh)) for oid, s in sides["bid"]), key=lambda x: -x[3])
        pstar = tracker.clearing_price(run)
        span = tracker.lifespan(run)

        def ok(entry, side):
            """Trade this trader now? Yes if it is on the efficient side of the estimated clearing price, or if it
            is late in the session, or it has been around about as long as traders usually stay."""
            oid, s, q, est = entry
            if late or tracker.leaving(s, tick) or pstar is None:
                return True
            if span is not None and tick - s["first"] >= max(1, span - 1):
                return True
            return est <= pstar if side == "ask" else est >= pstar
        intra_s = {x[0] for x in sellers if ok(x, "ask")}
        intra_b = {x[0] for x in buyers if ok(x, "bid")}
        used = set()
        # pass 1: intramarginal pairs whose quotes cross now (highest value with lowest cost first)
        for b in buyers:
            if b[0] not in intra_b:
                continue
            for s in sellers:
                if s[0] in used or b[0] in used:
                    continue
                if s[0] not in intra_s:
                    continue
                price = _price_with_fee(s[2], b[2], fee)
                if price is not None:
                    plan.append((s[0], b[0], price))
                    used |= {s[0], b[0]}
                    break
    return plan


PROBES_PER_TICK = 3     # match attempts per tick on pairs whose quotes do not cross yet (rate limit: 5 req/s)
PROBE_GIVEUP = 6        # refusals with no acceptance at all: the server checks quotes, stop probing
PROBE_MIN_AGE = 2       # a trader is probed once it has quoted this many ticks (its shading is learned) ...
PROBE_AFTER = 0.5       # ... or once this share of the session has passed (sim: beats the stall with and without arrivals)
# Off on purpose. A probe matches a pair whose quotes do not cross. The server has not been seen to accept
# that, and a wrong accept consumes a trader the stall still needed. Turn on only after a refused-or-accepted
# check on a live book.
_probe = {"on": False, "accepted": 0, "refused": 0, "tries": {}}


def probe_plan(tracker, used, fee, tick, max_n=PROBES_PER_TICK):
    """Pairs whose quotes do not cross but whose estimated limits do, priced at the midpoint of the estimates.

    The Market Test scores gains between TRUE limits; if the server checks a match against those limits rather than
    the quotes, these are trades the stall never makes (firm traders never relax into a cross). Probing a newcomer
    pairs it before better partners arrive, hence the age / session-progress gate."""
    if not _probe["on"]:
        return []
    out, runs = [], {}
    for oid, s in tracker.seen.items():
        progress = (tick - tracker.run_start.get(s["run"], tick)) / SESSION_TICKS
        if oid not in used and (progress >= PROBE_AFTER or tick - s["first"] >= PROBE_MIN_AGE):
            runs.setdefault(s["run"], {"ask": [], "bid": []})[s["side"]].append((oid, s))
    for run, sides in runs.items():
        sh = tracker.shade(run)
        sellers = sorted(((oid, s["quotes"][-1], s["quotes"][-1] * (1 - sh)) for oid, s in sides["ask"]), key=lambda x: x[2])
        buyers = sorted(((oid, s["quotes"][-1], s["quotes"][-1] * (1 + sh)) for oid, s in sides["bid"]), key=lambda x: -x[2])
        for (sell, ask, cost), (buy, bid, value) in zip(sellers, buyers):
            if value < cost:
                break
            if bid >= ask or _probe["tries"].get((sell, buy), 0) >= 2:
                continue
            price = min(max(round((cost + value) / 2), bid), ask)
            if price + fee(price) <= value:
                out.append((sell, buy, price))
    return out[:max_n]


def probe_result(sell, buy, ok, error=""):
    _probe["tries"][(sell, buy)] = _probe["tries"].get((sell, buy), 0) + 1
    _probe["accepted" if ok else "refused"] += 1
    if not ok and not _probe["accepted"] and _probe["refused"] >= PROBE_GIVEUP:
        _probe["on"] = False
        log(event="probe_disabled", refused=_probe["refused"], last_error=error[:200])


def _bench_quotes(book):
    asks, bids = {}, {}
    for o in book.get("bench_offers") or []:
        if (o.get("want") or {}).get("cash"):
            asks[o["id"]] = o["want"]["cash"]
        elif (o.get("give") or {}).get("cash"):
            bids[o["id"]] = o["give"]["cash"]
    return asks, bids


def live_bench_plan(book, fee):
    """Every quote-crossing bench pair, stall order, price the buyer can pay after the fee.

    This is the whole Market Test plan. It is what the free auto stall does, which is the floor under the
    teams tied at the top. Anything that matches a different pair first can land below that floor.
    """
    return stall_floor(book, [], fee)


def stall_floor(book, plan, fee):
    """Add any free-stall crosses the smart plan skipped, so we never score below half bench points.

    Impatient / hard Market Tests leave before our limit estimates settle; taking the leftover stall pairs
    restores the floor while keeping the smart plan's preferred intramarginal matches first.
    """
    used = {oid for s, b, _ in plan for oid in (s, b)}
    out = list(plan)
    asks, bids = _bench_quotes(book)
    for sell, buy, _mid in starter_plans.bench_plan(book):
        if sell in used or buy in used:
            continue
        ask, bid = asks.get(sell), bids.get(buy)
        if ask is None or bid is None:
            continue
        price = _price_with_fee(ask, bid, fee)
        if price is None:
            log(event="fee_blocked", sell=sell, buy=buy, ask=ask, bid=bid)
            continue
        out.append((sell, buy, price))
        used |= {sell, buy}
    return out


def log_fee_blocks(book, fee):
    """Surface quote-crossing bench pairs that our fee makes unmatchable (fee_safety watches this)."""
    asks, bids = _bench_quotes(book)
    for sell, buy, _ in starter_plans.bench_plan(book):
        ask, bid = asks.get(sell), bids.get(buy)
        if ask is not None and bid is not None and _price_with_fee(ask, bid, fee) is None:
            log(event="fee_blocked", sell=sell, buy=buy, ask=ask, bid=bid)


def take_lock():
    """One broker per venue: the standalone process or the agent's thread, whichever holds logs/broker.lock.
    Returns the open lock file (keep it alive) or None if another broker already runs."""
    import fcntl
    LOG.parent.mkdir(exist_ok=True)
    f = open(LOG.parent / "broker.lock", "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return None
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f


def main():
    lock = take_lock()  # noqa: F841 (held until exit)
    if lock is None:
        raise SystemExit("Another broker already runs for our venue (logs/broker.lock).")
    url = os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai")
    key = os.environ.get("BROKER_KEY")
    if not key:
        st = HERE / "state.json"
        key = json.loads(st.read_text()).get("broker_key") if st.exists() else None
    if not key:
        raise SystemExit("No broker key yet: the agent opens our venue at level 2 and stores it in state.json.")
    run(url, key)


_ticks_checked = 0.0


def refresh_session_ticks(url):
    """Read the next Market Test's length from /api/schedule (organisers may change it, e.g. the hard test).
    The nearest upcoming bench sets SESSION_TICKS; once it starts it leaves the list and the value stays."""
    global SESSION_TICKS, _ticks_checked
    if time.time() - _ticks_checked < 60:
        return
    _ticks_checked = time.time()
    try:
        import urllib.request
        with urllib.request.urlopen(url + "/api/schedule", timeout=10) as r:
            up = [u for u in json.load(r).get("upcoming", []) if u.get("action") == "bench"]
        if up:
            n = int((up[0].get("params") or {}).get("ticks") or SESSION_TICKS)
            if n != SESSION_TICKS:
                log(event="session_ticks", old=SESSION_TICKS, new=n)
                SESSION_TICKS = max(1, n)
    except Exception as e:  # keep the last known length
        log(event="session_ticks_failed", error=repr(e)[:120])


def run(url, key):
    """The broker loop. A standalone process (screen team13-broker) holds the lock; the agent's thread starts
    this only when that process is not running."""
    broker, tracker, seen = Broker(url, key), Tracker(), None
    last_beat = 0.0
    log(event="start", plan="stall")
    while True:
        try:
            refresh_session_ticks(url)
            tick, book = broker.clock()["tick"], broker.book()
            bench = book.get("bench_offers") or []
            tracker.update(tick, bench)
            fee_bps, per_card = book.get("fee_bps", 0), book.get("fee_per_card", 0)

            def fee(p):
                return math.ceil(fee_bps * p / 10000) + per_card
            # Include quotes so a mid-tick reprice (bench traders relax) triggers a new plan.
            now = (tick, sorted((o["id"], (o.get("want") or {}).get("cash") or (o.get("give") or {}).get("cash"))
                                for o in bench + (book.get("offers") or [])))
            if now != seen:
                seen = now
                try:
                    plan = live_bench_plan(book, fee)
                except Exception as e:  # a bug here must still cross whatever the stall would
                    log(event="plan_failed", error=repr(e))
                    plan = starter_plans.bench_plan(book)
                if fee_bps or per_card:
                    log_fee_blocks(book, fee)
                plan += starter_plans.public_plan(book)
                if auctions is not None:  # no-op since lots are settled by the seller (auctions.py)
                    try:
                        plan = auctions.broker_plan(plan, book.get("offers") or [], tick)
                    except Exception as e:
                        log(event="auction_error", error=repr(e)[:200])
                if bench:
                    log(event="plan", tick=tick, bench=len(bench), matches=len(plan),
                        fee_bps=fee_bps, fee_per_card=per_card)
                    log(event="bench_book", tick=tick,
                        offers=[[o["id"], "ask" if (o.get("want") or {}).get("cash") else "bid",
                                 (o.get("want") or {}).get("cash") or (o.get("give") or {}).get("cash"),
                                 o.get("expires_tick")] for o in bench])
                elif book.get("offers"):
                    log(event="plan", tick=tick, bench=0, offers=len(book.get("offers") or []), matches=len(plan))
                for sell, buy, price in plan:
                    try:
                        broker.match(sell, buy, price)
                        log(event="match", tick=tick, sell=sell, buy=buy, price=price)
                    except BazaarError as e:
                        log(event="match_refused", tick=tick, sell=sell, buy=buy, price=price, error=str(e)[:200])
            elif time.time() - last_beat >= 30:
                last_beat = time.time()
                log(event="heartbeat", tick=tick, fee_bps=fee_bps, offers=len(book.get("offers") or []))
        except BazaarError as e:
            log(event="read_failed", error=str(e)[:200])
            if "bad_key" in str(e) or "too_many_failures" in str(e):
                # a wrong key retried every half second gets the whole server address blocked: wait a minute
                time.sleep(60)
        except Exception as e:  # keep the loop up: a dead broker scores 0 for the rest of the session
            log(event="loop_error", error=repr(e)[:200])
        time.sleep(0.5)


if __name__ == "__main__":
    main()
