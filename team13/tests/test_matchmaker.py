"""Matchmaker, offline: pairs a bid elsewhere with another team's spare, invites both sides without naming the other,
respects the cooldown and closes its threads; off unless enable_matchmaker.

    python3 tests/test_matchmaker.py
"""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import strategy, matchmaker  # noqa: E402

events = [{"id": 1, "tick": 10, "type": "offer.listed", "payload": {"offer": {"id": 501, "maker": "t06"}}},
          {"id": 2, "tick": 11, "type": "settlement", "payload": {"items": [
              {"kind": "card", "ref": "LAT-07", "frm": "t02", "to": "t12"}, ]}},
          {"id": 3, "tick": 12, "type": "settlement", "payload": {"items": [
              {"kind": "card", "ref": "LAT-07", "frm": "t03", "to": "t12"}, ]}}]
boards = {"v07": [{"id": 501, "give": {"cash": 14}, "want": {"types": ["card:LAT-07"]}}]}
m = matchmaker.find_matches(events, boards, "t13", "v24", {"t12": {"LAT-07": [2, 12, "got it"]}}, {"t12": {"LAT": -5.5}})
assert m and m[0][:4] == ("LAT-07", "t06", 14, "t12"), m

tmp = tempfile.mkdtemp(); matchmaker.FEED_STORE = os.path.join(tmp, "feed.jsonl")
open(matchmaker.FEED_STORE, "w").write("\n".join(json.dumps(e) for e in events))


class Api:
    def __init__(s): s.opened, s.said, s.closed = [], [], []
    def open_thread(s, team, venue=None): s.opened.append((team, venue)); return {"id": 700 + len(s.opened)}
    def say(s, tid, text): s.said.append((tid, text))
    def close_thread(s, tid): s.closed.append(tid)


class Ctx:
    def __init__(s):
        s.api, s.S, s.state, s.logs = Api(), strategy.defaults(), {"venue": "v24"}, []
        s.clock, s.me, s.boards, s.leaderboard = {"tick": 100}, {"id": "t13"}, boards, []
        s.raw = type("R", (), {"url": "x"})()
    def log(s, *a, **k): s.logs.append((a, k))


c = Ctx(); mm = matchmaker.Matchmaker(c)
mm._broker_key = lambda: None
mm.step(); assert not c.api.opened  # off by default
c.S["enable_matchmaker"] = 1
mm.step()
assert c.api.opened == [("t12", "rastro")], c.api.opened  # seller first
text = c.api.said[0][1]
assert "LAT-07" in text and "14 P" in text and "t06" not in text and "Team 6" not in text  # never names the buyer
for t in range(101, 104):
    c.clock["tick"] = t; mm.step()
assert ("t06", "rastro") in c.api.opened and all("t12" not in x[1] for x in c.api.said)  # buyer never hears the seller
c.clock["tick"] = 110; mm.step(); assert c.api.closed  # threads closed after a few ticks
n = len(c.api.opened)
c.clock["tick"] = 125; mm.step(); c.clock["tick"] = 126; mm.step()
assert len(c.api.opened) == n  # same (team, card) not invited again within the cooldown
print("matchmaker ok:", len(c.api.opened), "invitations, both anonymous")
