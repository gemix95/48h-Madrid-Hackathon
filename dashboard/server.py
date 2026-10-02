"""Team 13 live dashboard: a tiny local proxy to the Bazaar API plus a page that polls it.

    BAZAAR_KEY=tk-xxxx-xxxx python3 server.py      # then open http://localhost:8765
"""
import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

URL = os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai")
KEY = os.environ["BAZAAR_KEY"]
PORT = int(os.environ.get("PORT", "8765"))
PAGE = Path(__file__).with_name("index.html")

ROUTES = {
    "me": "/api/me",
    "threads": "/api/me/threads",
    "offers": "/api/me/offers",
    "clock": "/api/clock",
    "leaderboard": "/api/leaderboard",
    "feed": "/api/feed?limit=40",
    "levels": "/api/levels",
}


def get(path):
    req = urllib.request.Request(URL + path, headers={"X-Team-Key": KEY})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read().decode(errors="replace")[:300]}
    except Exception as e:  # network hiccup: the page keeps the last good value
        return {"_error": str(e)}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/data"):
            with ThreadPoolExecutor(len(ROUTES)) as pool:  # 7 reads, well under the 20-request burst
                data = dict(zip(ROUTES, pool.map(get, ROUTES.values())))
            self._send(200, "application/json", json.dumps(data).encode())
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
    print(f"Dashboard on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
