"""Sales to teams never go under the card's median in team-to-team trades (4+ trades), and the median ignores dealers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from intel import Intel  # noqa: E402
from trader import Trader  # noqa: E402


def settle(i, ref, price, parties=("t02", "t09")):
    return {"id": i, "type": "settlement", "tick": i, "payload": {
        "parties": list(parties), "price": price, "items": [{"kind": "card", "ref": ref, "frm": parties[0], "to": parties[1]}]}}


def make_intel(tmp, evs):
    it = Intel(Path(tmp) / "none.jsonl")
    it.events = {e["id"]: e for e in evs}
    return it


def test_median_and_floor(tmp_path):
    evs = [settle(i, "MAL-10", p) for i, p in enumerate([30, 60, 65, 70, 80])]
    evs += [settle(10, "MAL-10", 5, ("t02", "abuela"))]                    # dealer trade: ignored
    evs += [settle(20 + i, "LAV-02", 10) for i in range(3)]               # too few trades
    it = make_intel(tmp_path, evs)
    assert it.team_median("MAL-10") == 65
    assert it.team_median("LAV-02") is None

    class Ctx:
        S = {"sell_at_median": 1}
        intel = it
    t = Trader.__new__(Trader)
    t.ctx = Ctx()
    assert t.market_floor(["MAL-10"]) == 65
    assert t.market_floor(["LAV-02"]) == 0
    Ctx.S = {"sell_at_median": 0}
    assert t.market_floor(["MAL-10"]) == 0
