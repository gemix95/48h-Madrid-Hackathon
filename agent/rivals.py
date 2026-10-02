"""Rival tracker: who is gaining points, and on what; and when an epic or legendary first appears. Public data only (no team key), so it can run anywhere.

Like a sailing leader covering the boats behind: every poll it snapshots the public leaderboard, and when a team's
score moves by at least --min points it prints the change (negotiating / market split) with the public events that
team was part of since the previous snapshot: dealer deals, team trades, venue openings, level unlocks, duels.

    python3 agent/rivals.py live [--every 60] [--min 0.8]      # alerts while the game runs
    python3 agent/rivals.py replay logs/watch.jsonl logs/feed.jsonl   # what moved the board in a past session
"""
import argparse
import json
import sys
import time
import urllib.request

URL = "https://bazaar.causaprima.ai"


def get(path):
    with urllib.request.urlopen(URL + path, timeout=15) as r:
        return json.load(r)


def board(lb):
    return {t["team"]: {"score": t["score"], "neg": t.get("negotiating", 0), "mkt": t.get("market", 0),
                        "level": t.get("level"), "pages": t.get("pages_complete"), "deals": t.get("deals")}
            for t in lb.get("teams", [])}


def describe(e, team):
    """One short line for a public event that involves `team`, or None."""
    p, t = e.get("payload") or {}, e.get("type")
    if t == "settlement":
        items = p.get("items") or []
        if team not in (p.get("parties") or []):
            return None
        got = [i.get("ref") or i.get("kind") for i in items if i.get("to") == team]
        gave = [i.get("ref") or i.get("kind") for i in items if i.get("frm") == team]
        who = p.get("persona") or next((x for x in p.get("parties", []) if x != team), "?")
        if got and not gave:
            return f"bought {','.join(got)} from {who} @{p.get('price')}"
        if gave and not got:
            return f"sold {','.join(gave)} to {who} @{p.get('price')}"
        return f"swapped {','.join(gave)} for {','.join(got)} with {who}"
    if t == "venue.opened" and p.get("owner") == team:
        return f"opened venue {p.get('venue')} ({p.get('fee_bps')} bps, {p.get('rules', {}).get('mechanism')})"
    if t == "level.unlocked" and p.get("team") == team:
        return f"unlocked {p.get('persona')} (level {p.get('level')})"
    if t == "gift.given" and p.get("team") == team:
        return None  # gifts never score
    return None


def report(prev, cur, events, min_move, now_label):
    lines = []
    for team, c in cur.items():
        p = prev.get(team)
        if not p:
            continue
        d = round(c["score"] - p["score"], 2)
        if abs(d) < min_move:
            continue
        dn, dm = round(c["neg"] - p["neg"], 2), round(c["mkt"] - p["mkt"], 2)
        why = [x for x in (describe(e, team) for e in events) if x]
        extra = []
        if c.get("pages") != p.get("pages"):
            extra.append(f"pages {p.get('pages')}->{c.get('pages')}")
        if c.get("level") != p.get("level"):
            extra.append(f"level {p.get('level')}->{c.get('level')}")
        lines.append((abs(d), f"[{now_label}] {team} {d:+.2f} (neg {dn:+.2f}, market {dm:+.2f}) -> {c['score']:.1f}"
                                f"{' | ' + ', '.join(extra) if extra else ''}"
                                f"{' | ' + '; '.join(why[-6:]) if why else ' | no public trade: relative move or duels/market'}"))
    return [x for _, x in sorted(lines, reverse=True)]


def scarce(catalog):
    """{ref: minted} for epics and legendaries: the real scarcity (0 of 9 and 0 of 3 minted on Friday night)."""
    return {c["id"]: c.get("minted", 0) for st in catalog.get("sets", []) for c in st.get("cards", [])
            if c.get("rarity") in ("epic", "legendary")}


def scarce_alerts(before, after, events):
    """One line per epic/legendary whose minted count moved, with who pulled or bought it if the feed says."""
    out = []
    for ref, n in after.items():
        if n > before.get(ref, 0):
            who = []
            for e in events:
                p = e.get("payload") or {}
                if e.get("type") == "pack.opened" and ref in json.dumps(p.get("best") or ""):
                    who.append(f"{p.get('team')} pulled it from {p.get('pack')}")
                elif e.get("type") == "settlement" and any(i.get("ref") == ref for i in p.get("items") or []):
                    who += [f"{i.get('frm')} -> {i.get('to')} @{p.get('price')}" for i in p["items"] if i.get("ref") == ref]
            out.append(f"SCARCE {ref}: minted {before.get(ref, 0)} -> {n}" + (f" | {'; '.join(who)}" if who else ""))
    return out


def live(every, min_move):
    prev, last_id, minted, n = None, None, None, 0
    while True:
        try:
            cur = board(get("/api/leaderboard"))
            evs = get("/api/feed?limit=1000").get("events", [])
            new = [e for e in evs if last_id is None or e["id"] > last_id]
            if evs:
                last_id = max(e["id"] for e in evs)
            if prev:
                for line in report(prev, cur, new, min_move, time.strftime("%H:%M")):
                    print(line, flush=True)
            prev = cur
            if n % 3 == 0:  # the catalog is bigger: every third poll
                m = scarce(get("/api/catalog"))
                if minted is not None:
                    for line in scarce_alerts(minted, m, new):
                        print(f"[{time.strftime('%H:%M')}] {line}", flush=True)
                minted = m
            n += 1
        except Exception as e:  # keep watching through network errors
            print(f"RIVALS-ERROR {type(e).__name__}: {e}", flush=True)
        time.sleep(every)


def replay(watch_path, feed_path, min_move):
    feed = [json.loads(l) for l in open(feed_path) if l.strip()]
    snaps = []
    for l in open(watch_path):
        r = json.loads(l)
        if r.get("ev") == "leaderboard":
            snaps.append((r["data"].get("snapshot_tick") or r["data"].get("tick"), board(r["data"])))
    prev_tick, prev = None, None
    for tick, cur in snaps:
        if prev is not None and cur != prev:
            window = [e for e in feed if prev_tick is not None and prev_tick - 5 < e["tick"] <= tick]
            for line in report(prev, cur, window, min_move, f"t{tick}"):
                print(line)
        prev_tick, prev = tick, cur


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["live", "replay"])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--every", type=float, default=60)
    ap.add_argument("--min", type=float, default=0.8)
    a = ap.parse_args()
    if a.mode == "live":
        live(a.every, a.min)
    else:
        replay(*(a.paths or ["logs/watch.jsonl", "logs/feed.jsonl"]), a.min)
