"""Intel from the public game feed: every team's dealer haggling, every settlement, gifts and market listings.

The feed (GET /api/feed, no key needed) shows other teams' offers and the prices they settled at. We keep our own
deduplicated copy of it on disk and learn from it:
  - per dealer and item class (a pack, a card of a rarity, selling a card of a rarity): opening asks, the prices
    teams actually got, the lowest, the median, final offers;
  - the "beginner" price: a team's first deal with a dealer happens at a fixed welcome price, not negotiated
    (on Friday Abuela sold the first pack/uncommon at 17 and the first common at 7 to everyone);
  - the market: who lists what at which price, and what trades settle at;
  - how many offers each dealer takes before naming its final, overall and per team (rounds()).
The haggler uses this to stop overpaying (never above what others typically get) and to close early when the dealer's
ask already matches the best price anyone has got; the dashboard shows it in the Intel tab.
"""
from __future__ import annotations

import json
import re
import statistics
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

URL = "https://bazaar.causaprima.ai"


class Intel:
    def __init__(self, store: Path, url: str = URL, catalog: dict | None = None):
        self.store, self.url = Path(store), url
        self.events: dict = {}
        self.rarity = {}
        self._summary, self._at = None, 0
        self.list_price = {"common": 10, "uncommon": 25}
        if catalog:
            self.set_catalog(catalog)
        # data/feed.jsonl: the team's snapshot from tick 0 (the server only serves the latest 500 events)
        seed = Path(__file__).resolve().parent.parent / "data" / "feed.jsonl"
        for path in (seed, self.store):
            if not path.exists():
                continue
            for line in path.read_text().splitlines():
                try:
                    e = json.loads(line)
                    self.events[e["id"]] = e
                except (ValueError, KeyError):
                    pass
        self._summary, self._at = None, 0

    def set_catalog(self, catalog: dict):
        rarity = {c["id"]: c["rarity"] for s in catalog["sets"] for c in s["cards"]}
        if rarity != self.rarity:
            self.rarity, self._summary = rarity, None

    # ------------------------------------------------------------------ collect
    def refresh(self) -> int:
        """Fetch the latest public events and append the new ones to our store. Returns how many were new."""
        try:
            with urllib.request.urlopen(f"{self.url}/api/feed?limit=5000", timeout=15) as r:
                evs = json.load(r).get("events", [])
        except Exception:
            return 0
        new = [e for e in evs if e.get("id") not in self.events]
        if new:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            with self.store.open("a") as f:
                for e in sorted(new, key=lambda e: e["id"]):
                    self.events[e["id"]] = e
                    f.write(json.dumps(e) + "\n")
            self._summary = None
        return len(new)

    # ------------------------------------------------------------------ analyse
    def item_class(self, topic: dict, items=None) -> str:
        side = "sell" if "sell" in (topic or {}) else "buy"
        t = (topic or {}).get(side) or {}
        if t.get("pack"):
            return f"buy:pack:{t['pack']}"
        ref = t.get("card")
        rar = t.get("rarity") or self.rarity.get(ref or "")
        if not rar and side == "sell" and items:
            rar = self.rarity.get(items[0].get("ref", ""))
        if not rar and items:
            rar = items[0].get("rarity") or self.rarity.get(items[0].get("ref", ""))
        return f"{side}:card:{rar or 'unknown'}"

    def summary(self) -> dict:
        if self._summary is not None and time.time() - self._at < 5:
            return self._summary
        evs = sorted(self.events.values(), key=lambda e: e["id"])
        threads: dict = {}
        settlements, gifts, listings, market_trades, suspects = [], [], [], [], []
        closed = set()
        for e in evs:
            p, t = e.get("payload") or {}, e.get("type")
            if t == "thread.opened" and p.get("kind") == "persona":
                threads[p["thread"]] = {"id": p["thread"], "team": p["team"], "dealer": p["with"], "topic": p.get("topic"),
                                        "opened": e["tick"], "asks": [], "offers": [], "final": None, "final_k": None, "deal": None}
            elif t == "thread.message" and p.get("thread") in threads:
                th, o = threads[p["thread"]], p.get("offer") or {}
                price = (o.get("give") or {}).get("cash") or (o.get("want") or {}).get("cash")
                if p.get("sender") == th["dealer"] and price and p.get("text"):
                    stated = {int(x) for x in re.findall(r"(\d+)\s*(?:P|primas)\b", p["text"])}
                    if len(stated) == 1 and abs(next(iter(stated)) - price) >= 2:  # words say one price, structure another
                        suspects.append({"tick": e["tick"], "message": p.get("message"), "thread": p["thread"], "dealer": th["dealer"],
                                         "team": th["team"], "stated": next(iter(stated)), "structured": price, "text": p["text"][:160]})
                if price:
                    (th["asks"] if p.get("sender") == th["dealer"] else th["offers"]).append((e["tick"], price))
                    if o.get("final") and p.get("sender") == th["dealer"]:
                        th["final"] = price
                        if th["final_k"] is None:
                            th["final_k"] = len(th["offers"])  # the team's offers before her final
            elif t == "settlement":
                if p.get("persona"):
                    settlements.append({**p, "tick": e["tick"]})
                else:
                    market_trades.append({**p, "tick": e["tick"]})
            elif t == "thread.closed":
                closed.add(p.get("thread"))
            elif t == "gift.given":
                gifts.append({**p, "tick": e["tick"]})
            elif t == "offer.listed":
                o = p.get("offer") or {}
                listings.append({"tick": e["tick"], "id": o.get("id"), "maker": o.get("maker"), "venue": p.get("venue"), "give": o.get("give"), "want": o.get("want")})

        # classify each dealer settlement from its own items (who gave what to whom), then link the conversation
        first_deal = {}
        for s_ in settlements:
            dealer = s_["persona"]
            team = next((x for x in s_["parties"] if x != dealer), None)
            items = s_.get("items") or [{}]
            it = items[0]
            side = "buy" if it.get("to") == team else "sell"
            if it.get("kind") == "pack":
                cls = f"buy:pack:{it.get('ref')}"
            else:
                cls = f"{side}:card:{self.rarity.get(it.get('ref', ''), 'unknown')}"
            cands = [th for th in threads.values() if th["team"] == team and th["dealer"] == dealer and th["deal"] is None
                     and th["opened"] <= s_["tick"] and self.item_class(th["topic"], items) == cls]
            th = max(cands, key=lambda th: th["opened"]) if cands else None
            beginner = (team, dealer) not in first_deal
            first_deal.setdefault((team, dealer), s_["tick"])
            rec = {"team": team, "dealer": dealer, "price": s_.get("price"), "tick": s_["tick"], "beginner": beginner,
                   "items": [i.get("ref") for i in items], "thread": th["id"] if th else None, "cls": cls}
            if th:
                th["deal"] = rec
                rec["opening"] = th["asks"][0][1] if th["asks"] else None
                rec["rounds"] = len(th["offers"])
            s_["rec"] = rec

        by_cls: dict = defaultdict(lambda: {"deals": [], "beginner": [], "openings": [], "finals": [], "walked": 0})
        for th in threads.values():
            cls = self.item_class(th["topic"])
            k = f"{th['dealer']}|{cls}"
            if th["asks"] and not (th["deal"] and th["deal"]["beginner"]):
                by_cls[k]["openings"].append(th["asks"][0][1])
            if th["final"]:
                by_cls[k]["finals"].append(th["final"])
        for s in settlements:
            r = s["rec"]
            k = f"{r['dealer']}|{r['cls']}"
            (by_cls[k]["beginner"] if r["beginner"] else by_cls[k]["deals"]).append(r)

        classes = {}
        for k, v in by_cls.items():
            dealer, cls = k.split("|", 1)
            prices = [r["price"] for r in v["deals"] if r["price"]]
            sell = cls.startswith("sell")
            classes[k] = {
                "dealer": dealer, "cls": cls, "n": len(prices),
                "best": (max(prices) if sell else min(prices)) if prices else None,
                "median": statistics.median(prices) if prices else None,
                "worst": (min(prices) if sell else max(prices)) if prices else None,
                "opening": statistics.median(v["openings"]) if v["openings"] else None,
                "finals": v["finals"], "beginner": sorted({r["price"] for r in v["beginner"] if r["price"]}),
                "prices": [(r["tick"], r["price"], r["team"]) for r in v["deals"]],
                "beginner_deals": [(r["tick"], r["price"], r["team"]) for r in v["beginner"]],
            }

        teams: dict = defaultdict(lambda: {"deals": 0, "negotiated": 0, "spent": 0, "gifts": 0, "listings": 0, "conversations": 0})
        for th in threads.values():
            teams[th["team"]]["conversations"] += 1
        for s in settlements:
            r = s["rec"]
            teams[r["team"]]["deals"] += 1
            teams[r["team"]]["negotiated"] += 0 if r["beginner"] else 1
            if r["cls"].startswith("buy"):
                teams[r["team"]]["spent"] += r["price"] or 0
        for g in gifts:
            teams[g["team"]]["gifts"] += 1
        for l in listings:
            if l["maker"]:
                teams[l["maker"]]["listings"] += 1

        # rounds: the team's offers until the dealer named a final (her patience); a deal without a final ended
        # earlier by choice, so it only counts for a dealer with fewer than three finals
        finals, ended = defaultdict(lambda: defaultdict(list)), defaultdict(lambda: defaultdict(list))
        for th in threads.values():
            if th["deal"] and th["deal"]["beginner"]:
                continue
            k = th["final_k"] if th["final_k"] is not None else (len(th["offers"]) if th["deal"] else None)
            if k:
                ended[th["dealer"]][th["team"]].append(k)
                if th["final_k"] is not None:
                    finals[th["dealer"]][th["team"]].append(k)
        rounds = {}
        for dealer in ended:
            src = finals[dealer] if sum(map(len, finals[dealer].values())) >= 3 else ended[dealer]
            ks = [k for v in src.values() for k in v]
            rounds[dealer] = {"mean": round(statistics.mean(ks), 1), "n": len(ks), "finals": src is finals[dealer],
                              "teams": {t: {"mean": round(statistics.mean(v), 1), "n": len(v)} for t, v in src.items() if v}}

        offer_maker = {l["id"]: l["maker"] for l in listings if l.get("id") and l.get("maker")}
        dealer_threads = []
        for th in threads.values():
            dealer_threads.append({"id": th["id"], "team": th["team"], "dealer": th["dealer"], "cls": self.item_class(th["topic"]),
                                   "opened": th["opened"], "asks": th["asks"], "offers": th["offers"], "final": th["final"],
                                   "deal": th["deal"]["price"] if th["deal"] else None,
                                   "last": max([x[0] for x in th["asks"] + th["offers"]] or [th["opened"]]),
                                   "beginner": bool(th["deal"] and th["deal"]["beginner"]),
                                   "closed": th["id"] in closed})
        self._summary = {"now_tick": max((e["tick"] for e in evs), default=0), "dealer_threads": dealer_threads, "suspects": suspects[-40:], "offer_maker": offer_maker, "classes": classes, "teams": dict(teams), "rounds": rounds, "gifts": gifts[-30:], "listings": listings[-80:],
                         "market_trades": market_trades[-50:], "events": len(self.events),
                         "dealer_deals": [s["rec"] for s in settlements][-120:]}
        self._at = time.time()
        return self._summary

    # ------------------------------------------------------------------ advice for the agent
    def advice(self, dealer: str, cls: str) -> dict:
        """What the market tells us about this dealer and item class (negotiated deals only)."""
        c = self.summary()["classes"].get(f"{dealer}|{cls}")
        if not c or c["n"] < 2:
            return {}
        return {"best": c["best"], "median": c["median"], "opening": c["opening"], "n": c["n"]}

    def rounds(self, dealer: str, team: str | None = None) -> dict:
        """{"mean", "n"}: how many offers a team makes before this dealer names its final, from every team's
        conversations (or one team's). Empty when nobody has finished a conversation with it yet."""
        r = self.summary()["rounds"].get(dealer) or {}
        return (r.get("teams") or {}).get(team, {}) if team else r

    def has_beginner_price(self, dealer: str) -> bool:
        return any(c["beginner"] for k, c in self.summary()["classes"].items() if c["dealer"] == dealer)
