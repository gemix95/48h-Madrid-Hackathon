"""Reconstruct every team's negotiation with a dealer from logs/feed.jsonl: price sequences and outcomes."""
import collections
import json
import sys

dealer = sys.argv[1] if len(sys.argv) > 1 else "abuela"
th = collections.defaultdict(list)
deals = {}
for line in open("logs/feed.jsonl"):
    e = json.loads(line)
    p = e["payload"]
    if e["type"] == "settlement" and p.get("persona") == dealer:
        deals[tuple(sorted(x for x in p["parties"] if x != dealer))[0]] = deals.get(p["parties"][0], 0)
    if e["type"] != "thread.message" or p.get("with") != dealer:
        continue
    o = p.get("offer") or {}
    g, w = o.get("give") or {}, o.get("want") or {}
    buy = bool(g.get("types") or g.get("assets")) if p["sender"] == dealer else bool(w.get("types") or w.get("assets"))
    price = (w.get("cash") or g.get("cash")) if o else None
    th[(p["team"], p["thread"])].append(("A" if p["sender"] == dealer else "T", price, "F" if o.get("final") else "",
                                         (p.get("text") or "")[:60], buy))
for (team, tid), v in sorted(th.items(), key=lambda kv: kv[0][1]):
    item = "buy" if any(x[4] for x in v if x[0] == "A") else "sell"
    print(f"{team} #{tid} {item}: " + " ".join(f"{s}{pr}{f}" for s, pr, f, _, _ in v))
