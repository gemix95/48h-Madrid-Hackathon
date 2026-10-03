"""Arbitrage desk for the dashboard (the Arbitrage tab and the standalone page at /arb, both behind the dashboard
password): what the arbitrage module is doing, what it could do right now, how its pairs ended, and what stops them.

The candidate list calls team13/arbitrage.py's own candidates() twice: once at the agent's real `arb_min_score`
(that set, with those caps, is exactly what the agent would take: the green rows) and once at a floor low enough to
keep every pair, so the near misses are visible with the reason they miss. Page and agent can never disagree.

Attempts are read from the agents' decision log. A pair that dies in the tick it starts is almost always a module of
ours cancelling the dealer bid, so guard cancellations from the same tick are joined onto the attempt and, when the
last attempts all died the same way, reported as a blocker.
"""
from __future__ import annotations

import html
import json
import math
import os
import time
import urllib.request
from pathlib import Path

import arbitrage  # team13/arbitrage.py
import strategy  # team13/strategy.py
from values import Values  # team13/values.py

HERE = Path(__file__).parent
TEAM13 = HERE.parent / "team13"
DECISIONS = TEAM13 / "logs" / "decisions.jsonl"
FEED = TEAM13 / "logs" / "feed_events.jsonl"
AGENTS_DIR = Path(os.environ.get("AGENTS_DIR", "/home/bazaar/agents"))  # server only: one <name>.env per teammate
URL = os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai")
SHOW_FLOOR = -10_000       # candidates() with this min score keeps every pair, so the page can show the near misses
SHOW_MAX = 25              # rows in the candidate table
BLOCKER_MIN = 2            # attempts that have to die the same way before we call it a blocker
# an attempt that ends in one of these never bought the card
DEAD = ("dealer_walked", "gave_up", "bid_gone_before_buy", "card_not_arrived", "sell_refused", "buy_refused", "open_refused")


def _get(path):
    with urllib.request.urlopen(URL + path, timeout=15) as r:
        return json.load(r)


def _agent_env() -> tuple:
    """(agent that runs arbitrage, its AGENT_KNOBS, where they were read from). A laptop has no agent envs."""
    want = os.environ.get("ARB_AGENT")
    for p in sorted(AGENTS_DIR.glob("*.env")) if AGENTS_DIR.is_dir() else []:
        try:
            env = dict(line.split("=", 1) for line in p.read_text().splitlines() if "=" in line and not line.startswith("#"))
        except OSError:
            continue
        env = {k.strip(): v.strip() for k, v in env.items()}
        name = env.get("AGENT_NAME") or p.stem
        roles = {r.strip() for r in env.get("AGENT_ROLE", "all").split(",")}
        if want and name != want:
            continue
        if not want and not roles & {"arbitrage", "all"}:
            continue
        knobs = {}
        for part in env.get("AGENT_KNOBS", "").split(","):
            k, _, v = part.partition("=")
            if k.strip():
                v = v.strip()
                knobs[k.strip()] = float(v) if v.replace(".", "", 1).isdigit() else v
        return name, knobs, str(p)
    return want or "anton", {}, ""


def _knobs() -> tuple:
    """strategy.json plus the arbitrage agent's own AGENT_KNOBS, as that agent sees them."""
    agent, knobs, src = _agent_env()
    return agent, {**strategy.load(), **knobs}, src


