"""Team 13's agent: observe -> decide -> act, once per tick, for the whole weekend.

    source ../bazaar.env && python3 agent.py            # live
    source ../bazaar.env && python3 agent.py --dry-run  # decide and log, never write

Modules, in priority order each tick (one accept per team per tick is shared between them):
  duels    the tournament: never cross our limit, settle before the pie decays
  haggler  the dealer ladder: Boulware concessions, kind words, take finals inside our cap
  trader   team trades at our private values: take good board offers, list spares, bid for what we value most
  venue    open our own market as soon as we reach level 2 (the broker runs in smart_broker.py)

Every decision is appended to logs/decisions.jsonl; the dashboard shows it live.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

from bazaar_sdk import Bazaar, BazaarError
from duels import Duels
from haggler import Haggler
from trader import Trader
from values import Values
from intel import Intel
import strategy

HERE = Path(__file__).parent
LOGS = HERE / "logs"
STATE = HERE / "state.json"
VENUE_BOND = 270


class DryApi:
    """Wraps the SDK: reads pass through, writes are logged and skipped."""
    WRITES = {"open_thread", "say", "accept", "list_offer", "cancel", "open_pack", "duel_say", "duel_accept",
              "open_venue", "close_thread", "flag", "set_fee", "close_venue"}

    def __init__(self, api, log):
        self._api, self._log = api, log

    def __getattr__(self, name):
        fn = getattr(self._api, name)
        if name not in self.WRITES:
            return fn

        def skipped(*a, **k):
            self._log("dry", name, args=[str(x) for x in a], kwargs={k2: str(v) for k2, v in k.items()})
            return {"id": -1, "cards": [], "broker_key": None}
        return skipped


class Context:
    def __init__(self, api, dry=False):
        LOGS.mkdir(exist_ok=True)
        self._logf = open(LOGS / "decisions.jsonl", "a", buffering=1)
        self.api = DryApi(api, self.log) if dry else api
        self.raw = api
        self.state = json.loads(STATE.read_text()) if STATE.exists() else {}
        self.clock, self.me, self.threads, self.my_offers, self.board, self.dealers = {}, {}, [], [], [], []
        self.values = None
        self._accepts = {}
        self.S = strategy.load()
        self.intel = None

    # ---------------------------------------------------------------- logging & state
    def log(self, module, action, **detail):
        rec = {"ts": round(time.time(), 1), "tick": self.clock.get("tick"), "module": module, "action": action, **detail}
        line = json.dumps(rec, default=str)
        self._logf.write(line + "\n")
        print(line[:300], flush=True)

    def save(self):
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, default=str))
        tmp.replace(STATE)

    # ---------------------------------------------------------------- budgets & rules
    def limit(self, name, default):
        return (self.clock.get("limits") or {}).get(name, default)

    def accepts_left(self, kind="team"):
        return self._accepts.get(kind, 0) > 0

    def take_accept(self, kind="team"):
        if self._accepts.get(kind, 0) <= 0:
            return False
        self._accepts[kind] -= 1
        return True

    def reserve(self):
        """Cash we keep back: the venue bond until our market is open (it is worth up to 30 points)."""
        return 10 if self.state.get("venue") else self.S["reserve_cash"]

    def locked_assets(self):
        ids = set()
        for o in self.my_offers:
            for a in (o.get("give") or {}).get("assets") or []:
                ids.add(a["id"] if isinstance(a, dict) else a)
        for th in self.threads:
            topic = (th.get("topic") or {}).get("sell") or {}
            if th["status"] == "open" and topic.get("asset"):
                ids.add(topic["asset"])
        return ids

    def open_new_packs(self):
        try:
            me = self.raw.me()
        except BazaarError:
            return
        for a in me.get("assets", []):
            if a.get("kind") == "pack":
                try:
                    res = self.api.open_pack(a["id"])
                    self.log("pack", "opened", pack=a["ref"], cards=[(c.get("ref"), c.get("rarity"), c.get("serial")) for c in res.get("cards", [])])
                except BazaarError as e:
                    self.log("pack", "open_refused", pack=a["id"], error=str(e))

    # ---------------------------------------------------------------- observe
    def observe(self, full=False):
        api = self.raw
        self.S = strategy.load()  # the dashboard's Strategy tab writes strategy.json
        self.me = api.me()
        if self.intel is None:
            self.intel = Intel(LOGS / "feed_events.jsonl", url=self.raw.url)
        if self.clock.get("tick", 0) - self.state.get("intel_tick", -99) >= 2:  # the whole public feed, every 2 ticks
            self.intel.refresh()
            self.state["intel_tick"] = self.clock.get("tick", 0)
        self.threads = api.my_threads().get("threads", [])
        self.my_offers = api.my_offers().get("offers", [])
        try:
            self.board = api.board("rastro").get("offers", [])
        except BazaarError:
            self.board = []
        if full or not self.dealers:
            self.dealers = api.dealers().get("personas", [])
            self.catalog = api.catalog()
            self.levels = api.levels().get("levels", [])
        if self.values is None or full:
            self.values = Values(self.catalog, self.me)
            self.intel.set_catalog(self.catalog)
        else:
            self.values.update(self.me)
        self.state["ladder_deals"] = sum(1 for t in self.threads if t.get("kind") == "persona" and t["status"] == "deal")
        self._accepts = {"team": self.limit("accepts_per_team_per_tick", 1), "duel": 3}


class Venue:
    """Open our market as soon as the rules allow; the broker key goes to state.json for smart_broker.py."""

    def __init__(self, ctx):
        self.ctx = ctx

    def step(self):
        ctx = self.ctx
        if not ctx.S["enable_venue"] or ctx.state.get("venue") or ctx.me.get("level", 1) < 2:
            return
        if ctx.me["cash"] < VENUE_BOND + 5:
            ctx.log("venue", "waiting_for_cash", cash=ctx.me["cash"])
            return
        last = ctx.state.get("venue_try_tick", -99)
        if ctx.clock.get("tick", 0) - last < 10:
            return
        ctx.state["venue_try_tick"] = ctx.clock.get("tick", 0)
        try:
            res = ctx.api.open_venue("Mercado Trece", fee_bps=int(ctx.S["venue_fee_bps"]), fee_per_card=0, rules={"mechanism": "board"})
            ctx.state["venue"] = res.get("venue") or res.get("id")
            ctx.state["broker_key"] = res.get("broker_key")
            ctx.log("venue", "opened", venue=ctx.state["venue"])
        except BazaarError as e:
            ctx.log("venue", "open_refused", error=str(e))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-trade", action="store_true", help="skip team trading")
    args = ap.parse_args()
    api = Bazaar(os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai"), os.environ["BAZAAR_KEY"], wait_on_tick=False)
    ctx = Context(api, dry=args.dry_run)
    modules = [("duels", Duels(ctx)), ("haggler", Haggler(ctx)), ("venue", Venue(ctx))]
    if not args.no_trade:
        modules.append(("trader", Trader(ctx)))
    switch = {"duels": "enable_duels", "haggler": "enable_haggler", "trader": "enable_trader", "venue": "enable_venue"}
    ctx.log("agent", "start", dry=args.dry_run)
    last_tick, n = None, 0
    while True:
        try:
            ctx.clock = api.clock()
            if ctx.clock.get("paused") or ctx.clock.get("doors") != "open":
                if last_tick != "idle":
                    ctx.log("agent", "idle", paused=ctx.clock.get("paused"), doors=ctx.clock.get("doors"))
                    last_tick = "idle"
                time.sleep(5)
                continue
            if ctx.clock["tick"] == last_tick:
                time.sleep(max(0.2, min(5.0, float(ctx.clock.get("next_tick_in", 1)) + 0.3)))
                continue
            last_tick = ctx.clock["tick"]
            ctx.observe(full=(n % 20 == 0))
            n += 1
            for name, mod in modules:
                if not ctx.S.get(switch[name], 1):
                    continue
                try:
                    mod.step()
                except BazaarError as e:
                    if e.code not in ("wait_for_tick", "rate_limited"):
                        ctx.log(name, "error", error=str(e))
                except Exception as e:
                    ctx.log(name, "crash", error=repr(e), trace=traceback.format_exc()[-800:])
            ctx.save()
        except BazaarError as e:
            ctx.log("agent", "api_error", error=str(e))
            time.sleep(3)
        except KeyboardInterrupt:
            ctx.save()
            sys.exit(0)
        except Exception as e:
            ctx.log("agent", "crash", error=repr(e), trace=traceback.format_exc()[-800:])
            time.sleep(3)


if __name__ == "__main__":
    main()
