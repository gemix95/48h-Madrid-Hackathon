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
import advisor  # noqa: E402  (team13/advisor.py: recalibrates on every team's deals, proposes better settings)
RECHECK = threading.Event()
DECISIONS = HERE.parent / "team13" / "logs" / "decisions.jsonl"
BROKER_LOG = HERE.parent / "team13" / "logs" / "broker.jsonl"


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
    "clock": ("/api/clock", 2, False),
    "board": ("/api/venues/rastro/offers", 5, False),
    "feed": ("/api/feed?limit=60", 5, False),
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
            with lock:
                cache["intel"] = INTEL.summary()
            intel_due = now + 10
        for name, (path, every, keyed) in ROUTES.items():
            if now < due[name]:
                continue
            data = get(path, keyed)
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
            with lock:
                body = json.dumps({**cache, "history": history, "served_at": time.time(),
                                   "decisions": tail(DECISIONS), "broker_log": tail(BROKER_LOG, 60)}).encode()
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
        if not self.path.startswith("/strategy"):
            return self._send(404, "text/plain", b"not found")
        try:
            n = int(self.headers.get("Content-Length", 0))
            values = json.loads(self.rfile.read(min(n, 20000)) or b"{}")
            saved = strategy.save(values if isinstance(values, dict) else {})
            self._send(200, "application/json", json.dumps({"ok": True, "current": saved}).encode())
        except ValueError as e:
            self._send(400, "application/json", json.dumps({"ok": False, "error": str(e)}).encode())

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=poll, daemon=True).start()
    threading.Thread(target=listen, daemon=True).start()
    threading.Thread(target=advise, daemon=True).start()
    print(f"Team 13 war room on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
