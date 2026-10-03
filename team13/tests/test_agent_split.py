"""One agent per person on one team key: own state and lock, dealer scope, budget per 2 game hours, accept slots.

    python3 tests/test_agent_split.py      (no network)
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.update(AGENT_NAME="Sergio", AGENT_DEALERS="abuela,pilar", AGENT_BUDGET_2H="40", AGENT_SLOT="1", AGENT_SLOTS="3",
                  AGENT_KNOBS="enable_haggler=1,workshop_accumulate=0,not_a_knob=5")
import agent  # noqa: E402
from haggler import Haggler  # noqa: E402


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    return ok


results = []
results.append(check("own state file and lock", agent.state_path_for("haggler").name == "state-sergio.json"
                     and agent.role_label("haggler") == "sergio", agent.state_path_for("haggler").name))

agent.LOGS = Path(tempfile.mkdtemp())  # never write test lines into the real agent log
ctx = agent.Context(api=None, dry=True, state_path=agent.LOGS / "state-sergio.json")
ctx.me, ctx.S = {"cash": 300, "venue": {"status": "open", "owner": "t13"}, "id": "t13"}, {**ctx.S, "day_budget": 120, "cash_floor": 40}
ctx.clock = {"tick": 100, "t_hours": 8.5, "today": "sat"}
results.append(check("own knobs over the shared strategy (unknown ones dropped)",
                     agent.load_strategy()["enable_haggler"] == 1 and agent.load_strategy()["workshop_accumulate"] == 0
                     and "not_a_knob" not in agent.load_strategy(), agent.agent_knobs()))
results.append(check("scope and name read from the environment", ctx.dealer_scope == {"abuela", "pilar"} and ctx.name == "sergio"))

# budget per 2 game hours: 40 P in the 8-10 h window, then nothing until 10 h
results.append(check("fresh window: 40 P", ctx.budget_left() == 40, ctx.budget_left()))
ctx.record_spend(30, "pack")
results.append(check("after 30 P: 10 left", ctx.budget_left() == 10, ctx.budget_left()))
ctx.record_spend(10, "card")
results.append(check("spent: buys nothing more this window", ctx.budget_left() == 0, ctx.budget_left()))
ctx.clock["t_hours"] = 10.1
results.append(check("next window: 40 P again", ctx.budget_left() == 40, ctx.budget_left()))

# the team's single accept per tick: slot 1 of 3 owns ticks 1, 4, 7...
ctx._accepts = {"team": 1, "duel": 3}
ctx.clock["tick"] = 100  # 100 % 3 == 1
results.append(check("accepts on its own tick", ctx.take_accept("team")))
ctx._accepts = {"team": 1, "duel": 3}
ctx.clock["tick"] = 101
results.append(check("never on another agent's tick", not ctx.take_accept("team")))
results.append(check("duel accepts are not slotted", ctx.take_accept("duel")))

# dealer scope: Chato and Los Pícaros are someone else's
ctx.dealers = [{"id": d, "status": "active", "level": i} for i, d in enumerate(("abuela", "chato", "pilar", "picaros"), start=1)]
ctx.S["haggle_level_filter"] = ""  # no level filter: the scope alone must decide
ctx.me["unlocked"], ctx.threads, seen = ["abuela", "chato", "pilar", "picaros"], [], []
h = Haggler(ctx)
h.choose_topic = lambda d: seen.append(d["id"])  # record which dealers it would open a conversation with
h._visit = lambda d: seen.append(d["id"])
h.step()
results.append(check("only its own dealers", set(seen) == {"abuela", "pilar"}, sorted(set(seen))))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
