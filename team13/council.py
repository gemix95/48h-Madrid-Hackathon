"""El Consejo: the group board where our agents leave what they learnt, and a slow tuner that improves the strategy.

    ../.venv/bin/python council.py run                      # digest + tune every cycle (no team key needed)
    ../.venv/bin/python council.py run --dry                 # post lessons, propose changes, never touch strategy.json
    ../.venv/bin/python council.py post "Rival t04 opens Abuela at 18 P"   # a human (or anything not trading): Admin
    ../.venv/bin/python council.py read [topic]              # the latest notes
    ../.venv/bin/python council.py sync                      # pull + push the shared board now

One board for every laptop: the `council` branch, checked out at ../.council (created on first use). Every process
pulls it every 15 s and pushes each note at once; each process writes its own file (notes/<who>@<host>.jsonl), so
two laptops never edit the same lines and a pull never conflicts. Notes wait in ../.council-spool until pushed.

A note: {ts, tick, author, topic, lesson, evidence, agent, role, host}. author is the module (haggler, trader, ...),
"tuner", or "Admin" for a human or anything not trading in the market. agent is the market agent's unique id: a
famous businessperson's surname picked at start (one nobody used in the last day), fixed until that agent restarts;
role is its AGENT_ROLE. Agents announce the deals they open, close and walk away from, so the others don't step on them.

The tuner reads only logs, so it never writes with the team key and never needs an agent restart: it changes one
knob per cycle in strategy.json (which the agent re-reads every tick), one step at a time, inside the knob's range,
then judges the change after a trial and reverts it if the metric did not move the right way.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import random
import re
import socket
import statistics
import subprocess
import threading
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path

import strategy

HERE = Path(__file__).parent
LOGS = HERE / "logs"
BOARD = LOGS / "council.jsonl"
STATE = LOGS / "council_state.json"
DECISIONS = LOGS / "decisions.jsonl"
FEED = LOGS / "feed_events.jsonl"
ME = "t13"

REPO = HERE.parent
BOARD_DIR = REPO / ".council"        # git worktree of the shared `council` branch
NOTES = BOARD_DIR / "notes"
SPOOL = REPO / ".council-spool"      # this laptop's notes not pushed yet (outside the worktree: git never touches it)
LOCK = REPO / ".council.lock"
BRANCH = "council"
REFSPEC = f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}"  # explicit: works in single-branch or shallow clones
PULL_SECONDS = 15
HOST = re.sub(r"[^A-Za-z0-9-]", "", socket.gethostname().split(".")[0]) or "host"
NAMES = ["Rockefeller", "Carnegie", "Vanderbilt", "Morgan", "Ford", "Fugger", "Medici", "Rothschild", "Astor", "Mellon",
         "Hearst", "Edison", "Disney", "Chanel", "Lauder", "Walton", "Kroc", "Hilton", "Hughes", "Jobs", "Onassis",
         "Ferrari", "Kamprad", "Morita", "Honda", "Matsushita", "Strauss", "Wedgwood", "Agnelli", "Mitsui", "Tata",
         "Krupp", "Siemens", "Nobel", "Guggenheim", "Pulitzer", "Getty", "Hershey", "Ortega", "Gucci"]
WHO = {"writer": "admin", "agent": None, "role": None}  # who this process posts as (identify())

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
def post(author: str, topic: str, lesson: str, evidence: dict | None = None, tick: int | None = None,
         agent: str | None = None) -> dict:
    note = {"ts": round(time.time(), 1), "tick": tick, "author": author, "topic": topic, "lesson": lesson,
            "evidence": evidence or {}, "agent": agent or WHO["agent"], "role": WHO["role"], "host": HOST}
    SPOOL.mkdir(exist_ok=True)
    with (SPOOL / f"{WHO['writer']}@{HOST}.jsonl").open("a") as f:
        f.write(json.dumps(note, default=str) + "\n")
    _kick.set()  # push it now (the sync thread, or the next sync() call)
    return note


def peer_claims(exclude_agent: str | None = None, exclude_host: str | None = None, max_age_s: float = 2400) -> set:
    """Plan keys (`dealer:buy:rare`, …) another agent is still negotiating (from haggler deal notes)."""
    cutoff = time.time() - max_age_s
    open_keys: dict = {}
    for note in read(n=400):
        if (note.get("ts") or 0) < cutoff:
            continue
        if exclude_agent and note.get("agent") == exclude_agent:
            continue
        if exclude_host and not exclude_agent and note.get("host") == exclude_host:
            continue
        if note.get("author") != "haggler" or note.get("topic") != "deal":
            continue
        key = (note.get("evidence") or {}).get("key")
        if not key:
            continue
        lesson = note.get("lesson") or ""
        if lesson.startswith("Negotiating with"):
            open_keys[key] = note
        elif lesson.startswith("Deal with") or lesson.startswith("No deal with"):
            open_keys.pop(key, None)
    return set(open_keys)


def peer_lessons(exclude_agent: str | None = None, exclude_host: str | None = None, n: int = 10) -> list:
    """Short strategy lines from other laptops' agents and the tuner (for Claude + logs)."""
    want = {"learn", "dealers", "selling", "buying", "value", "duels", "our_market", "safety", "volume", "market"}
    out, seen = [], set()
    for note in reversed(read(n=250)):
        if exclude_agent and note.get("agent") == exclude_agent:
            continue
        if exclude_host and not exclude_agent and note.get("host") == exclude_host:
            continue
        top = note.get("topic") or ""
        auth = note.get("author") or ""
        if top not in want and auth not in ("haggler", "trader", "tuner", "market", "duels", "flipper"):
            continue
        if top == "deal" and auth == "haggler" and not (note.get("lesson") or "").startswith("Negotiating"):
            continue  # closed haggles are coordination, not lessons
        txt = (note.get("lesson") or "").strip()
        if not txt or txt in seen:
            continue
        who = note.get("agent") or note.get("host") or "peer"
        seen.add(txt)
        out.append(f"{who}: {txt[:220]}")
        if len(out) >= n:
            break
    return out


