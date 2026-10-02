"""Team 13's broker for our venue, built to beat the free stall on the Market Test.

    source ../bazaar.env && python3 smart_broker.py     # reads the broker key from state.json (or BROKER_KEY)

The Market Test scores the share of the possible gains (between TRUE limits) we realise. The stall crosses every
quote pair the moment it crosses. That loses value in two ways:
  1. it pairs an extra-marginal trader (a seller whose true cost is above the market price, a buyer whose value is
     below it) with someone who could have traded with a better partner a few ticks later;
  2. it never knows who is about to leave.
Bench traders shade their quotes away from hidden limits and most relax them as patience runs out, so we:
  - track every bench offer's quote over time and estimate its limit (quote minus learned shading);
  - compute the efficient set per run on the estimates and match intramarginal pairs as soon as they cross;
  - let extra-marginal pairs trade only late in the session, or when a trader looks about to leave;
  - fall back to the stall's plan if anything is off, so we never do worse than half points by construction.
Real offers on our venue are crossed card by card exactly like the starter (lowest ask vs highest bid covering fee).
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

from bazaar_sdk import BazaarError, Broker
import starter_plans

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
                ask, bid = s[2], b[2]
                price = next((p for p in range((ask + bid) // 2, ask - 1, -1) if p + fee(p) <= bid), None) if bid >= ask else None
                if price is not None:
                    plan.append((s[0], b[0], price))
                    used |= {s[0], b[0]}
                    break
    return plan


def main():
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
    """The broker loop. The agent starts it in a thread as soon as our venue is open (market.py)."""
    broker, tracker, seen = Broker(url, key), Tracker(), None
    log(event="start")
    while True:
        try:
            refresh_session_ticks(url)
            tick, book = broker.clock()["tick"], broker.book()
            bench = book.get("bench_offers") or []
            tracker.update(tick, bench)
            fee_bps, per_card = book.get("fee_bps", 0), book.get("fee_per_card", 0)

            def fee(p):
                return math.ceil(fee_bps * p / 10000) + per_card
            now = (tick, sorted(o["id"] for o in bench + (book.get("offers") or [])))
            if now != seen:
                seen = now
                try:
                    plan = smart_bench_plan(book, tracker, tick, fee)
                except Exception as e:  # never worse than the stall
                    log(event="smart_plan_failed", error=repr(e))
                    plan = starter_plans.bench_plan(book)
                plan += starter_plans.public_plan(book)
                if bench and tick % 4 == 0:
                    log(event="book", tick=tick, bench=len(bench), sample=bench[:2], keys=sorted(book))
                for sell, buy, price in plan:
                    try:
                        broker.match(sell, buy, price)
                        log(event="match", tick=tick, sell=sell, buy=buy, price=price)
                    except BazaarError as e:
                        log(event="match_refused", tick=tick, sell=sell, buy=buy, price=price, error=str(e))
        except BazaarError as e:
            log(event="read_failed", error=str(e))
        time.sleep(1.0)


if __name__ == "__main__":
    main()
