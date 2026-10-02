"""Guess every team's set preferences from the public feed (logs/feed.jsonl).

Every team has the same six multipliers, shuffled. What a team pays for (dealer buys, Rastro bids, cards it takes)
says it values that set; what it sells or lists says it does not. Duplicates muddy this, so it is a lean, not a fact.

    python3 agent/team_intel.py            # table: team x set lean, plus who wants what right now
"""
import collections
import json
import os

FEED = os.path.join(os.path.dirname(__file__), "..", "logs", "feed.jsonl")
SETS = ["LAV", "MAL", "LAT", "SAL", "RET", "CHA"]


def set_of(ref: str) -> str:
    return ref.split("-")[0]


def load():
    with open(FEED) as f:
        return [json.loads(line) for line in f if line.strip()]


def lean(events) -> dict:
    score = collections.defaultdict(lambda: collections.Counter())
    for e in events:
        p = e["payload"]
        if e["type"] == "settlement":
            for i in p.get("items", []):
                if i.get("kind") != "card":
                    continue
                if i.get("to", "").startswith("t"):
                    score[i["to"]][set_of(i["ref"])] += 1      # paid for it
                if i.get("frm", "").startswith("t"):
                    score[i["frm"]][set_of(i["ref"])] -= 1     # let it go
        elif e["type"] == "offer.listed":
            o = p.get("offer", {})
            maker = o.get("maker", "")
            if not maker.startswith("t"):
                continue
            for a in (o.get("give") or {}).get("assets", []):
                score[maker][set_of(a["ref"])] -= 0.5          # offered away
            for t in (o.get("want") or {}).get("types", []) + (o.get("want") or {}).get("cards", []):
                ref = t.split(":")[-1]
                score[maker][set_of(ref)] += 1                  # bid for it
        elif e["type"] == "thread.opened" and p.get("kind") == "persona":
            topic = p.get("topic") or {}
            card = (topic.get("buy") or {}).get("card")
            if card:
                score[p["team"]][set_of(card)] += 0.5          # asked a dealer for it
    return score


def main() -> None:
    sc = lean(load())
    print("team  " + " ".join(f"{s:>5}" for s in SETS) + "   likes            dislikes")
    for team in sorted(sc):
        row = sc[team]
        likes = [s for s, v in row.most_common() if v > 0][:2]
        dislikes = [s for s, v in sorted(row.items(), key=lambda kv: kv[1]) if v < 0][:2]
        print(f"{team:5} " + " ".join(f"{row.get(s, 0):5.1f}" for s in SETS) + f"   {','.join(likes):16} {','.join(dislikes)}")


if __name__ == "__main__":
    main()
