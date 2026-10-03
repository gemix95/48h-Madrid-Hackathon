"""El Consejo: the group board where our agents leave what they learnt, and a slow tuner that improves the strategy.

    ../.venv/bin/python council.py run                      # digest + tune every cycle (no team key needed)
    ../.venv/bin/python council.py run --dry                 # post lessons, propose changes, never touch strategy.json
    ../.venv/bin/python council.py post trader "LAT commons sell at 8 P on v02"   # any agent or teammate can post
    ../.venv/bin/python council.py read [topic]              # the latest notes

Every note goes to logs/council.jsonl: {ts, tick, author, topic, lesson, evidence}. Authors are the agent's modules
(digested from logs/decisions.jsonl and the public feed), the tuner itself, or anyone posting by hand.

The tuner reads only logs, so it never writes with the team key and never needs an agent restart: it changes one
knob per cycle in strategy.json (which the agent re-reads every tick), one step at a time, inside the knob's range,
then judges the change after a trial and reverts it if the metric did not move the right way.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

import strategy

HERE = Path(__file__).parent
LOGS = HERE / "logs"
BOARD = LOGS / "council.jsonl"
STATE = LOGS / "council_state.json"
DECISIONS = LOGS / "decisions.jsonl"
FEED = LOGS / "feed_events.jsonl"
ME = "t13"

WINDOW = 120          # ticks of history each metric looks at (~1 game hour on Saturday)
CYCLE_SECONDS = 600   # one digest + at most one strategy change every 10 minutes
TRIAL_TICKS = 90      # how long a change runs before it is judged
COOLDOWN_TICKS = 150  # a knob is left alone this long after a change or revert
MIN_SAMPLE = 6        # distinct cards needed before a sell-through or fill rate is trusted

# knob: (metric, low, high, direction). Below `low` the knob moves by `direction` steps, above `high` the other way.
RULES = {
    "trade_ask_start": ("ask_sell_through", 0.2, 0.7, -1),   # spares not selling: ask less; all selling: ask more
    "trade_bid_start": ("bid_fill", 0.1, 0.6, +1),           # bids never filled: bid more; all filled: bid less
}
SAFE = {"trade_ask_start": (0.95, 1.3), "trade_bid_start": (0.45, 0.8)}  # tighter than the knob ranges


# ---------------------------------------------------------------- the board
def post(author: str, topic: str, lesson: str, evidence: dict | None = None, tick: int | None = None) -> dict:
    LOGS.mkdir(exist_ok=True)
    note = {"ts": round(time.time(), 1), "tick": tick, "author": author, "topic": topic, "lesson": lesson,
            "evidence": evidence or {}}
    with BOARD.open("a") as f:
        f.write(json.dumps(note, default=str) + "\n")
    return note


def read(topic: str | None = None, n: int = 30) -> list:
    if not BOARD.exists():
        return []
    notes = [json.loads(line) for line in BOARD.read_text().splitlines() if line.strip()]
    return [x for x in notes if topic in (None, x.get("topic"), x.get("author"))][-n:]


def _jsonl(path: Path, tail_bytes: int = 4_000_000) -> list:
    if not path.exists():
        return []
    with path.open("rb") as f:
        f.seek(0, 2)
        start = max(0, f.tell() - tail_bytes)
        f.seek(start)
        lines = f.read().decode(errors="replace").splitlines()[1 if start else 0:]  # a mid-file seek cuts a line
    out = []
    for line in lines:
        try:
            out.append(json.loads(line.replace("Infinity", "null").replace("NaN", "null")))
        except ValueError:
            pass
    return out


# ---------------------------------------------------------------- what each module learnt
def metrics() -> dict:
    dec, feed = _jsonl(DECISIONS), _jsonl(FEED)
    now = max((r.get("tick") or 0 for r in dec), default=0)
    lo = now - WINDOW
    recent = [r for r in dec if (r.get("tick") or 0) >= lo]
    sells, buys, on_ours = Counter(), Counter(), Counter()
    venue = next((r.get("venue") for r in reversed(dec) if r.get("module") == "market" and r.get("venue")), "v03")
    for e in feed:
        p = e.get("payload") or {}
        if e.get("type") != "settlement" or (p.get("tick") or e.get("tick") or 0) < lo or p.get("kind") != "trade":
            continue
        if p.get("venue") == venue:
            on_ours[tuple(sorted(p.get("parties") or []))] += 1
        for it in p.get("items") or []:
            if it.get("kind") != "card" or p.get("persona"):
                continue
            if it.get("frm") == ME:
                sells[it.get("ref")] += 1
            elif it.get("to") == ME:
                buys[it.get("ref")] += 1
    asked = {r.get("ref") for r in recent if r.get("module") == "trade" and r.get("action") == "list_ask"}
    bid = {r.get("ref") for r in recent if r.get("module") == "trade" and r.get("action") == "list_bid"}
    gains = [r.get("gain") or 0 for r in recent if r.get("module") == "trade" and r.get("action") == "accept"]
    captures = defaultdict(list)
    for r in recent:
        if r.get("module") == "learn" and r.get("action") == "reward" and r.get("price"):
            captures[r.get("cls")].append(r.get("capture") or 0)
    guard = Counter(r.get("why") for r in recent if r.get("module") == "guard" and r.get("action") == "cancelled")
    m = {
        "tick": now, "window": WINDOW, "venue": venue,
        "asked": len(asked), "sold": len(asked & set(sells)),
        "ask_sell_through": (len(asked & set(sells)) / len(asked)) if len(asked) >= MIN_SAMPLE else None,
        "bids": len(bid), "filled": len(bid & set(buys)),
        "bid_fill": (len(bid & set(buys)) / len(bid)) if len(bid) >= MIN_SAMPLE else None,
        "trade_gain": round(sum(gains), 1), "trades_taken": len(gains),
        "dealer_capture": {k: round(statistics.mean(v), 3) for k, v in captures.items()},
        "dealer_deals": sum(1 for r in recent if r.get("module") == "haggle" and r.get("action") == "deal"),
        "guard_cancels": dict(guard),
        "invites": sum(1 for r in recent if r.get("module") == "market" and r.get("action") == "invited"),
        "trades_on_our_market": sum(on_ours.values()),
        "club_rewards": sum(1 for r in recent if r.get("module") == "market" and r.get("action") == "club_reward"),
        "crashes": Counter(r.get("module") for r in recent if r.get("action") == "crash"),
    }
    return m


def digest(m: dict) -> list:
    """Turn the metrics into plain-language notes, one per module that has something to say."""
    t, notes = m["tick"], []
    span = f"last {m['window']} ticks"
    if m["asked"]:
        rate = "n/a" if m["ask_sell_through"] is None else f"{m['ask_sell_through']:.0%}"
        notes.append(("trader", "selling", f"{m['sold']} of {m['asked']} spare cards we listed sold ({rate}, {span}).",
                      {k: m[k] for k in ("asked", "sold", "ask_sell_through")}))
    if m["bids"]:
        rate = "n/a" if m["bid_fill"] is None else f"{m['bid_fill']:.0%}"
        notes.append(("trader", "buying", f"{m['filled']} of {m['bids']} cards we bid for were bought ({rate}, {span}).",
                      {k: m[k] for k in ("bids", "filled", "bid_fill")}))
    if m["trades_taken"]:
        notes.append(("trader", "value", f"Took {m['trades_taken']} team offers for +{m['trade_gain']} P of private value ({span}).",
                      {"trade_gain": m["trade_gain"], "trades": m["trades_taken"]}))
    if m["dealer_capture"]:
        best = max(m["dealer_capture"].items(), key=lambda kv: kv[1])
        worst = min(m["dealer_capture"].items(), key=lambda kv: kv[1])
        notes.append(("haggler", "dealers", f"{m['dealer_deals']} dealer deals; best capture {best[1]:.0%} on {best[0]}, "
                      f"worst {worst[1]:.0%} on {worst[0]} ({span}).", {"capture": m["dealer_capture"]}))
    notes.append(("market", "our_market", f"{m['trades_on_our_market']} trades between other teams on {m['venue']}, "
                  f"{m['invites']} invitations sent, {m['club_rewards']} club rewards ({span}).",
                  {k: m[k] for k in ("trades_on_our_market", "invites", "club_rewards")}))
    if m["guard_cancels"]:
        notes.append(("guard", "safety", "Cancelled our own offers: " + ", ".join(f"{n}× {w}" for w, n in m["guard_cancels"].items())
                      + f" ({span}).", {"cancels": m["guard_cancels"]}))
    if m["crashes"]:
        notes.append(("agent", "health", "Crashes logged by: " + ", ".join(f"{k} ({n})" for k, n in m["crashes"].items()) + ".",
                      {"crashes": dict(m["crashes"])}))
    return [post(a, top, txt, ev, tick=t) for a, top, txt, ev in notes]


# ---------------------------------------------------------------- the tuner
def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"trial": None, "touched": {}}


def _save_state(st: dict):
    STATE.write_text(json.dumps(st, indent=1))


def _set_knob(knob: str, value):
    raw = json.loads(strategy.PATH.read_text()) if strategy.PATH.exists() else {}
    raw[knob] = value
    return strategy.save(raw)[knob]


def tune(m: dict, apply: bool) -> None:
    st, S, t = _load_state(), strategy.load(), m["tick"]
    trial = st.get("trial")
    if trial and t - trial["tick"] >= TRIAL_TICKS:  # judge the running change
        metric, low, high, _ = RULES[trial["knob"]]
        before, after = trial["before"], m.get(metric)
        target = (low + high) / 2
        better = after is not None and abs(after - target) < abs(before - target)
        if better or after is None:
            post("tuner", "strategy", f"Kept {trial['knob']} = {trial['new']}: {metric} went {before:.0%} → "
                 f"{'n/a' if after is None else f'{after:.0%}'}.", {"trial": trial, "after": after}, tick=t)
        else:
            if apply and S.get(trial["knob"]) == trial["new"]:  # only revert our own change, not a human edit
                _set_knob(trial["knob"], trial["old"])
            post("tuner", "strategy", f"Reverted {trial['knob']} to {trial['old']}: {metric} went {before:.0%} → {after:.0%}, "
                 "no closer to the target.", {"trial": trial, "after": after}, tick=t)
        st["touched"][trial["knob"]] = t
        st["trial"] = None
        _save_state(st)
        return
    if trial:
        return  # one experiment at a time
    for knob, (metric, low, high, direction) in RULES.items():
        val = m.get(metric)
        if val is None or t - st["touched"].get(knob, -10**6) < COOLDOWN_TICKS:
            continue
        if low <= val <= high:
            continue
        step = strategy.KNOBS[knob][3] * (direction if val < low else -direction)
        lo_safe, hi_safe = SAFE[knob]
        new = round(min(hi_safe, max(lo_safe, S[knob] + step)), 4)
        if new == S[knob]:
            continue
        why = f"{metric} {val:.0%} is {'below' if val < low else 'above'} the {low:.0%}-{high:.0%} band"
        if apply:
            new = _set_knob(knob, new)
            st["trial"] = {"knob": knob, "old": S[knob], "new": new, "tick": t, "before": val}
            _save_state(st)
        post("tuner", "strategy", f"{'Changed' if apply else 'Would change'} {knob} {S[knob]} → {new}: {why}. "
             f"Judged after {TRIAL_TICKS} ticks.", {"knob": knob, "old": S[knob], "new": new, "metric": metric, "value": val}, tick=t)
        return  # one change per cycle


def run(apply: bool, once: bool = False):
    post("tuner", "health", f"Council started ({'tuning live' if apply else 'dry run: proposals only'}).")
    while True:
        try:
            m = metrics()
            digest(m)
            tune(m, apply)
        except Exception as e:  # the board must never take anything down
            post("tuner", "health", f"Council cycle failed: {e!r}"[:300])
        if once:
            return
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry", action="store_true")
    r.add_argument("--once", action="store_true")
    p = sub.add_parser("post")
    p.add_argument("author")
    p.add_argument("lesson")
    p.add_argument("--topic", default="note")
    q = sub.add_parser("read")
    q.add_argument("topic", nargs="?")
    a = ap.parse_args()
    if a.cmd == "run":
        run(apply=not a.dry, once=a.once)
    elif a.cmd == "post":
        print(json.dumps(post(a.author, a.topic, a.lesson)))
    else:
        for x in read(a.topic):
            print(f"[{x.get('tick')}] {x['author']}/{x['topic']}: {x['lesson']}")
