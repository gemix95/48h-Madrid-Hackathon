"""flags.py: no flag on our real dealer threads (Friday dealers never lied), catches synthetic lies, ignores gifts.

    source ../bazaar.env && python3 tests/test_flags.py     (reads our threads and the catalog only)
"""
import json, os, sys, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from flags import FlagHunter  # noqa: E402

URL, KEY = os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai"), os.environ["BAZAAR_KEY"]
get = lambda p: json.load(urllib.request.urlopen(urllib.request.Request(URL + p, headers={"X-Team-Key": KEY})))


class Ctx:
    pass


ctx = Ctx()
ctx.catalog = get("/api/catalog")
threads = [t for t in get("/api/me/threads").get("threads", []) if t.get("kind") == "persona"]
H = FlagHunter(ctx)
auto = [(t["id"], f) for t in threads for f in H.findings(t) if f[3]]
print(f"real dealer threads: {len(threads)}, auto findings: {len(auto)}")
assert not auto, auto

sell = lambda ref, p, text, mid, final=False: {"id": mid, "sender": "vault", "text": text,
                                               "offer": {"give": {"types": [f"card:{ref}"]}, "want": {"cash": p}, "final": final}}
th = {"id": 1, "with": "vault", "kind": "persona", "topic": {"buy": {"card": "SAL-11"}}, "messages": [
    sell("SAL-11", 200, "SAL-11 for 180 P, a bargain.", 10),                                   # price lie
    sell("SAL-03", 180, "Here is SAL-11, as you asked: 180 P.", 11),                           # item lie (code)
    sell("SAL-11", 190, "Final offer: 190 P.", 12),                                            # bluff, proven below
    sell("SAL-11", 185, "Fine. 185 P.", 13),
    sell("SAL-11", 185, "185 P. And take this, a little present from me: Mercado de la Cebada.", 14),  # gift: no flag
]}
kinds = {(k, mid) for k, mid, _, _ in H.findings(th)}
print(sorted(kinds))
assert ("price", 10) in kinds and ("item", 11) in kinds and ("bluff", 12) in kinds
assert not any(mid == 14 and k in ("price", "item") for k, mid in kinds)
print("OK")
