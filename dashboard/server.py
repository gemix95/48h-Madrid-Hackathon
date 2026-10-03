"""Team 13 war room: a local poller/cache for the Bazaar API plus a page that reads it.

    source ../bazaar.env && python3 server.py      # then open http://localhost:8765

Public routes are read without the team key (their own, generous rate limit), so the dashboard
never eats into the 5 req/s our agents need. Team routes are spaced out in one background thread.
"""
import base64
import hmac
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

URL = os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai")
KEY = os.environ["BAZAAR_KEY"]
PORT = int(os.environ.get("PORT", "8765"))
# Set DASHBOARD_PASSWORD to require a login (user "team13") — always do this before sharing the dashboard remotely.
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
HERE = Path(__file__).parent
PAGE = HERE / "index.html"
HISTORY = HERE / "history.json"
sys.path.insert(0, str(HERE.parent / "team13"))
import strategy  # noqa: E402  (team13/strategy.py: the knobs the agent reads every tick)
from intel import Intel  # noqa: E402  (team13/intel.py: what every team does, from the public feed)
INTEL = Intel(HERE / "feed_events.jsonl", url=URL)
from learner import Learner  # noqa: E402  (team13/learner.py: lessons from every dealer conversation)
LEARNER = Learner()
STATE = HERE.parent / "team13" / "state.json"
import advisor  # noqa: E402  (team13/advisor.py: recalibrates on every team's deals, proposes better settings)
RECHECK = threading.Event()
DECISIONS = HERE.parent / "team13" / "logs" / "decisions.jsonl"
BROKER_LOG = HERE.parent / "team13" / "logs" / "broker.jsonl"
import council  # noqa: E402  (team13/council.py: El Consejo, the board where our agents post what they learnt)
from logindex import LogIndex, message_origins  # noqa: E402  (who sent each of our messages, what the guard cancelled and why)
import swaps  # noqa: E402  (dashboard/swaps.py: swap opportunities and deals, and the one write the dashboard makes)
import workshop_panel as workshop_tab  # noqa: E402  (El Taller tab; team13/workshop.py is the agent module)
HAND_LOG = HERE.parent / "logs" / "hand.jsonl"
AUTO = swaps.Auto(on=os.environ.get("AUTO_SWAPS", "1") != "0")  # AUTO_SWAPS=0: this dashboard never sends swaps by itself


def _restore_longshots():
    """Long shots sent before a restart must not raise the auto bar for good-for-both swaps: read them from the hand log."""
    try:
        for line in HAND_LOG.read_text().splitlines()[-2000:]:
            rec = json.loads(line)
            if rec.get("ev") == "swap_offer" and rec.get("auto") == "longshot" and (rec.get("r") or {}).get("offer"):
                AUTO.longshot_ids.add(rec["r"]["offer"])
    except (OSError, ValueError):
        pass
# the bots' own logs, so they only exist on the laptop that runs them: agent.py writes decisions.jsonl, agent/hand.py hand.jsonl
INDEX = LogIndex({"agent": DECISIONS, "manual": HERE.parent / "logs" / "hand.jsonl"})


