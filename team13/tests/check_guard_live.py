"""Live read-only check of guard.py: three bad offers must be cancelled, a pack bid kept, and a capped SAL-10 bid kept
only while SAL-10 is still worth more than the bid to us.

    source ../bazaar.env && python3 tests/check_guard_live.py     (reads /api/me and /api/catalog only)
"""
import json, os, sys, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from values import Values  # noqa: E402
from guard import Guard  # noqa: E402

URL, KEY = os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai"), os.environ["BAZAAR_KEY"]
get = lambda p: json.load(urllib.request.urlopen(urllib.request.Request(URL + p, headers={"X-Team-Key": KEY})))
me = get("/api/me")
page_card = next(a for a in me["assets"] if a["ref"] == "SAL-07")["id"]
fake = [{"id": 1, "maker": me["id"], "status": "open", "give": {"assets": [page_card]}, "want": {"cash": 46}},
        {"id": 2, "maker": me["id"], "status": "open", "give": {"cash": 80}, "want": {"types": ["card:SAL-10"]}},
        {"id": 3, "maker": me["id"], "status": "open", "give": {"cash": 10}, "want": {"cards": ["SAL-03"]}},
        {"id": 4, "maker": me["id"], "status": "open", "give": {"cash": 70}, "want": {"types": ["card:SAL-10"]}},
        {"id": 5, "maker": me["id"], "status": "open", "give": {"cash": 12}, "want": {"types": ["pack:sobre_barrio"]}}]
cancelled = []


class Ctx:
    pass


ctx = Ctx()
ctx.me, ctx.values, ctx.my_offers = me, Values(get("/api/catalog"), me), []
ctx.raw = type("Raw", (), {"my_offers": lambda self: {"offers": fake}})()
ctx.api = type("Api", (), {"cancel": lambda self, i: cancelled.append(i), "close_thread": lambda self, i: None})()
ctx.log = lambda *a, **k: print(" ", k.get("offer"), k.get("why"), "loss", k.get("loss"), "gain", k.get("gain"))
Guard(ctx).step()
# offer 4 (a 70 P bid for SAL-10, at the team cap) must be kept while SAL-10 is worth more than 70 P to us, and
# cancelled once we hold it (then a second copy is worth far less): follow the live album, not a fixed answer
expected = [1, 2, 3] + ([4] if ctx.values.gain_of_adding(["SAL-10"]) < 70 else [])
assert cancelled == expected, (cancelled, expected)
print("OK")
