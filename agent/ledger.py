"""Card ledger: who probably holds a card, from public evidence only (like reading the table in poker).

Hidden: starting hands, pack contents (pack.opened rarely names its best card), and /api/cards (401 for others).
Seen: every settlement (which card moved from whom to whom), Abuela's gifts, every listing (a team lists only what
it holds), each team's rarest card on the leaderboard. Each team's set lean (agent/team_intel.py) says whether it
would part with a card: a team that dumps a set sells its cards cheaply.

    python3 agent/ledger.py who MAL-03 MAL-07 MAL-09 MAL-10     # likely holders, best first
    python3 agent/ledger.py team t12                            # what we know t12 holds
"""
import collections
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from team_intel import lean  # noqa: E402

FEED = os.path.join(HERE, "..", "logs", "feed.jsonl")
if not os.path.exists(FEED):
    FEED = os.path.join(HERE, "..", "data", "feed.jsonl")
URL = "https://bazaar.causaprima.ai"


def load_feed():
    with open(FEED) as f:
        return [json.loads(line) for line in f if line.strip()]


def build(events, leaderboard=None):
    """{team: {ref: [count_estimate, last_tick, evidence]}} from public events, oldest first."""
    hold = collections.defaultdict(dict)

    def add(team, ref, tick, why, n=1):
        if not (team and team[:1] == "t" and team[1:].isdigit()):
            return
        c = hold[team].setdefault(ref, [0, tick, why])
        c[0], c[1], c[2] = max(0, c[0] + n), tick, why

    for e in sorted(events, key=lambda e: e["id"]):
        p, t, tick = e.get("payload") or {}, e.get("type"), e.get("tick")
        if t == "settlement":
            for i in p.get("items") or []:
                if i.get("kind") != "card":
                    continue
                add(i.get("to"), i["ref"], tick, f"got it t{tick} @{p.get('price')}")
                add(i.get("frm"), i["ref"], tick, f"gave it away t{tick} @{p.get('price')}", -1)
        elif t == "gift.given":
            for ref in p.get("cards") or []:
                add(p.get("team"), ref, tick, f"gift t{tick}")
        elif t == "offer.listed":
            o = p.get("offer") or {}
            for a in (o.get("give") or {}).get("assets") or []:
                c = hold[o.get("maker")].get(a["ref"]) if o.get("maker") in hold else None
                if not c or c[0] <= 0:
                    add(o.get("maker"), a["ref"], tick, f"listed it t{tick}")
    for t in (leaderboard or {}).get("teams", []):
        r = (t.get("rarest") or {}).get("ref")
        if r and hold[t["team"]].get(r, [0])[0] <= 0:
            add(t["team"], r, None, "rarest card on the leaderboard")
    return hold


def who(refs, hold, leans):
    for ref in refs:
        rows = []
        for team, cards in hold.items():
            c = cards.get(ref)
            if c and c[0] > 0:
                set_lean = leans.get(team, {}).get(ref[:3], 0)
                rows.append((set_lean, team, c))
        print(f"\n{ref}: {len(rows)} likely holder(s)" + ("" if rows else " (none seen: starting hands and packs are hidden)"))
        for set_lean, team, c in sorted(rows):  # most willing sellers first: they dump this set
            mood = "dumps this set" if set_lean < 0 else ("collects this set" if set_lean > 0 else "neutral")
            print(f"  {team}: x{c[0]} ({c[2]}) | {mood} ({set_lean:+.1f})")


def main():
    events = load_feed()
    try:
        with urllib.request.urlopen(URL + "/api/leaderboard", timeout=10) as r:
            lb = json.load(r)
    except Exception:
        lb = None
    hold, leans = build(events, lb), lean(events)
    if len(sys.argv) >= 3 and sys.argv[1] == "who":
        who(sys.argv[2:], hold, leans)
    elif len(sys.argv) >= 3 and sys.argv[1] == "team":
        for ref, c in sorted(hold.get(sys.argv[2], {}).items()):
            if c[0] > 0:
                print(f"  {ref} x{c[0]} ({c[2]})")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