def _log_tail(n_bytes=3_000_000) -> tuple:
    """(arbitrage events, guard cancellations) from the end of the agents' decision log."""
    if not DECISIONS.exists():
        return [], []
    with DECISIONS.open("rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - n_bytes))
        lines = f.read().decode(errors="replace").splitlines()
    arb, guard = [], []
    for line in lines:
        if '"module": "arb"' in line:
            target = arb
        elif '"module": "guard"' in line and '"action": "cancelled"' in line:
            target = guard
        else:
            continue
        try:
            target.append(json.loads(line))
        except ValueError:
            pass
    return arb, guard


def history(events, guards) -> list:
    """One row per attempt, newest first: how it started, how it ended, and what cancelled it on the way."""
    rows, open_ = [], {}
    # a guard cancellation of a bid FOR a card, by (tick, ref): the guard runs last in the tick that posted the bid
    blocks = {}
    for g in guards:
        want = g.get("want")
        for ref in (want if isinstance(want, list) else []):
            blocks[(g.get("tick"), ref)] = g
    for e in events:
        a, key = e.get("action"), e.get("ref")
        if a == "start":
            row = {"tick": e.get("tick"), "ts": e.get("ts"), "ref": key, "dealer": e.get("dealer"),
                   "bid_price": e.get("bid_price"), "value": e.get("value"), "cap": e.get("cap"),
                   "expected": e.get("score"), "outcome": "in progress", "blocked_by": None}
            open_[key] = row
            rows.append(row)
        elif a in ("open_refused", "scan_failed"):
            rows.append({"tick": e.get("tick"), "ts": e.get("ts"), "ref": key, "dealer": e.get("dealer"),
                         "outcome": a.replace("_", " "), "note": e.get("error"), "blocked_by": None})
        elif key in open_:
            r = open_[key]
            if a == "bought":
                r.update(paid=e.get("price"), outcome="bought, selling")
            elif a == "sold":
                r.update(outcome="sold", paid=e.get("paid") or r.get("paid"), score=e.get("score"))
                open_.pop(key)
            elif a in DEAD:
                r.update(outcome=a.replace("_", " "), last_price=e.get("last_price"), note=e.get("error"))
                open_.pop(key)
    for r in rows:  # the guard cancels in the same tick the bid went out, which closes the dealer thread
        g = blocks.get((r.get("tick"), r.get("ref")))
        if g and r.get("outcome") in ("dealer walked", "gave up", "open refused"):
            r["blocked_by"] = f"guard: {g.get('why')}"
    return rows[::-1]


def blocker(rows) -> dict | None:
    """When the last attempts all died the same way, say so once instead of making the reader read the table."""
    dead = [r for r in rows if r.get("outcome") not in ("sold", "bought, selling", "in progress")]
    if len(dead) < BLOCKER_MIN:
        return None
    why = dead[0].get("blocked_by") or dead[0].get("outcome")
    same = [r for r in dead if (r.get("blocked_by") or r.get("outcome")) == why]
    if len(same) < BLOCKER_MIN or len(same) < len(dead[:len(same)]):
        return None
    return {"why": why, "n": len(same), "since_tick": same[-1].get("tick"), "last_tick": same[0].get("tick"),
            "sold": any(r.get("outcome") == "sold" for r in rows)}


def _why_not(p) -> str:
    """Why the agent would not take this pair, in the module's own terms (p["bar"] is the least score it must clear)."""
    bar = p.get("bar", 0)
    pmax = arbitrage.max_price(p["value"], p["net_in"], bar)
    if pmax is None:
        return f"the bid pays {round(p['net_in'] - p['value'], 1)} P over our value, under the {bar} P bar"
    if pmax < math.floor(p["list"] * arbitrage.START_SHARE):
        return f"our max is {pmax} P, the dealer opens at {p['list']} P"
    return f"our max is {pmax} P"


def compute(cache: dict) -> dict:
    agent, S, knobs_src = _knobs()
    me, cat = cache.get("me") or {}, cache.get("catalog") or {}
    min_score = S.get("arb_min_score", 8)
    data = {"at": int(time.time()), "agent": agent, "knobs_from": knobs_src or "strategy.json (no agent env here)",
            "enabled": bool(S.get("enable_arbitrage", 0)), "min_score": min_score, "scan_every": arbitrage.SCAN_EVERY,
            "min_bid_life": arbitrage.MIN_BID_LIFE, "max_per_day": S.get("arb_max_per_day", 6),
            "ladder_slack": S.get("arb_ladder_slack", 0), "ladder_best": arbitrage.LADDER_BEST,
            "candidates": [], "error": None}
    state_file = TEAM13 / f"state-{agent}.json"
    try:
        st = json.loads(state_file.read_text()).get("arb") or {}
    except (OSError, ValueError):
        st = {}
        data["error"] = f"no {state_file.name} here (the agents run on the server)"
    data["active"], data["done"], data["scan_tick"] = st.get("active"), st.get("done") or {}, st.get("scan")
    arb_events, guard_events = _log_tail()
    data["history"] = history(arb_events, guard_events)
    data["blocker"] = blocker(data["history"])
    try:
        venues = [v for v in ((cache.get("venues") or {}).get("venues") or _get("/api/venues")["venues"])
                  if v.get("status") == "open"]
        boards = {v["venue"]: _get(f"/api/venues/{v['venue']}/offers").get("offers", []) for v in venues}
        makers = {}
        if FEED.exists():
            with FEED.open() as f:
                for line in f:
                    if '"offer.listed"' in line:
                        o = (json.loads(line).get("payload") or {}).get("offer") or {}
                        makers[o.get("id")] = o.get("maker")

        class Ctx:  # the slice of the agent's context arbitrage.candidates() reads
            pass
        ctx = Ctx()
        ctx.values, ctx.me = Values(cat, me), me
        ctx.clock = cache.get("clock") or {"tick": 0}
        ctx.threads = (cache.get("threads") or {}).get("threads") or []
        ctx.deal_threads = (cache.get("threads_deal") or {}).get("threads")  # deals per dealer: the ladder's best three
        dealers = (cache.get("dealers") or {}).get("personas") or _get("/api/dealers").get("personas", [])
        # exactly what the agent would take, with the caps it would use; then every pair, to show the near misses
        slack = data["ladder_slack"]
        take = {(p["ref"], p["dealer"], p["bid"]): (sc, p)
                for sc, p in arbitrage.candidates(ctx, venues, boards, makers, dealers, min_score, slack)}
        rows = arbitrage.candidates(ctx, venues, boards, makers, dealers, SHOW_FLOOR, slack)
        names = {v["venue"]: v.get("name", v["venue"]) for v in venues}
        tick = ctx.clock.get("tick", 0)
        expires = {o.get("id"): o.get("expires_tick") for offers in boards.values() for o in offers}
        seen = set()
        for sc, p in rows[:SHOW_MAX]:
            key = (p["ref"], p["dealer"], p["bid"])
            if key in seen:
                continue
            seen.add(key)
            hit = take.get(key)
            sc, p = hit if hit else (sc, p)  # a green row shows the agent's own cap and score, not the list price
            p["bar"] = min_score - (slack if p.get("ladder_slot") else 0)  # SHOW_FLOOR left a nonsense bar on the rest
            data["candidates"].append({**p, "score": sc, "venue_name": names.get(p["bid_venue"], p["bid_venue"]),
                                       "ticks_left": (expires.get(p["bid"]) or tick) - tick, "go": bool(hit),
                                       "why_not": None if hit else _why_not(p)})
        data["candidates"].sort(key=lambda c: (not c["go"], -c["score"]))
        data["tick"] = tick
    except Exception as e:
        data["error"] = repr(e)[:200]
    return data


def render(d: dict) -> str:
    esc = lambda x: html.escape(str(x if x is not None else "–"))
    today = sum(d["done"].values()) if d.get("done") else 0
    sold = [r for r in d["history"] if r["outcome"] == "sold"]
    gross = sum((r.get("bid_price") or 0) - (r.get("paid") or 0) for r in sold)
    pts = sum(r.get("score") or 0 for r in sold)
    a = d.get("active")
    active = (f'<b>{esc(a["ref"])}</b> from {esc(a["dealer"])}: phase {esc(a.get("phase"))}, our offer {esc(a.get("price"))} P, '
              f'cap {esc(a.get("cap"))} P, bid {esc(a.get("bid_price"))} P, round {esc(a.get("rounds"))}') if a else \
             f'<span class="dim">idle: scanning every {esc(d.get("scan_every"))} ticks, last scan at tick {esc(d.get("scan_tick"))}</span>'
    b = d.get("blocker")
    warn = (f'<div class="warn"><b>Every pair dies the same way: {esc(b["why"])}</b> — {esc(b["n"])} attempts in a row '
            f'(ticks {esc(b["since_tick"])}–{esc(b["last_tick"])}){"" if b["sold"] else ", none ever sold"}.</div>') if b else ""
    cand = "".join(
        f'<tr class="{"go" if c["go"] else ""}"><td><b>{esc(c["ref"])}</b></td><td>{esc(c["dealer"])} <span class="dim">list {esc(c["list"])}</span></td>'
        f'<td>{esc(c["bid_price"])} P <span class="dim">{esc(c["venue_name"])}</span></td><td>{esc(c["value"])}</td>'
        f'<td>{esc(c["cap"]) if c["go"] else "–"}</td><td><b>{esc(c["score"]) if c["go"] else "–"}</b></td>'
        f'<td>{esc(c["ticks_left"])}</td>'
        f'<td class="dim">{"free slot, L" + esc(c.get("level")) if c.get("ladder_slot") else esc(c.get("dealer_deals")) + " deals"}</td>'
        f'<td class="dim">{esc(c["why_not"]) if not c["go"] else "the agent would take it"}</td></tr>'
        for c in d["candidates"]) \
        or '<tr><td colspan="9" class="dim">no team bid that a dealer can feed right now</td></tr>'
    hist = "".join(
        f'<tr><td>{esc(r["tick"])}</td><td><b>{esc(r["ref"])}</b></td><td>{esc(r.get("dealer"))}</td><td>{esc(r.get("paid"))}</td>'
        f'<td>{esc(r.get("bid_price"))}</td><td>{esc(r.get("expected"))}</td><td>{esc(r.get("score"))}</td><td>{esc(r["outcome"])}</td>'
        f'<td class="dim">{esc(r.get("blocked_by") or r.get("note") or "")}</td></tr>'
        for r in d["history"][:40]) or '<tr><td colspan="9" class="dim">no attempts yet</td></tr>'
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30"><title>Arbitrage Desk</title><style>
:root{{--bg:#f6f4ef;--card:#fff;--ink:#1d1b16;--dim:#6b665c;--line:#e4dfd3;--gold:#b4832a;--green:#1f7a4d;--red:#b03a2e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#14161b;--card:#1c1f26;--ink:#ece8de;--dim:#9a958a;--line:#2c303a;--gold:#e0b45a;--green:#4cc38a;--red:#f07a6a}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:18px 16px 40px}}h1{{margin:0;font-size:24px}}h2{{font-size:16px;margin:22px 0 8px}}.dim{{color:var(--dim)}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:14px 0}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}}.kpi b{{display:block;font-size:22px}}
.box{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}}
.warn{{border:1px solid var(--red);border-radius:10px;padding:10px 12px;margin:14px 0;background:color-mix(in srgb,var(--red) 10%,transparent)}}
.wrap{{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}}
table{{border-collapse:collapse;width:100%;min-width:640px}}td,th{{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left}}
th{{font-size:11px;text-transform:uppercase;color:var(--dim)}}tr.go td{{background:color-mix(in srgb,var(--green) 12%,transparent)}}
</style></head><body><main>
<h1>Arbitrage Desk</h1>
<div class="dim">Dealer → team bid. Score per pair = min(0, value − paid) + min(50, bid − fee − value). Agent: {esc(d.get("agent"))} ·
module {"<b>on</b>" if d["enabled"] else "<b>off</b>"} · min score {esc(d["min_score"])} (−{esc(d.get("ladder_slack"))} at a dealer
with an empty best-{esc(d.get("ladder_best"))} slot) · max {esc(d["max_per_day"])} pairs/day · tick {esc(d.get("tick"))} ·
knobs from {esc(d.get("knobs_from"))} · {time.strftime("%H:%M:%S", time.localtime(d["at"]))}{(" · error: " + esc(d["error"])) if d.get("error") else ""}</div>
{warn}
<div class="kpis"><div class="kpi"><span class="dim">pairs today</span><b>{today}</b></div>
<div class="kpi"><span class="dim">sold (log)</span><b>{len(sold)}</b></div>
<div class="kpi"><span class="dim">cash, before fees</span><b>{gross:+} P</b></div>
<div class="kpi"><span class="dim">score (formula)</span><b>{pts:+.1f}</b></div></div>
<h2>Now</h2><div class="box">{active}</div>
<h2>Candidates right now <span class="dim">(green: the agent would take it)</span></h2>
<div class="wrap"><table><thead><tr><th>Card</th><th>Dealer</th><th>Team bid</th><th>Value to us</th><th>Our max</th><th>Score at max</th><th>Bid ticks left</th><th>Ladder</th><th>Verdict</th></tr></thead><tbody>{cand}</tbody></table></div>
<h2>History</h2>
<div class="wrap"><table><thead><tr><th>Tick</th><th>Card</th><th>Dealer</th><th>Paid</th><th>Bid</th><th>Expected</th><th>Score</th><th>Outcome</th><th>Why</th></tr></thead><tbody>{hist}</tbody></table></div>
<p class="dim">The manual LAT-10 pair (Pícaros 60 → t18 72, ticks 1331-1332) ran before the module and is not in this log.</p>
</main></body></html>"""
