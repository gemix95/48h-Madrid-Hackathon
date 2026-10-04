"""team13/agents.json sets a named agent's AGENT_* env from the repo; an empty value removes it.

    python3 tests/test_agent_overrides.py      (no network)
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.update(AGENT_NAME="Sergio", AGENT_BUDGET_2H="30", AGENT_KNOBS="haggle_buy_cards=0", AGENT_DEALERS="abuela,pilar")
import agent  # noqa: E402


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))
    return ok


results = []
tmp = Path(tempfile.mkdtemp())
agent.OVERRIDES, agent.LOGS = tmp / "agents.json", tmp  # never write test lines into the real agent log
results.append(check("no file: env untouched", agent.apply_overrides() == {} and os.environ["AGENT_BUDGET_2H"] == "30"))

agent.OVERRIDES.write_text(json.dumps({"note": "x", "sergio": {"AGENT_BUDGET_2H": "", "AGENT_KNOBS": "haggle_buy_cards=1",
                                                                 "AGENT_NAME": "anton", "PATH": "/nope"},
                                       "emmanuele": {"AGENT_DEALERS": "chato"}}))
applied = agent.apply_overrides()
results.append(check("empty value removes the 2-hour cap", "AGENT_BUDGET_2H" not in os.environ, applied))
results.append(check("knobs replaced", agent.agent_knobs().get("haggle_buy_cards") == 1, agent.agent_knobs()))
results.append(check("name and non-AGENT_ keys ignored", os.environ["AGENT_NAME"] == "Sergio" and os.environ.get("PATH") != "/nope"))
results.append(check("another agent's entry not applied", os.environ["AGENT_DEALERS"] == "abuela,pilar"))
results.append(check("context has no 2-hour cap", agent.Context(api=None, dry=True, state_path=tmp / "s.json").budget_2h is None))

agent.OVERRIDES.write_text("{not json")
results.append(check("a typo changes nothing", agent.apply_overrides() == {}))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