def read(topic: str | None = None, n: int = 30) -> list:
    """The latest notes from every laptop (the board), this laptop's unpushed ones, and the old main-branch file."""
    files = [BOARD] + sorted(NOTES.glob("*.jsonl")) + sorted(SPOOL.glob("*.jsonl")) + sorted(SPOOL.glob("*.sending"))
    seen, notes = set(), []
    for path in files:
        for x in _jsonl(path):
            if not isinstance(x, dict):
                continue
            key = (x.get("ts"), x.get("author"), x.get("topic"), x.get("lesson"))
            if key not in seen:
                seen.add(key)
                notes.append(x)
    notes.sort(key=lambda x: x.get("ts") or 0)
    return [x for x in notes if topic in (None, x.get("topic"), x.get("author"), x.get("agent"))][-n:]


# ---------------------------------------------------------------- sharing it between laptops (git)
_kick = threading.Event()


def _git(*args, cwd=BOARD_DIR, timeout=30):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # a background push must fail, never wait for a password
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))


@contextmanager
def _locked():
    """One git user of the board at a time on this laptop (agent, tuner, dashboard and hand posts share it)."""
    with LOCK.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def ensure() -> bool:
    """Check out the `council` branch at ../.council the first time (any laptop, any git version)."""
    if (BOARD_DIR / ".git").exists():
        return True
    with _locked():
        if (BOARD_DIR / ".git").exists():
            return True
        _git("fetch", "-q", "origin", REFSPEC, cwd=REPO)
        r = _git("worktree", "add", "-q", "-B", BRANCH, str(BOARD_DIR), f"origin/{BRANCH}", cwd=REPO)
        return r.returncode == 0


