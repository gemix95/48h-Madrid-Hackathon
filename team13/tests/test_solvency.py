"""solvency.py: our real cash lies inside the bounds rebuilt from the public feed; a bid above a maker's high bound
is refused, one inside it is allowed.

    source ../bazaar.env && python3 tests/test_solvency.py      (reads /api/me and data/feed.jsonl)
"""
import json, os, sys, urllib.request
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from solvency import cash_bounds, Solvency  # noqa: E402

feed = [json.loads(l) for l in open(os.path.join(HERE, "..", "..", "data", "feed.jsonl")) if l.strip()]
me = json.load(urllib.request.urlopen(urllib.request.Request(
    os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai") + "/api/me", headers={"X-Team-Key": os.environ["BAZAAR_KEY"]})))
lo, hi = cash_bounds(feed, me["id"])
print(f"{me['id']}: bounds [{lo}, {hi}], real cash {me['cash']}")
assert lo - 15 <= me["cash"] <= hi + Solvency.SLACK, "our own cash must sit in the corridor (snapshot may lag a little)"


class Ctx:
    pass


ctx = Ctx()
ctx.clock = {"tick": 1}
ctx.intel = type("I", (), {"events": {e["id"]: e for e in feed}})()
sol = Solvency(ctx)
b = sol.bounds("t12")
assert sol.cannot_pay("t12", b[1] + Solvency.SLACK + 1) and not sol.cannot_pay("t12", max(1, b[1]))
assert not sol.cannot_pay("abuela", 10 ** 6)   # dealers are not judged
print("t12 bounds", b, "-> OK")
