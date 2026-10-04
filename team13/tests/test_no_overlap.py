"""Sergio's trading rules, offline: team buys stay under 85% of our value, no trade on El Rastro or a close rival's
market, and our agents never sell the card another one is passing on (arbitrage) or selling to a dealer.

    python3 tests/test_no_overlap.py      (no network)
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent  # noqa: E402
import arbitrage  # noqa: E402
import strategy  # noqa: E402
from trader import Trader  # noqa: E402


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    return ok


results = []
tmp = Path(tempfile.mkdtemp())
agent.LOGS = tmp  # never write test lines into the real agent log


class V:
    cards = {"LAV-07": {"rarity": "uncommon", "set": "LAV"}}
    assets = [{"id": 10, "ref": "LAV-07"}, {"id": 11, "ref": "LAV-07"}, {"id": 12, "ref": "SAL-02"}]


ctx = agent.Context(api=None, dry=True, state_path=tmp / "state-sergio.json")
ctx.values, ctx.clock = V(), {"tick": 500}
ctx.me = {"id": "t13", "cash": 200, "assets": V.assets}
results.append(check("no peers: nothing locked", ctx.peer_busy_assets() == set()))

(tmp / "state-anton.json").write_text(json.dumps({"arb": {"active": {"ref": "LAV-07", "phase": "wait_card"}}}))
ctx.clock = {"tick": 501}
results.append(check("anton's arbitrage card: both copies locked here", {10, 11} <= ctx.locked_assets(reserved=False),
                     ctx.peer_busy_assets()))
old = time.time() - 600
os.utime(tmp / "state-anton.json", (old, old))
ctx.clock = {"tick": 502}
results.append(check("a stopped agent's old file locks nothing", ctx.peer_busy_assets() == set()))
(tmp / "state-sergio.json").write_text(json.dumps({"arb": {"active": {"ref": "SAL-02"}}}))
ctx.clock = {"tick": 503}
results.append(check("our own state file is not a peer", 12 not in ctx.peer_busy_assets()))

# arbitrage: the copy it bought is in a teammate's dealer sale -> it waits, never sells it
class A:
    def __init__(s): s.accepted = []
    def accept(s, oid, assets=None): s.accepted.append((oid, assets))


class C:
    def __init__(s):
        s.S, s.state, s.logs, s.clock = {**strategy.defaults(), "enable_arbitrage": 1}, {}, [], {"tick": 700}
        s.values, s.api = V(), A()
    def log(s, *a, **k): s.logs.append((a, k))
    def take_accept(s): return True
    def locked_assets(s, reserved=True): return {11}
    def day_key(s): return "sun"


c = C()
c.state["arb"] = {"active": {"ref": "LAV-07", "phase": "wait_card", "held_before": 1, "last_tick": 699, "bid": 5,
                             "bid_price": 40, "value": 20, "net_in": 37, "cap": 25, "paid": 22}, "scan": 0, "done": {}}
arbitrage.Arbitrage(c).step()
results.append(check("arbitrage leaves a promised copy alone", c.api.accepted == [] and c.state["arb"]["active"]["sell_tries"] == 1))
c.locked_assets = lambda reserved=True: set()
c.clock = {"tick": 701}
arbitrage.Arbitrage(c).step()
results.append(check("then sells the copy it bought", c.api.accepted == [(5, [11])], c.api.accepted))

# trader: 85% cap on team buys, no El Rastro, only safe markets
t = Trader.__new__(Trader)
t.ctx = ctx
ctx.S = {**ctx.S, "trade_buy_margin": 0.15, "trade_no_rastro": 1, "trade_safe_only": 1, "rival_margin": 6}
results.append(check("buy cap is 85% of our value", t.buy_cap(100) == 85.0))
ctx.venues = [{"venue": "rastro", "owner": "world", "status": "open"}, {"venue": "v13", "owner": "t11", "status": "open"},
              {"venue": "v01", "owner": "t06", "status": "open"}]
ctx.leaderboard = [{"team": "t13", "score": 27}, {"team": "t11", "score": 8}, {"team": "t06", "score": 27}]
ctx.me["score"] = {"score": 27}
results.append(check("no trade on El Rastro", not t.venue_ok("rastro") and not t.venue_ok(None)))
results.append(check("a team far behind us: ok; a close rival: no", t.venue_ok("v13") and not t.venue_ok("v01"),
                     [t.venue_ok("v13"), t.venue_ok("v01")]))
ctx.S = {**ctx.S, "trade_no_rastro": 0, "trade_safe_only": 0, "trade_buy_margin": 0}
results.append(check("knobs off: the old behaviour", t.venue_ok("rastro") and t.venue_ok("v01") and t.buy_cap(100) == 100))
results.append(check("new knobs are known (AGENT_KNOBS keeps them)",
                     strategy.clean({"trade_no_rastro": 1, "trade_safe_only": 1, "trade_buy_margin": 0.15})
                     == {"trade_no_rastro": 1, "trade_safe_only": 1, "trade_buy_margin": 0.15}))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