def sync(pull_only: bool = False) -> bool:
    """Move this laptop's spooled notes into the board, commit, pull (rebase), push. False when git failed."""
    if not ensure():
        return False
    with _locked():
        NOTES.mkdir(exist_ok=True)
        for path in sorted(SPOOL.glob("*.jsonl")) + sorted(SPOOL.glob("*.sending")):
            sending = path if path.suffix == ".sending" else path.with_suffix(".sending")
            if path != sending:
                os.replace(path, sending)  # writers start a fresh spool file meanwhile
            with (NOTES / sending.with_suffix(".jsonl").name).open("a") as out:
                out.write(sending.read_text())
            sending.unlink()
        _git("add", "-A", "notes")
        if _git("diff", "--cached", "--quiet").returncode:
            _git("-c", "user.name=El Consejo", "-c", "user.email=council@team13.local", "commit", "-q", "-m",
                 f"{WHO['agent'] or WHO['writer']}@{HOST}: notes")
        ok = True
        for _ in range(3):
            if _git("fetch", "-q", "origin", REFSPEC).returncode:
                ok = False  # offline: the notes stay committed here and go out with the next sync
                break
            if _git("rebase", "-q", f"origin/{BRANCH}").returncode:
                _git("rebase", "--abort")  # cannot happen with one file per writer; never leave the board half-merged
                ok = False
                break
            ahead = _git("rev-list", "--count", f"origin/{BRANCH}..HEAD").stdout.strip()
            if pull_only or ahead in ("", "0"):
                break
            ok = _git("push", "-q", "origin", f"HEAD:{BRANCH}").returncode == 0
            if ok:
                break
        return ok


def start_sync(every: float = PULL_SECONDS) -> threading.Thread:
    """Background: pull every `every` seconds, push as soon as a note is posted."""
    def loop():
        while True:
            _kick.clear()
            try:
                sync()
            except Exception:  # the board must never take anything down
                pass
            _kick.wait(every)
    t = threading.Thread(target=loop, name="council-sync", daemon=True)
    t.start()
    return t


def identify(role: str) -> str:
    """A market agent's unique id: a famous businessperson nobody on the board used in the last day. Fixed for the
    life of the process (a restart picks a new one)."""
    try:
        sync(pull_only=True)
    except Exception:
        pass
    used = {x.get("agent") for x in read(n=10 ** 6) if time.time() - (x.get("ts") or 0) < 86400}
    free = [n for n in NAMES if n not in used]
    name = random.choice(free) if free else f"{random.choice(NAMES)}-{random.randint(10, 99)}"
    WHO.update(writer=name, agent=name, role=role)
    return name


def local_agent() -> str | None:
    """The id of the agent on this laptop (from its start line), for the notes the tuner writes about it."""
    for r in reversed(_jsonl(DECISIONS, 400_000)):
        if r.get("module") == "agent" and r.get("action") == "start":
            return r.get("agent_id")
    return None


def _item(key: str) -> str:
    return (key or "").split(":")[-1].replace("_", " ")


