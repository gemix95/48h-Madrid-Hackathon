"""AGENT_ROLE / AGENT_BUDGET: several agents on one key split the modules, cap their own spend, and each guard only
cancels the offers its own process made."""
import sys
sys.path.insert(0, ".")
import agent
import guard


def test_roles():
    assert agent.parse_role("dealers") == {"duels", "haggler"}
    assert agent.parse_role("market") == {"venue", "trader", "flipper", "wtb", "loans"}
    assert agent.parse_role("") == agent.ROLES["all"]
    assert agent.parse_role(" Haggler , trader ") == {"haggler", "trader"}
    assert not agent.ROLES["dealers"] & agent.ROLES["market"]
    assert agent.ROLES["dealers"] | agent.ROLES["market"] == agent.ROLES["all"]
    try:
        agent.parse_role("haggler,nope")
        raise AssertionError("unknown module accepted")
    except SystemExit:
        pass
    print("roles OK: presets are disjoint and cover every module")


class FakeCtx:
    budget_left = agent.Context.budget_left
    spent_today = agent.Context.spent_today
    day_key = agent.Context.day_key
    reserve = agent.Context.reserve
    owns = agent.Context.owns

    def __init__(self, agent_budget=None, spent=0, cash=500):
        self.S = {"day_budget": 120, "cash_floor": 40, "reserve_cash": 0}
        self.clock, self.me = {"today": "sat"}, {"id": "t13", "cash": cash}
        self.state = {"venue": "v13", "spent": {"sat": spent}}
        self.agent_budget = agent_budget


def test_budget():
    assert FakeCtx().budget_left() == 120
    assert FakeCtx(agent_budget=60).budget_left() == 60
    assert FakeCtx(agent_budget=60, spent=45).budget_left() == 15
    assert FakeCtx(agent_budget=60, spent=70).budget_left() == 0
    assert FakeCtx(agent_budget=200, cash=100).budget_left() == 60  # still never below the cash floor
    print("budget OK: AGENT_BUDGET caps this agent's day")


class FakeValues:
    assets = [{"id": 7, "ref": "LAV-08"}]

    def loss_of_removing(self, refs):
        return 80  # the card is worth far more to us than the 49 asked

    def gain_of_adding(self, refs):
        return 0


class FakeApi:
    def __init__(self):
        self.cancelled = []

    def cancel(self, oid):
        self.cancelled.append(oid)

    def close_thread(self, tid):
        pass


def _guard_run(shared, owned):
    ctx = FakeCtx()
    ctx.values, ctx.shared = FakeValues(), shared
    ctx.state["owned"] = owned
    offers = [{"id": 4403, "maker": "t13", "thread": 460, "give": {"assets": [7]}, "want": {"cash": 49}},
              {"id": 4410, "maker": "t13", "give": {"assets": [7]}, "want": {"cash": 49}}]
    ctx.raw = type("Raw", (), {"my_offers": lambda self: {"offers": offers}})()
    ctx.api, ctx.my_offers, ctx.log = FakeApi(), offers, lambda *a, **k: None
    guard.team_caps = lambda: {}
    guard.Guard(ctx).step()
    return ctx.api.cancelled


def test_guard_respects_owner():
    assert _guard_run(False, {}) == [4403, 4410]                         # one agent: guards every team offer
    assert _guard_run(True, {}) == []                                     # split: a teammate's offers are theirs
    assert _guard_run(True, {"threads": [460]}) == [4403]                 # we spoke in that thread
    assert _guard_run(True, {"offers": [4410]}) == [4410]                 # we listed it
    print("guard OK: with roles split each agent cancels only its own offers")


def test_owned_api_records():
    ctx = FakeCtx()
    ctx.state = {}

    class Api:
        def say(self, tid, *a, **k):
            return {}

        def list_offer(self, *a, **k):
            return {"id": 99}
    api = agent.OwnedApi(Api(), ctx)
    api.say(460, "hola", price=10)
    api.list_offer({"cash": 5}, {"cards": ["LAV-08"]})
    assert ctx.state["owned"] == {"threads": [460], "offers": [99]}
    assert ctx.owns({"id": 1, "thread": 460}) and ctx.owns({"id": 99}) and not ctx.owns({"id": 2, "thread": 461})
    print("owned OK: threads spoken in and offers listed are remembered")


if __name__ == "__main__":
    test_roles()
    test_budget()
    test_guard_respects_owner()
    test_owned_api_records()
