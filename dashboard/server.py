"""Team 13 war room: a local poller/cache for the Bazaar API plus a page that reads it.

    source ../bazaar.env && python3 server.py      # then open http://localhost:8765

Public routes are read without the team key (their own, generous rate limit), so the dashboard
never eats into the 5 req/s our agents need. Team routes are spaced out in one background thread.
"""
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

URL = os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai")
KEY = os.environ["BAZAAR_KEY"]
PORT = int(os.environ.get("PORT", "8765"))
HERE = Path(__file__).parent
PAGE = HERE / "index.html"
HISTORY = HERE / "history.json"
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


def agent_running():
    """Is team13/agent.py running on this machine? (matches ' agent.py' or '/agent.py', not starter_agent.py)"""
    try:
        return subprocess.run(["pgrep", "-f", "[ /]agent\\.py"], capture_output=True).returncode == 0
    except Exception:
        return None


def poll():
    while True:
        now = time.time()
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
    def do_GET(self):
        if self.path.startswith("/data"):
            with lock:
                body = json.dumps({**cache, "history": history, "served_at": time.time(),
                                   "decisions": tail(DECISIONS), "broker_log": tail(BROKER_LOG, 60)}).encode()
            self._send(200, "application/json", body)
        elif self.path in ("/", "/index.html"):
            self._send(200, "text/html; charset=utf-8", PAGE.read_bytes())
        else:
            self._send(404, "text/plain", b"not found")

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
    print(f"Team 13 war room on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