def announce(rec: dict, dealer_names: dict | None = None) -> None:
    """Post the deals a market agent opens, closes or walks away from (called for every decision it logs)."""
    m, a, t = rec.get("module"), rec.get("action"), rec.get("tick")
    who = lambda d: (dealer_names or {}).get(d, d)  # noqa: E731
    if m == "haggle" and a == "opened":
        p = rec.get("plan") or {}
        post("haggler", "deal", f"Negotiating with {who(rec.get('dealer'))}: {p.get('side', 'buy')} {_item(p.get('key'))}, "
             f"opening {p.get('lo')} P, cap {p.get('hi')} P.", {"dealer": rec.get("dealer"), "key": p.get("key"),
                                                               "lo": p.get("lo"), "hi": p.get("hi")}, tick=t)
    elif m == "haggle" and a == "deal":
        d = (rec.get("key") or "").split(":")[0]
        post("haggler", "deal", f"Deal with {who(d)}: {_item(rec.get('key'))} for {rec.get('price')} P after "
             f"{rec.get('rounds')} offers.", {"key": rec.get("key"), "price": rec.get("price"), "thread": rec.get("thread")}, tick=t)
    elif m == "haggle" and a == "ended":
        d = (rec.get("key") or "").split(":")[0]
        post("haggler", "deal", f"No deal with {who(d)} on {_item(rec.get('key'))} ({rec.get('reason') or rec.get('status')}).",
             {"key": rec.get("key"), "thread": rec.get("thread")}, tick=t)
    elif m == "trade" and a == "accept":
        post("trader", "deal", f"Took offer {rec.get('offer')} on {rec.get('venue')}: +{rec.get('gain')} P of value.",
             {"offer": rec.get("offer"), "venue": rec.get("venue"), "gain": rec.get("gain")}, tick=t)
    elif m == "duel" and a == "accept":
        post("duels", "deal", f"Duel {rec.get('duel')} settled at {rec.get('price')} P, {rec.get('days')} days.",
             {"duel": rec.get("duel"), "price": rec.get("price")}, tick=t)
    elif m == "duel" and a == "result":
        post("duels", "score", f"Duel {rec.get('duel')} {rec.get('status')}: score {rec.get('score')} "
             f"({rec.get('role')} vs {rec.get('rival')}, {rec.get('rounds')} rounds).",
             {"duel": rec.get("duel"), "score": rec.get("score"), "status": rec.get("status")}, tick=t)
    elif m == "duel_tuner" and a in ("applied", "proposal", "current_is_best"):
        post("tuner", "duels", rec.get("lesson") or f"Duel tune {a}: {rec.get('applied') or rec.get('changes')}",
             {"applied": rec.get("applied"), "mean_score": rec.get("mean_score"), "deal_rate": rec.get("deal_rate")}, tick=t)
    elif m == "workshop" and a == "crafted":
        post("workshop", "deal",
             f"Workshop: burned {', '.join(rec.get('refs') or [])} ({rec.get('rarity')}) for {rec.get('pulled') or 'a'} "
             f"{rec.get('pulled_rarity') or rec.get('next')} (gave up {rec.get('loss')} P, expected {rec.get('ev')} P).",
             {"refs": rec.get("refs"), "pulled": rec.get("pulled"), "surplus": rec.get("surplus")}, tick=t)
    elif m == "flip" and a in ("buy", "sell"):
        txt = (f"Flip: bought {rec.get('ref')} at {rec.get('price')} P to sell to {rec.get('target_team')} at {rec.get('target_bid')} P."
               if a == "buy" else f"Flip: sold {rec.get('ref')} to {rec.get('buyer')} for {rec.get('price')} P.")
        post("flipper", "deal", txt, {"ref": rec.get("ref"), "price": rec.get("price")}, tick=t)


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
    agent = local_agent()
    return [post(a, top, txt, ev, tick=t, agent=agent) for a, top, txt, ev in notes]


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
    WHO.update(writer="tuner", role="tuner")
    if not once:
        start_sync()
    post("tuner", "health", f"Council started ({'tuning live' if apply else 'dry run: proposals only'}).")
    while True:
        try:
            m = metrics()
            digest(m)
            tune(m, apply)
        except Exception as e:  # the board must never take anything down
            post("tuner", "health", f"Council cycle failed: {e!r}"[:300])
        if once:
            sync()
            return
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry", action="store_true")
    r.add_argument("--once", action="store_true")
    p = sub.add_parser("post", help='post "lesson" as Admin (a human, or anything not trading in the market)')
    p.add_argument("lesson")
    p.add_argument("--topic", default="note")
    q = sub.add_parser("read")
    q.add_argument("topic", nargs="?")
    sub.add_parser("sync")
    a = ap.parse_args()
    if a.cmd == "run":
        run(apply=not a.dry, once=a.once)
    elif a.cmd == "post":
        print(json.dumps(post("Admin", a.topic, a.lesson)))
        print("pushed" if sync() else "saved here; pushes on the next sync")
    elif a.cmd == "sync":
        print("synced" if sync() else "sync failed (offline, or no council branch on origin)")
    else:
        sync(pull_only=True)
        for x in read(a.topic):
            who = " · ".join(v for v in (x.get("agent"), x.get("role")) if v)
            print(f"[{x.get('tick')}] {x['author']}/{x['topic']}{f' ({who})' if who else ''}: {x['lesson']}")
