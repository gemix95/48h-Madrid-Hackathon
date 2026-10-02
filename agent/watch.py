"""Background watcher: our progress, the leaderboard, levels, announcements, other teams' dealer deals,
and cheap El Rastro listings of cards we value. Prints one line per *change* (for the Monitor tool);
everything raw goes to logs/watch.jsonl.

    python3 agent/watch.py [poll_seconds]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bazaar-kit"))
from bazaar_sdk import Bazaar, BazaarError  # noqa: E402

LOG = os.path.join(os.path.dirname(__file__), "..", "logs", "watch.jsonl")
POLL = float(sys.argv[1]) if len(sys.argv) > 1 else 60
b = Bazaar(os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai"), os.environ["BAZAAR_KEY"], wait_on_tick=False)


def out(msg: str) -> None:
    print(msg, flush=True)


def log(ev: str, data) -> None:
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps({"ts": time.time(), "ev": ev, "data": data}) + "\n")


state = {"rank": None, "score": None, "level": None, "cash": None, "levels": None, "top": None, "feed_id": 0,
         "team_levels": {}, "cheap_seen": set()}

INTERESTING = {"announce", "level.announced", "level.activated", "level.unlocked", "limits.changed", "duels.scheduled",
               "bench.started", "round.started", "venue.opened", "set.released"}


def poll() -> None:
    me = b.me()
    lb = b.leaderboard()
    log("me", {k: me[k] for k in ("cash", "level", "score", "unlocked")})
    log("leaderboard", lb)
    sc = me.get("score") or {}
    ours = (me["level"], me["cash"], round(sc.get("score", 0), 2), sc.get("rank"))
    if ours != (state["level"], state["cash"], state["score"], state["rank"]):
        out(f"US: level {ours[0]} cash {ours[1]} score {ours[2]} rank {ours[3]} "
            f"(neg {round(sc.get('negotiating', 0), 2)} mm {round(sc.get('market', 0), 2)} ladder {sc.get('ladder_points')})")
        state["level"], state["cash"], state["score"], state["rank"] = ours
    teams = lb.get("teams", [])
    top = [(t["team"], round(t["score"], 1)) for t in teams[:5]]
    if top != state["top"]:
        out("TOP5: " + ", ".join(f"{t}={s}" for t, s in top))
        state["top"] = top
    for t in teams:
        if t["team"] in state["team_levels"] and t["level"] != state["team_levels"][t["team"]]:
            out(f"LEVEL-UP: {t['team']} ({t['name']}) now level {t['level']}")
        state["team_levels"][t["team"]] = t["level"]
    lv = b.levels().get("levels", [])
    sig = json.dumps(lv, sort_keys=True)
    if sig != state["levels"]:
        out("LEVELS: " + "; ".join(f"{x.get('id') or x.get('name')}={x.get('status')}: {str(x.get('line') or x.get('how') or '')[:120]}"
                                   for x in lv))
        state["levels"] = sig
    for d in b.duels().get("duels", []):  # a live duel: say so once, with its raw shape, so a human can step in
        key = f"duel:{d.get('id')}:{d.get('status')}"
        if key not in state["cheap_seen"]:
            state["cheap_seen"].add(key)
            log("duel", d)
            out(f"DUEL {d.get('id')} status={d.get('status')} role={d.get('role')} limit={d.get('your_limit')} "
                f"rival_offer={d.get('rival_offer')} keys={sorted(d)}")
    feed = b.feed(150).get("events", [])
    for e in feed:
        if e["id"] <= state["feed_id"]:
            continue
        p = e.get("payload", {})
        if e["type"] in INTERESTING or e["type"].startswith(("level", "announce", "duel", "bench")):
            out(f"EVENT t{e['tick']} {e['type']}: {json.dumps(p)[:200]}")
        elif e["type"] == "settlement":
            log("settlement", p)
            for i in p.get("items", []):
                who = f"{i.get('frm')} -> {i.get('to')}"
                if p.get("persona"):
                    verb = "SOLD-TO-DEALER" if i.get("to") == p["persona"] else "BOUGHT-FROM-DEALER"
                    out(f"{verb} {p['persona']} {who}: {i.get('ref') or i.get('kind')} @ {p.get('price')}")
                else:
                    out(f"TEAM-TRADE {p.get('venue')} {who}: {i.get('ref') or i.get('kind')} @ {p.get('price')} (fee {p.get('fee')})")
    if feed:
        state["feed_id"] = max(state["feed_id"], max(e["id"] for e in feed))


def main() -> None:
    while True:
        try:
            poll()
        except BazaarError as e:
            out(f"WATCH-ERROR {e.code}: {e.message}")
        except Exception as e:  # keep the watcher alive through transient network errors
            out(f"WATCH-ERROR {type(e).__name__}: {e}")
        time.sleep(POLL)


if __name__ == "__main__":
    main()
