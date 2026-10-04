"""Concierge, offline: a bid on our market reaches the holders of the card (spares first), an ask reaches the
collectors of its set (teams bidding for it elsewhere first); never the maker, never twice, the maker never named.

    python3 tests/test_concierge.py
"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import strategy, concierge  # noqa: E402

events = [{"id": 1, "tick": 1, "type": "offer.listed", "payload": {"offer": {"id": 900, "maker": "t06"}}},
          {"id": 2, "tick": 1, "type": "offer.listed", "payload": {"offer": {"id": 901, "maker": "t02"}}},
          {"id": 3, "tick": 1, "type": "offer.listed", "payload": {"offer": {"id": 50, "maker": "t18"}}}]
hold = {"t12": {"LAT-07": [3, 1, "x"]}, "t07": {"LAT-07": [1, 1, "x"]}, "t09": {"LAT-07": [1, 1, "x"]}, "t06": {"LAT-07": [1, 1, "x"]}}
leans = {"t12": {"LAT": -29}, "t07": {"LAT": -5, "RET": 100}, "t09": {"LAT": 4}, "t16": {"RET": 142}, "t18": {"RET": 3}, "t02": {"RET": 10}}
book = [{"id": 900, "give": {"cash": 14}, "want": {"types": ["card:LAT-07"]}},
        {"id": 901, "give": {"assets": [{"id": 5, "ref": "RET-09"}]}, "want": {"cash": 75}}]
boards = {"rastro": [{"id": 50, "give": {"cash": 60}, "want": {"types": ["card:RET-09"]}}]}
p = {(side, ref): teams for oid, side, ref, price, teams, cross in concierge.plan(book, events, boards, "t13", "v24", hold, leans)}
assert p[("bid", "LAT-07")] == ["t12", "t07", "t09"], p   # spare first, then a dumped set; never the maker t06
assert p[("ask", "RET-09")][0] == "t18" and "t16" in p[("ask", "RET-09")] and "t02" not in p[("ask", "RET-09")], p
# a team whose own offer elsewhere crosses ours comes first: t15 sells LAT-07 at 12 (our bid pays 14), t05 bids 80 for
# RET-09 (our ask is 75); t11 asks 20 for LAT-07, above our bid: not a crossing
ev2 = events + [{"id": 4, "tick": 1, "type": "offer.listed", "payload": {"offer": {"id": 51, "maker": "t15"}}},
                {"id": 5, "tick": 1, "type": "offer.listed", "payload": {"offer": {"id": 52, "maker": "t05"}}},
                {"id": 6, "tick": 1, "type": "offer.listed", "payload": {"offer": {"id": 53, "maker": "t11"}}}]
b2 = {"rastro": boards["rastro"] + [{"id": 51, "give": {"assets": [{"id": 9, "ref": "LAT-07"}]}, "want": {"cash": 12}},
                                    {"id": 52, "give": {"cash": 80}, "want": {"types": ["card:RET-09"]}},
                                    {"id": 53, "give": {"assets": [{"id": 8, "ref": "LAT-07"}]}, "want": {"cash": 20}}]}
q = {(side, ref): (teams, cross) for oid, side, ref, price, teams, cross in concierge.plan(book, ev2, b2, "t13", "v24", hold, leans)}
assert q[("bid", "LAT-07")][0][0] == "t15" and q[("bid", "LAT-07")][1] == {"t15": ("rastro", 12, 51)}, q
assert "t11" not in q[("bid", "LAT-07")][0], q
assert q[("ask", "RET-09")][0][0] == "t05" and q[("ask", "RET-09")][1] == {"t05": ("rastro", 80, 52)}, q
# a card on an open auction lot is left to its auction
assert ("bid", "LAT-07") not in {(side, ref) for _, side, ref, *_ in concierge.plan(book, ev2, b2, "t13", "v24", hold, leans, skip={"LAT-07"})}

concierge.FEED_STORE = os.path.join(tempfile.mkdtemp(), "feed.jsonl")
open(concierge.FEED_STORE, "w").write("\n".join(json.dumps(e) for e in events))


class Api:
    def __init__(s): s.opened, s.said, s.closed = [], [], []
    def open_thread(s, team, venue=None): s.opened.append(team); return {"id": 700 + len(s.opened)}
    def say(s, tid, text): s.said.append((tid, text))
    def close_thread(s, tid): s.closed.append(tid)


class Ctx:
    def __init__(s):
        s.api, s.S, s.state, s.logs = Api(), {**strategy.defaults(), "enable_concierge": 1}, {"venue": "v24"}, []
        s.clock, s.me, s.leaderboard, s.values = {"tick": 100}, {"id": "t13"}, [], None
    def log(s, *a, **k): s.logs.append((a, k))
    def public_get(s, path):
        if path == "/api/venues": return {"venues": [{"venue": "v24", "status": "open", "owner": "t13"}, {"venue": "rastro", "status": "open"}]}
        return {"offers": book if path.endswith("/v24/offers") else boards["rastro"]}


import ledger, team_intel  # noqa: E402
ledger.build = lambda ev, lb=None: hold
team_intel.lean = lambda ev: leans
c = Ctx(); k = concierge.Concierge(c)
for t in range(100, 108):
    c.clock["tick"] = t; k.step()
texts = [x[1] for x in c.api.said]
assert len(c.api.opened) >= 3 and all("t06" not in x and "t02" not in x and "Team" not in x.replace("Team 13", "") for x in texts), texts
assert any("offer 900" in x and "14 P" in x for x in texts) and c.api.closed, (texts, c.api.closed)
n = len(c.api.opened)
for t in range(108, 140):
    c.clock["tick"] = t; k.step()
sent = c.state["concierge"]["sent"]
assert len(sent) == len(c.api.opened) == len(set(sent)), (sent, c.api.opened)  # each team once per offer
print("concierge ok:", len(c.api.opened), "teams asked, crossing makers first, makers never named, each once")
