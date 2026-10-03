"""Duel strategy learner: improve duel knobs from finished + live duels, every few minutes.

Reads our done duels from the API (and the decisions log as a fallback), measures what actually
scores, re-simulates a short grid against the rival styles we are meeting, and writes better
knobs into strategy.json. The agent reloads strategy every tick — no agent restart needed.

    ../.venv/bin/python duel_tuner.py run            # loop: learn + apply
    ../.venv/bin/python duel_tuner.py run --dry      # learn + propose, do not write strategy.json
    ../.venv/bin/python duel_tuner.py once           # one pass
    ../.venv/bin/python duel_tuner.py status         # print the last learning snapshot
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import statistics
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import strategy

HERE = Path(__file__).parent
LOGS = HERE / "logs"
STATE = LOGS / "duel_learn.json"
BOARD = LOGS / "duels_board.json"   # per-duel points for the war room
DECISIONS = LOGS / "decisions.jsonl"
ENV = HERE.parent / "bazaar.env"

CYCLE_SECONDS = 90          # re-learn often during a live session
COOLDOWN_TICKS = 40         # leave a knob alone this long after a change
MIN_DONE = 8                # need this many finished duels before auto-applying
MIN_GAIN = 0.008            # sim weighted-mean gain required to switch
KNOBS = ("duel_rounds", "duel_anchor", "duel_accept", "duel_seller_cap")

# search grid (kept small: a cycle must finish well under one tick)
GRID = {
    "duel_rounds": [4, 5, 6, 7, 8, 10],
    "duel_anchor": [3.0, 4.0, 5.0, 6.0],
    "duel_accept": [0.4, 0.45, 0.5, 0.55, 0.6],
    "duel_seller_cap": [1.8, 2.0, 2.2, 2.5],
}


def _load_env():
    if not ENV.exists():
        return
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _api(path: str):
    _load_env()
    url = os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai").rstrip("/")
    key = os.environ.get("BAZAAR_KEY") or os.environ.get("BAZAAR_TEAM_KEY")
    if not key:
        raise RuntimeError("BAZAAR_KEY missing")
    req = urllib.request.Request(url + path, headers={"X-Team-Key": key})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp)


def _jsonl(path: Path, n: int = 5000) -> list:
    if not path.exists():
        return []
    out = []
    with path.open() as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out[-n:]


def _score(d: dict) -> float:
    r = d.get("result")
    if isinstance(r, (int, float)):
        return float(r)
    if isinstance(r, dict):
        for k in ("your_score", "score", "points"):
            if isinstance(r.get(k), (int, float)):
                return float(r[k])
    return 0.0


def _rival_style(d: dict) -> str:
    """Rough rival personality from how far their price moved toward our limit."""
    rival, limit, role = d.get("rival"), d.get("your_limit"), str(d.get("role") or "")
    if limit is None or not rival:
        return "unknown"
    seller = role.startswith("sell")
    prices = [m["price"] for m in (d.get("messages") or [])
              if m.get("from") == rival and m.get("price") is not None]
    if len(prices) < 2:
        return "unknown"
    first, last = prices[0], prices[-1]
    if seller:
        move, span = last - first, max(1.0, abs(limit - first))
    else:
        move, span = first - last, max(1.0, abs(first - limit))
    frac = move / span
    if frac < 0.15:
        return "tough"
    if frac > 0.55:
        return "conceder"
    return "tit-for-tat"


def fetch_done() -> list:
    try:
        data = _api("/api/duels?done=true")
        return data.get("duels") or []
    except Exception:
        return []


def fetch_live() -> list:
    try:
        data = _api("/api/duels")
        return data.get("duels") or []
    except Exception:
        return []


def fetch_me() -> dict:
    try:
        return _api("/api/me")
    except Exception:
        return {}


def scoreboard(done: list, me: dict | None = None) -> dict:
    """Per-duel points + totals for the dashboard."""
    rows = []
    for d in done:
        sc = _score(d)
        rows.append({
            "duel": d.get("duel") or d.get("id"),
            "session": d.get("session"),
            "status": d.get("status"),
            "role": d.get("role"),
            "rival": d.get("rival"),
            "item": d.get("item"),
            "limit": d.get("your_limit"),
            "price": d.get("price"),
            "days": d.get("days"),
            "rounds": d.get("rounds") or 0,
            "points": round(sc, 2),
            "issues": d.get("issues") or ["price"],
        })
    rows.sort(key=lambda r: (-(r.get("session") or 0), -(r.get("duel") or 0)))
    total = round(sum(r["points"] for r in rows), 2)
    deals = sum(1 for r in rows if r["status"] == "deal")
    zeros = sum(1 for r in rows if r["points"] <= 0)
    by_session = {}
    for r in rows:
        s = r.get("session") or 0
        slot = by_session.setdefault(s, {"session": s, "n": 0, "points": 0.0, "deals": 0, "zeros": 0})
        slot["n"] += 1
        slot["points"] = round(slot["points"] + r["points"], 2)
        if r["status"] == "deal":
            slot["deals"] += 1
        if r["points"] <= 0:
            slot["zeros"] += 1
    # cumulative series (oldest → newest) for the sparkline
    chron = sorted(rows, key=lambda r: ((r.get("session") or 0), (r.get("duel") or 0)))
    running, cum = 0.0, []
    for r in chron:
        running = round(running + r["points"], 2)
        cum.append({"duel": r["duel"], "points": r["points"], "total": running, "status": r["status"]})
    best = max(rows, key=lambda r: r["points"], default=None)
    worst_nz = min((r for r in rows if r["points"] > 0), key=lambda r: r["points"], default=None)
    sc = (me or {}).get("score") or {}
    return {
        "at": time.time(),
        "duels": rows,
        "n": len(rows),
        "deals": deals,
        "zeros": zeros,
        "deal_rate": round(deals / len(rows), 3) if rows else None,
        "raw_total": total,
        "mean": round(total / len(rows), 2) if rows else None,
        "best": best,
        "worst_nonzero": worst_nz,
        "by_session": [by_session[k] for k in sorted(by_session)],
        "cumulative": cum,
        "duel_points": sc.get("duel_points"),
        "negotiating": sc.get("negotiating"),
        "board_score": sc.get("score"),
        "rank": sc.get("rank"),
    }


def analyze(done: list, live: list | None = None) -> dict:
    """Empirical picture of our duel performance and the rivals we face."""
    live = live or []
    rows = []
    for d in done:
        sc = _score(d)
        style = _rival_style(d)
        seller = str(d.get("role") or "").startswith("sell")
        limit, price = d.get("your_limit"), d.get("price")
        surplus = None
        if d.get("status") == "deal" and price is not None and limit is not None:
            surplus = (price - limit) if seller else (limit - price)
        rival = d.get("rival")
        rival_prices = [m["price"] for m in (d.get("messages") or [])
                        if m.get("from") == rival and m.get("price") is not None]
        last_rival = rival_prices[-1] if rival_prices else None
        missed = False
        if d.get("status") == "no_deal" and last_rival is not None and limit is not None:
            missed = (last_rival >= limit) if seller else (last_rival <= limit)
        rows.append({
            "status": d.get("status"), "score": sc, "role": d.get("role"),
            "rounds": d.get("rounds") or 0, "style": style, "surplus": surplus,
            "missed_inside": missed, "rival": rival, "session": d.get("session"),
            "decay": d.get("decay_per_round") or d.get("decay") or 0.06,
            "two_issue": "days" in (d.get("issues") or []),
        })

    n = len(rows)
    deals = [r for r in rows if r["status"] == "deal"]
    no_deals = [r for r in rows if r["status"] == "no_deal"]
    styles = Counter(r["style"] for r in rows)
    # map unknown → clone for the simulator weights
    sim_w = {"tough": 0.0, "conceder": 0.0, "tit-for-tat": 0.0, "clone": 0.0}
    for s, c in styles.items():
        key = "clone" if s in ("unknown", "mid") else ("tit-for-tat" if s == "tit-for-tat" else s)
        if key not in sim_w:
            key = "clone"
        sim_w[key] += c
    total_w = sum(sim_w.values()) or 1
    sim_w = {k: v / total_w for k, v in sim_w.items()}

    short = [r["score"] for r in rows if r["rounds"] <= 2]
    long_ = [r["score"] for r in rows if r["rounds"] >= 5]
    by_role = {role: statistics.mean([r["score"] for r in rows if r["role"] == role] or [0])
               for role in ("seller", "buyer")}

    # live pressure: how many open gaps / ticks left
    live_gaps, live_left = [], []
    for d in live:
        if d.get("status") not in ("live", "open", "active", "running", "negotiating"):
            continue
        up = (d.get("your_offer") or {}).get("price")
        tp = (d.get("rival_offer") or {}).get("price")
        if up is not None and tp is not None:
            live_gaps.append(abs(up - tp))
        if d.get("deadline_tick") is not None:
            live_left.append(d["deadline_tick"])  # absolute; caller can diff vs clock

    out = {
        "n": n, "deals": len(deals), "no_deals": len(no_deals),
        "deal_rate": (len(deals) / n) if n else None,
        "mean_score": statistics.mean([r["score"] for r in rows]) if rows else None,
        "sum_score": round(sum(r["score"] for r in rows), 2) if rows else 0,
        "mean_surplus": statistics.mean([r["surplus"] for r in deals if r["surplus"] is not None]) if deals else None,
        "mean_rounds_deal": statistics.mean([r["rounds"] for r in deals]) if deals else None,
        "mean_rounds_nodeal": statistics.mean([r["rounds"] for r in no_deals]) if no_deals else None,
        "missed_inside": sum(1 for r in rows if r["missed_inside"]),
        "short_mean": statistics.mean(short) if short else None,
        "long_mean": statistics.mean(long_) if long_ else None,
        "by_role": by_role,
        "styles": dict(styles),
        "sim_weights": sim_w,
        "two_issue_share": (sum(1 for r in rows if r["two_issue"]) / n) if n else 0,
        "live": len(live),
        "live_mean_gap": statistics.mean(live_gaps) if live_gaps else None,
        "at": time.time(),
    }
    return out


def empirical_proposal(m: dict, S: dict) -> dict:
    """Heuristic moves from real outcomes. Returns proposed knobs (may equal current)."""
    prop = {k: S[k] for k in KNOBS}
    reasons = []
    if (m.get("n") or 0) < MIN_DONE:
        return {"changes": {}, "reasons": ["need more finished duels"], "proposed": prop}

    # Long talks score worse than short ones → settle sooner
    if m.get("short_mean") is not None and m.get("long_mean") is not None and m["long_mean"] + 2 < m["short_mean"]:
        if prop["duel_rounds"] > 5:
            prop["duel_rounds"] = max(4, int(prop["duel_rounds"]) - 1)
            reasons.append(f"long talks score {m['long_mean']:.1f} vs short {m['short_mean']:.1f}: fewer rounds")
        if prop["duel_accept"] > 0.4:
            prop["duel_accept"] = round(max(0.4, float(prop["duel_accept"]) - 0.05), 2)
            reasons.append("accept a bit earlier so the pie does not melt")

    # Zeroes from walking past a good rival offer → accept sooner
    if m.get("missed_inside", 0) >= 1 and prop["duel_accept"] > 0.4:
        prop["duel_accept"] = round(max(0.4, float(prop["duel_accept"]) - 0.05), 2)
        reasons.append(f"missed {m['missed_inside']} deal(s) that were inside our limit")

    # Too many no-deals → open closer / concede faster so rivals can take us
    if m.get("deal_rate") is not None and m["deal_rate"] < 0.78:
        if prop["duel_rounds"] > 5:
            prop["duel_rounds"] = max(4, int(prop["duel_rounds"]) - 1)
            reasons.append(f"deal rate {m['deal_rate']:.0%} is low: converge faster")
        if prop["duel_seller_cap"] > 1.8:
            prop["duel_seller_cap"] = round(max(1.8, float(prop["duel_seller_cap"]) - 0.2), 1)
            reasons.append("seller opens a bit closer so rivals can accept")

    # High deal rate but weak mean score → be greedier on the open
    if m.get("deal_rate") is not None and m["deal_rate"] >= 0.85 and (m.get("mean_score") or 0) < 16:
        if prop["duel_anchor"] < 6.0:
            prop["duel_anchor"] = round(min(6.0, float(prop["duel_anchor"]) + 0.5), 1)
            reasons.append(f"deals close but mean score {m['mean_score']:.1f} is soft: open farther")

    # Live large gaps with little time → accelerate (lower rounds / accept)
    if m.get("live_mean_gap") is not None and m["live_mean_gap"] > 40 and m.get("live", 0) >= 2:
        if prop["duel_accept"] > 0.4:
            prop["duel_accept"] = round(max(0.4, float(prop["duel_accept"]) - 0.05), 2)
            reasons.append(f"live gaps avg {m['live_mean_gap']:.0f} P: take sooner")

    # Conceder-heavy field → slightly greedier open, earlier accept
    styles = m.get("styles") or {}
    n = m.get("n") or 1
    if styles.get("conceder", 0) / n >= 0.4:
        if prop["duel_anchor"] < 6.0:
            prop["duel_anchor"] = round(min(6.0, float(prop["duel_anchor"]) + 0.5), 1)
            reasons.append("many conceders: open farther")
        if prop["duel_accept"] > 0.45:
            prop["duel_accept"] = round(max(0.45, float(prop["duel_accept"]) - 0.05), 2)
            reasons.append("many conceders: bank the pie earlier")

    # never open so high that rivals cannot take us (hurts deal rate more than it gains surplus)
    if prop["duel_seller_cap"] > 2.2 and (m.get("deal_rate") or 1) < 0.9:
        prop["duel_seller_cap"] = 2.2
    changes = {k: prop[k] for k in KNOBS if prop[k] != S.get(k)}
    return {"changes": changes, "reasons": reasons or ["empirical picture matches current knobs"], "proposed": prop}


def simulate(S: dict, weights: dict, n: int = 80) -> dict:
    sys.path.insert(0, str(HERE / "tests"))
    import tournament as T  # noqa: WPS433
    ev = T.eval_duels(S, n=n, seed=7)
    wmean = sum(weights.get(s, 0.25) * ev[s] for s in ("tough", "conceder", "tit-for-tat", "clone"))
    return {"by_style": {k: round(v, 3) for k, v in ev.items()},
            "wmean": round(wmean, 3), "worst": round(min(ev.values()), 3)}


def search(S: dict, weights: dict, focus: dict | None = None) -> dict:
    """Small grid around current + empirical focus. Returns best candidate."""
    sys.path.insert(0, str(HERE / "tests"))
    import tournament as T  # noqa: WPS433

    # shrink the grid around current values + any empirical proposal
    def nearby(key, cur):
        opts = GRID[key]
        if cur in opts:
            i = opts.index(cur)
            return opts[max(0, i - 1): i + 2]
        # closest three
        ordered = sorted(opts, key=lambda x: abs(x - cur))
        return sorted(set(ordered[:3]))

    base = {k: S[k] for k in KNOBS}
    focus = focus or {}
    axes = {}
    for k in KNOBS:
        vals = set(nearby(k, base[k]))
        if k in focus:
            vals.add(focus[k])
        axes[k] = sorted(vals)

    cur_ev = T.eval_duels(S, n=60, seed=7)
    cur_w = sum(weights.get(s, 0.25) * cur_ev[s] for s in cur_ev)
    best, best_w, best_ev = dict(base), cur_w, cur_ev
    for vals in itertools.product(*[axes[k] for k in KNOBS]):
        cand = {**S, **dict(zip(KNOBS, vals))}
        ev = T.eval_duels(cand, n=60, seed=7)
        w = sum(weights.get(s, 0.25) * ev[s] for s in ev)
        # robust: prefer mean of wmean and worst
        score = 0.5 * w + 0.5 * min(ev.values())
        best_score = 0.5 * best_w + 0.5 * min(best_ev.values())
        if score > best_score + 1e-6:
            best, best_w, best_ev = {k: cand[k] for k in KNOBS}, w, ev
    return {
        "current_wmean": round(cur_w, 3),
        "best_wmean": round(best_w, 3),
        "best": best,
        "by_style": {k: round(v, 3) for k, v in best_ev.items()},
        "gain": round(best_w - cur_w, 3),
    }


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"touched": {}, "history": [], "trial": None}


def _save_state(st: dict):
    LOGS.mkdir(exist_ok=True)
    # keep history short
    st["history"] = (st.get("history") or [])[-40:]
    STATE.write_text(json.dumps(st, indent=1, default=str))


def _set_knobs(changes: dict) -> dict:
    raw = json.loads(strategy.PATH.read_text()) if strategy.PATH.exists() else {}
    raw.update(changes)
    return strategy.save(raw)


def _clock_tick() -> int | None:
    try:
        return int(_api("/api/clock").get("tick"))
    except Exception:
        return None


def learn(apply: bool = True) -> dict:
    """One learning cycle. Safe to call from a loop or the dashboard."""
    S = strategy.load()
    if not S.get("enable_duels", 1) or not S.get("duel_autotune", 1):
        snap = {"status": "disabled", "at": time.time(),
                "note": "enable_duels or duel_autotune is off"}
        st = _load_state(); st["last"] = snap; _save_state(st)
        return snap

    done, live, me = fetch_done(), fetch_live(), fetch_me()
    m = analyze(done, live)
    board = scoreboard(done, me)
    m["duel_points"] = board.get("duel_points")
    m["rank"] = board.get("rank")
    m["raw_total"] = board.get("raw_total")
    tick = _clock_tick() or m.get("n") or 0
    try:
        LOGS.mkdir(exist_ok=True)
        BOARD.write_text(json.dumps(board, indent=1, default=str))
    except OSError:
        pass

    emp = empirical_proposal(m, S)
    sim = search(S, m.get("sim_weights") or {}, focus=emp.get("proposed"))

    # Merge: start from empirical, then take sim values when sim gain is clear and agrees on direction
    proposed = dict(emp["proposed"])
    reasons = list(emp["reasons"])
    if sim["gain"] >= MIN_GAIN:
        for k in KNOBS:
            if sim["best"][k] != S.get(k):
                # prefer sim when empirical had no opinion on this knob
                if k not in emp["changes"] or emp["changes"].get(k) == sim["best"][k]:
                    proposed[k] = sim["best"][k]
        reasons.append(f"sim +{sim['gain']:.3f} pie share vs observed rival mix")
    elif sim["gain"] > 0 and not emp["changes"]:
        proposed = dict(sim["best"])
        reasons.append(f"sim slight edge +{sim['gain']:.3f}; empirical stable")

    # hard caps from the field: keep opens takeable while we are still dropping deals
    if (m.get("deal_rate") or 1) < 0.9 and proposed.get("duel_seller_cap", 2.2) > 2.2:
        proposed["duel_seller_cap"] = 2.2
        reasons.append("cap seller open at 2.2× while deal rate < 90%")

    changes = {k: proposed[k] for k in KNOBS if proposed[k] != S.get(k)}
    st = _load_state()

    # judge previous trial against mean_score if we have one
    trial = st.get("trial")
    if trial and tick - trial.get("tick", 0) >= COOLDOWN_TICKS:
        before, after = trial.get("before_mean"), m.get("mean_score")
        kept = after is None or before is None or after >= before - 0.3
        if not kept and apply and all(S.get(k) == trial["new"].get(k) for k in trial["new"]):
            _set_knobs(trial["old"])
            reasons.append(f"reverted trial: mean score {before} → {after}")
            changes = {}
            proposed = {k: S[k] for k in KNOBS}
        st["trial"] = None

    applied = {}
    status = "current_is_best"
    if changes and (m.get("n") or 0) >= MIN_DONE:
        # cooldown per knob
        ready = {k: v for k, v in changes.items()
                 if tick - (st.get("touched") or {}).get(k, -10**6) >= COOLDOWN_TICKS}
        if not ready:
            status = "cooldown"
        else:
            # one step at a time when moving several knobs: apply the whole ready set (small)
            if apply:
                old = {k: S[k] for k in ready}
                saved = _set_knobs(ready)
                applied = {k: saved[k] for k in ready}
                for k in ready:
                    st.setdefault("touched", {})[k] = tick
                st["trial"] = {"tick": tick, "old": old, "new": applied,
                               "before_mean": m.get("mean_score")}
                status = "applied"
            else:
                applied = ready
                status = "proposal"
    elif changes:
        status = "insufficient_data"
    else:
        status = "current_is_best"

    snap = {
        "status": status, "at": time.time(), "tick": tick,
        "metrics": m, "empirical": emp, "sim": sim,
        "current": {k: S[k] for k in KNOBS},
        "proposed": proposed, "changes": changes, "applied": applied,
        "reasons": reasons,
        "lesson": _lesson(status, m, changes or applied, reasons),
        "scoreboard": {
            "raw_total": board.get("raw_total"), "duel_points": board.get("duel_points"),
            "n": board.get("n"), "deals": board.get("deals"), "zeros": board.get("zeros"),
            "mean": board.get("mean"), "by_session": board.get("by_session"),
            "best": board.get("best"), "cumulative": board.get("cumulative"),
            "duels": board.get("duels"), "rank": board.get("rank"),
            "negotiating": board.get("negotiating"), "board_score": board.get("board_score"),
        },
    }
    st["last"] = snap
    st.setdefault("history", []).append({
        "at": snap["at"], "status": status, "applied": applied, "mean_score": m.get("mean_score"),
        "deal_rate": m.get("deal_rate"), "duel_points": m.get("duel_points"),
    })
    _save_state(st)
    # mirror into the agent decisions log so the Duels tab can show it without a new API
    try:
        LOGS.mkdir(exist_ok=True)
        with DECISIONS.open("a") as f:
            f.write(json.dumps({
                "ts": snap["at"], "tick": tick, "module": "duel_tuner", "action": status,
                "applied": applied, "changes": changes, "proposed": proposed,
                "mean_score": m.get("mean_score"), "deal_rate": m.get("deal_rate"),
                "duel_points": m.get("duel_points"), "raw_total": board.get("raw_total"),
                "n": m.get("n"), "styles": m.get("styles"),
                "reasons": reasons, "lesson": snap["lesson"],
                "current": snap["current"], "sim_wmean": (sim or {}).get("best_wmean"),
                "scoreboard": snap["scoreboard"],
            }, default=str) + "\n")
    except OSError:
        pass

    # El Consejo note (best-effort)
    try:
        import council
        council.WHO.update(writer="tuner", role="tuner")
        council.post("tuner", "duels", snap["lesson"], {
            "status": status, "applied": applied, "changes": changes,
            "mean_score": m.get("mean_score"), "deal_rate": m.get("deal_rate"),
            "duel_points": m.get("duel_points"), "styles": m.get("styles"),
        }, tick=tick)
        council.sync()
    except Exception:
        pass
    return snap


def _lesson(status, m, changes, reasons) -> str:
    bits = [f"{m.get('n', 0)} finished duels, deal rate "
            f"{(m.get('deal_rate') or 0):.0%}, mean score {m.get('mean_score') or 0:.1f}, "
            f"duel points {m.get('duel_points') if m.get('duel_points') is not None else '–'}."]
    if changes:
        bits.append("Knobs " + ", ".join(f"{k}→{v}" for k, v in changes.items()) + f" ({status}).")
    else:
        bits.append(f"No knob change ({status}).")
    if reasons:
        bits.append(" ".join(reasons[:3]))
    return " ".join(bits)


def run(apply: bool, once: bool = False):
    LOGS.mkdir(exist_ok=True)
    while True:
        try:
            snap = learn(apply=apply)
            print(json.dumps({
                "status": snap["status"], "applied": snap.get("applied"),
                "mean_score": (snap.get("metrics") or {}).get("mean_score"),
                "deal_rate": (snap.get("metrics") or {}).get("deal_rate"),
                "duel_points": (snap.get("metrics") or {}).get("duel_points"),
                "lesson": snap.get("lesson"),
            }))
        except Exception as e:
            err = {"status": "error", "error": repr(e), "at": time.time()}
            print(json.dumps(err))
            st = _load_state(); st["last"] = err; _save_state(st)
        if once:
            return
        time.sleep(CYCLE_SECONDS)


def status() -> dict:
    st = _load_state()
    return st.get("last") or {"status": "never_run"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry", action="store_true")
    r.add_argument("--once", action="store_true")
    sub.add_parser("once")
    sub.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "status":
        print(json.dumps(status(), indent=2, default=str))
    elif a.cmd == "once":
        run(apply=True, once=True)
    else:
        run(apply=not a.dry, once=getattr(a, "once", False))