def tail(path, n=150):
    """Last n JSON lines of an agent log (the agent appends, we only read)."""
    if not path.exists():
        return []
    with path.open("rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 200_000))
        lines = f.read().decode(errors="replace").splitlines()[-n:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out

# name: (path, seconds between refreshes, needs the team key)
ROUTES = {
    "me": ("/api/me", 3, True),
    "threads": ("/api/me/threads", 3, True),
    "offers": ("/api/me/offers", 6, True),
    "duels": ("/api/duels", 6, True),
    "duels_done": ("/api/duels?done=true", 15, True),
    "clock": ("/api/clock", 2, False),
    "board": ("/api/venues/rastro/offers", 5, False),
    "feed": ("/api/feed?limit=200", 5, False),
    "leaderboard": ("/api/leaderboard", 30, False),
    "levels": ("/api/levels", 30, False),
    "venues": ("/api/venues", 30, False),
    "dealers": ("/api/dealers", 60, False),
    "schedule": ("/api/schedule", 60, False),
    "catalog": ("/api/catalog", 300, False),
}

cache: dict = {}
due: dict = {name: 0.0 for name in ROUTES}
history: list = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
lock = threading.Lock()


def get(path, keyed):
    req = urllib.request.Request(URL + path, headers={"X-Team-Key": KEY} if keyed else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode(errors="replace")[:300]}
    except Exception as e:
        return {"_error": str(e)}


def post(path, body):
    """The one write the dashboard makes with our key (the Swaps tab's button), shaped like get()."""
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"X-Team-Key": KEY, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode(errors="replace")[:300]}
    except Exception as e:
        return {"_error": str(e)}


_swaps = {"key": None, "view": None}
_workshop = {"key": None, "view": None}


def swaps_view():
    """Swap opportunities and deals for the Negotiations > Swaps tab; recomputed only when the tick, the public feed
    or our offers changed. Called with `lock` held."""
    me, cat, offers = cache.get("me"), cache.get("catalog"), (cache.get("offers") or {}).get("offers")
    if not (isinstance(me, dict) and me.get("assets") is not None and isinstance(cat, dict) and "sets" in cat and offers is not None):
        return None
    min_gain, tick = strategy.load().get("trade_min_gain", 3), (cache.get("clock") or {}).get("tick") or me.get("tick") or 0
    key = (len(INTEL.events), tick, tuple(sorted(o["id"] for o in offers)), min_gain)
    if _swaps["key"] != key:
        try:
            events = sorted(list(INTEL.events.values()), key=lambda e: e["id"])
            _swaps["view"] = swaps.view(me, cat, events, cache.get("leaderboard"), offers, (cache.get("venues") or {}).get("venues", []), min_gain, tick)
        except Exception as e:
            _swaps["view"] = {"error": repr(e)[:200]}
        _swaps["key"] = key
    return _swaps["view"]


def workshop_view():
    """Workshop tab payload; recomputed when hand, offers, or strategy change."""
    me, cat = cache.get("me"), cache.get("catalog")
    offers = cache.get("offers")
    threads = (cache.get("threads") or {}).get("threads")
    if not (isinstance(me, dict) and me.get("assets") is not None and isinstance(cat, dict) and "sets" in cat):
        return None
    S = strategy.load()
    tick = (cache.get("clock") or {}).get("tick") or me.get("tick") or 0
    try:
        st_mtime = STATE.stat().st_mtime
    except OSError:
        st_mtime = 0
    offer_ids = tuple(sorted(o["id"] for o in (offers or {}).get("offers", []) if "id" in o))
    key = (tick, len(me.get("assets", [])), offer_ids, S.get("workshop_edge"), S.get("workshop_spares"), S.get("enable_workshop"), st_mtime)
    if _workshop["key"] != key:
        try:
            st = json.loads(STATE.read_text()) if STATE.exists() else {}
            agent_bits = {k: st.get(k) for k in ("flip", "loans")}
            _workshop["view"] = workshop_tab.view(me, cat, offers, threads, cache.get("levels"), S, agent_bits, tail(DECISIONS, 400))
        except Exception as e:
            _workshop["view"] = {"error": repr(e)[:200]}
        _workshop["key"] = key
    return _workshop["view"]


def swaps_payload():
    """swaps_view() plus each row's auto-send countdown (or why it won't send itself) and the auto-send state."""
    v = swaps_view()
    if not v or "opportunities" not in v:
        return v
    return {**v, "opportunities": [{**r, "auto": AUTO.state(r)} for r in v["opportunities"]], "auto": AUTO.snapshot()}


_ann = {"n": -1, "v": []}


def announcements():
    """Every announcement on the big screen, newest first: ours and the other teams' (from our copy of the public feed)."""
    n = len(INTEL.events)
    if _ann["n"] != n:
        evs = sorted((e for e in list(INTEL.events.values()) if e.get("type") == "venue.announcement"), key=lambda e: -e["id"])[:60]
        _ann["v"] = [{"id": e["id"], "tick": e.get("tick"), "venue": (e.get("payload") or {}).get("venue"),
                      "name": (e.get("payload") or {}).get("name"), "text": str((e.get("payload") or {}).get("text") or "")[:300]} for e in evs]
        _ann["n"] = n
    return _ann["v"]


def log_hand(rec):
    """Same log as agent/hand.py, so every swap sent from the dashboard is traceable."""
    try:
        HAND_LOG.parent.mkdir(exist_ok=True)
        with HAND_LOG.open("a") as f:
            f.write(json.dumps({"ts": time.time(), **rec}) + "\n")
    except OSError:
        pass


def autosend():
    """Once a second: arm the countdowns (swaps.Auto) and send the swap whose time has come, at most one per 30 s.
    Right before sending it re-reads our offers from the game, so a swap another laptop just sent is not sent twice."""
    while True:
        time.sleep(1)
        try:
            with lock:
                v = swaps_view()
                me, cat, offers, venues = cache.get("me"), cache.get("catalog"), (cache.get("offers") or {}).get("offers"), (cache.get("venues") or {}).get("venues")
            if not (v and "opportunities" in v and isinstance(me, dict) and offers is not None and venues):
                continue
            S = strategy.load()
            min_gain = S.get("trade_min_gain", 3)
            r = AUTO.plan(time.time(), v["opportunities"], offers, me.get("id"), min_gain)
            if not r:
                r = AUTO.longshot(time.time(), v["opportunities"], offers, me.get("id"), S)
                if not r:
                    continue
            fresh = get("/api/me/offers", True)
            if "_error" in fresh:
                continue
            if r.get("both") and AUTO.check(r, time.time(), fresh.get("offers", []), me.get("id"), min_gain):
                AUTO.armed.pop(AUTO.key(r), None)  # the game moved on (asked meanwhile, too many open): not this one
                continue
            res = swaps.post_swap(post, time.time(), me, cat, fresh.get("offers", []), venues, min_gain, r["team"], r["want"], r["asset"], cache.get("leaderboard"))
            AUTO.done(time.time(), r, res)
            log_hand({"ev": "swap_offer", "auto": "both" if r.get("both") else "longshot", "team": r["team"], "want": r["want"], "asset": r["asset"], "r": res})
            if res.get("ok"):
                due["offers"] = 0
        except Exception as e:
            print("autosend:", repr(e)[:200], flush=True)


def record(me, clock):
    """One history point per tick (or per minute while the clock is paused)."""
    s = me.get("score") or {}
    point = {"ts": time.time(), "tick": me.get("tick"), "t_hours": clock.get("t_hours"), "cash": me.get("cash"),
             "collection": me.get("collection_value"), "score": s.get("score"), "negotiating": s.get("negotiating"),
             "market": s.get("market"), "rank": s.get("rank"), "deals": s.get("deals")}
    if history and history[-1]["tick"] == point["tick"] and point["ts"] - history[-1]["ts"] < 60:
        return
    history.append(point)
    del history[:-5000]
    HISTORY.write_text(json.dumps(history))


def listen():
    """Our team's live event stream: on any event, refresh team data at once instead of waiting for the poll."""
    while True:
        try:
            req = urllib.request.Request(URL + "/api/events/stream?scope=team", headers={"X-Team-Key": KEY, "Accept": "text/event-stream"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                for raw in resp:
                    if raw.startswith(b"data:") or raw.startswith(b"event:"):
                        for name in ("me", "threads", "offers", "duels", "feed", "board"):
                            due[name] = 0
        except Exception:
            time.sleep(5)


PRICE_IN, PRICE_OUT = 4.0, 20.0  # Claude Opus 5.5, $ per million tokens (input, output incl. thinking)
_spend = {"offset": 0, "calls": 0, "tin": 0, "tout": 0, "by_day": {}, "since": {}}


def api_spend():
    """Exact Claude spend from the agent's log (every call records its input/output tokens). Incremental read."""
    if not DECISIONS.exists():
        return {}
    with DECISIONS.open("rb") as f:
        f.seek(_spend["offset"])
        chunk = f.read()
        _spend["offset"] = f.tell()
    for line in chunk.decode(errors="replace").splitlines():
        if '"module": "llm"' not in line or '"action": "message"' not in line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        tin, tout = r.get("tokens_in") or 0, r.get("tokens_out") or 0
        usd = tin * PRICE_IN / 1e6 + tout * PRICE_OUT / 1e6
        day = time.strftime("%Y-%m-%d", time.localtime(r.get("ts", time.time())))
        _spend["calls"] += 1
        _spend["tin"] += tin
        _spend["tout"] += tout
        _spend["by_day"][day] = _spend["by_day"].get(day, 0.0) + usd
        _spend.setdefault("events", []).append((r.get("ts", 0), usd))
        del _spend["events"][:-20000]
    total = sum(_spend["by_day"].values())
    today = _spend["by_day"].get(time.strftime("%Y-%m-%d"), 0.0)
    # credit left: the balance you entered, minus what was spent after you entered it
    S = strategy.load()
    credit = S.get("api_credit_usd") or 0
    set_at = (strategy.PATH.stat().st_mtime if strategy.PATH.exists() else 0)
    if credit and _spend["since"].get("credit") != credit:
        _spend["since"] = {"credit": credit, "at": set_at}
    spent_since = sum(u for ts, u in _spend.get("events", []) if ts >= _spend["since"].get("at", 0)) if credit else 0
    return {"calls": _spend["calls"], "tokens_in": _spend["tin"], "tokens_out": _spend["tout"], "usd_total": round(total, 4),
            "usd_today": round(today, 4), "credit_set": credit, "credit_left": round(credit - spent_since, 2) if credit else None,
            "avg_usd_per_call": round(total / _spend["calls"], 5) if _spend["calls"] else None}


def list_prices():
    lp = {}
    for p in (cache.get("dealers") or {}).get("personas", []):
        for s in (p.get("menu") or {}).get("sells", []):
            lp[f"buy:pack:{s['pack']}" if s.get("pack") else f"buy:card:{s.get('rarity')}"] = s.get("list_price")
    return lp


def advise():
    """Every 5 minutes (or on demand): re-run the tournament on what all teams got, store any better strategy."""
    time.sleep(20)  # let the first feed refresh land
    while True:
        try:
            res = advisor.propose(INTEL.summary(), list_prices(), strategy.load())
            with lock:
                dismissed = (cache.get("proposal") or {}).get("dismissed")
                if dismissed and res.get("changes") == dismissed:
                    res["status"], res["note"] = "dismissed", "you dismissed this proposal"
                res["dismissed"] = dismissed
                cache["proposal"] = res
        except Exception as e:
            with lock:
                cache["proposal"] = {"status": "error", "error": repr(e)[:200], "at": time.time()}
        RECHECK.wait(300)
        RECHECK.clear()


def agent_running():
    """Is team13/agent.py running on this machine? (matches ' agent.py' or '/agent.py', not starter_agent.py)"""
    try:
        return subprocess.run(["pgrep", "-f", "[ /]agent\\.py"], capture_output=True).returncode == 0
    except Exception:
        return None


def poll():
    intel_due = boards_due = 0.0
    while True:
        now = time.time()
        if now >= boards_due and isinstance(cache.get("venues"), dict):  # every market's book, public reads
            boards = {}
            for v in cache["venues"].get("venues", []):
                if v.get("status") == "open":
                    data = get(f"/api/venues/{v['venue']}/offers", False)
                    if "_error" not in data:
                        boards[v["venue"]] = data.get("offers", [])
            with lock:
                cache["boards"] = boards
            boards_due = now + 8
        if now >= intel_due:  # public feed, no key: our own copy for the Intel tab
            if isinstance(cache.get("catalog"), dict) and "sets" in cache["catalog"]:
                INTEL.set_catalog(cache["catalog"])
            INTEL.refresh()
            summ = INTEL.summary()
            me_id = (cache.get("me") or {}).get("id", "t13")
            learned = LEARNER.fit(summ.get("dealer_threads", []), me_id, summ.get("now_tick")) if INTEL.rarity else None
            with lock:
                cache["intel"] = {k: v for k, v in summ.items() if k != "dealer_threads"}
                if learned:
                    cache["learner"] = learned
            intel_due = now + 10
        for name, (path, every, keyed) in ROUTES.items():
            if now < due[name]:
                continue
            data = get(path, keyed)
            if name in ("duels", "duels_done") and isinstance(data, dict):
                for d in data.get("duels") or []:  # API names the id "duel"; keep both
                    if d.get("id") is None and d.get("duel") is not None:
                        d["id"] = d["duel"]
            with lock:
                if "_error" in data and name in cache and "_error" not in cache[name]:
                    cache[name + "_error"] = data  # keep the last good value on a hiccup
                else:
                    cache[name] = data
                    cache.pop(name + "_error", None)
                if name == "me" and "_error" not in data and isinstance(cache.get("clock"), dict):
                    record(data, cache["clock"])
            due[name] = now + every
            time.sleep(0.25 if keyed else 0.05)  # ~1 req/s on the team key, leaving room for our agents
        with lock:
            cache["agent_running"] = agent_running()
        time.sleep(0.3)


class Handler(BaseHTTPRequestHandler):
    def _authorized(self):
        """HTTP Basic auth when DASHBOARD_PASSWORD is set; the browser shows its own login box."""
        if not PASSWORD:
            return True
        header = self.headers.get("Authorization", "")
        if header.startswith("Basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                if hmac.compare_digest(pw, PASSWORD):
                    return True
            except ValueError:
                pass
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="Team 13 war room"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self):
        if not self._authorized():
            return
        if self.path.startswith("/data"):
            INDEX.refresh()
            with lock:
                try:  # what the agent is doing right now (its own state file; read-only)
                    st = json.loads(STATE.read_text())
                    agent_state = {k: st.get(k) for k in ("plans", "team_haggles", "listings", "bandit", "venue", "invites", "flip", "loans")}
                    agent_state["plans"] = {k: v for k, v in (agent_state["plans"] or {}).items() if not v.get("done")}
                except (OSError, ValueError):
                    agent_state = {}
                try:
                    council_state = json.loads(council.STATE.read_text())
                except (OSError, ValueError):
                    council_state = {}
                council_view = {"notes": council.read(n=200), "state": council_state,
                                "rules": {k: {"metric": m, "low": lo, "high": hi, "safe": council.SAFE[k]}
                                          for k, (m, lo, hi, _) in council.RULES.items()},
                                "trial_ticks": council.TRIAL_TICKS, "cooldown_ticks": council.COOLDOWN_TICKS,
                                "cycle_seconds": council.CYCLE_SECONDS}
                try:
                    duel_learn = json.loads((HERE.parent / "team13" / "logs" / "duel_learn.json").read_text())
                except (OSError, ValueError):
                    duel_learn = {}
                try:
                    duels_board = json.loads((HERE.parent / "team13" / "logs" / "duels_board.json").read_text())
                except (OSError, ValueError):
                    duels_board = {}
                body = json.dumps({**cache, "api_spend": api_spend(), "agent_state": agent_state, "council": council_view,
                                   "history": history, "served_at": time.time(),
                                   "decisions": tail(DECISIONS), "broker_log": tail(BROKER_LOG, 60),
                                   "origins": message_origins(INDEX, cache.get("threads"), (cache.get("me") or {}).get("id")),
                                   "log_sources": INDEX.status(), "guard": INDEX.recent_guard(), "swaps": swaps_payload(),
                                   "announcements": announcements(), "duel_learn": duel_learn, "duels_board": duels_board,
                                   "workshop": workshop_view()}).encode()
            self._send(200, "application/json", body)
        elif self.path.startswith("/strategy"):
            self._send(200, "application/json", json.dumps(strategy.describe()).encode())
        elif self.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", PAGE.read_bytes())
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        if not self._authorized():
            return
        if self.path.startswith("/strategy/recheck"):
            RECHECK.set()
            return self._send(200, "application/json", b'{"ok": true}')
        if self.path.startswith("/strategy/dismiss"):
            with lock:
                prop = cache.get("proposal") or {}
                prop["dismissed"], prop["status"] = prop.get("changes"), "dismissed"
            return self._send(200, "application/json", b'{"ok": true}')
        if self.path.startswith("/swap/auto"):
            return self._swap_auto()
        if self.path.startswith("/swap"):
            return self._swap()
        if not self.path.startswith("/strategy"):
            return self._send(404, "text/plain", b"not found")
        try:
            n = int(self.headers.get("Content-Length", 0))
            values = json.loads(self.rfile.read(min(n, 20000)) or b"{}")
            saved = strategy.save(values if isinstance(values, dict) else {})
            self._send(200, "application/json", json.dumps({"ok": True, "current": saved}).encode())
        except ValueError as e:
            self._send(400, "application/json", json.dumps({"ok": False, "error": str(e)}).encode())

    def _swap(self):
        """Post one swap offer with our key, straight from here: it does not go through the agent or its guard."""
        reply = lambda res, code=200: self._send(code, "application/json", json.dumps(res).encode())
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):  # a form on another site cannot send this
            return reply({"ok": False, "error": "send JSON"}, 415)
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(min(n, 2000)) or b"{}")
            team, want, asset = str(req["team"]), str(req["want"]), int(req["asset"])
        except (ValueError, KeyError, TypeError):
            return reply({"ok": False, "error": "bad request"}, 400)
        with lock:
            me, cat, offers, venues = cache.get("me"), cache.get("catalog"), (cache.get("offers") or {}).get("offers"), (cache.get("venues") or {}).get("venues")
        if not (isinstance(me, dict) and me.get("assets") is not None and isinstance(cat, dict) and "sets" in cat and offers is not None and venues):
            return reply({"ok": False, "error": "no team data yet, try again in a few seconds"})
        res = swaps.post_swap(post, time.time(), me, cat, offers, venues, strategy.load().get("trade_min_gain", 3), team, want, asset, cache.get("leaderboard"))
        log_hand({"ev": "swap_offer", "team": team, "want": want, "asset": asset, "r": res})
        if res.get("ok"):
            due["offers"] = 0  # show it in Our offers at once
        reply(res)

    def _swap_auto(self):
        """Auto-send switches: {"on": true|false} for all of it, {"cancel": "<team>|<card>|<asset>"} for one row."""
        reply = lambda res, code=200: self._send(code, "application/json", json.dumps(res).encode())
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return reply({"ok": False, "error": "send JSON"}, 415)
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(min(n, 2000)) or b"{}")
        except ValueError:
            return reply({"ok": False, "error": "bad request"}, 400)
        if "on" in req:
            AUTO.set_on(bool(req["on"]))
        if isinstance(req.get("cancel"), str):
            AUTO.cancel(req["cancel"])
        reply({"ok": True, "auto": AUTO.snapshot()})

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    INDEX.refresh()  # read the logs written so far once, before the first page load
    threading.Thread(target=poll, daemon=True).start()
    council.start_sync()  # the shared board (council branch): pulled every 15 s, so every laptop sees every agent's notes
    threading.Thread(target=listen, daemon=True).start()
    threading.Thread(target=advise, daemon=True).start()
    _restore_longshots()
    threading.Thread(target=autosend, daemon=True).start()
    print(f"Team 13 war room on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
