"""Epics, offline: nothing before round 3; then one team bid for the epic worth most (value - 50, within the team cap)
and a Pícaros haggle for the next one, within free cash; a held card cancels its bid; an unfilled bid waits a few
ticks before the card moves to the dealer (a fill in the same tick must not become a second copy); the dealer's
ask is taken inside the cap.

    python3 tests/test_epics.py
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import epics, strategy  # noqa: E402
from values import Values  # noqa: E402
from bazaar_sdk import BazaarError  # noqa: E402

epics.team_caps = lambda: {"SAL-11": 200}  # the tests fix the team cap; agent/caps.json may change
RAR = ["common"] * 5 + ["uncommon"] * 3 + ["rare"] * 2 + ["epic", "legendary"]
BOOK = {"common": 10, "uncommon": 25, "rare": 70, "epic": 180, "legendary": 450}


def catalog():
    sets = []
    for sid in ("LAV", "MAL", "SAL", "LAT"):
        cards = [{"id": f"{sid}-{i + 1:02d}", "rarity": r, "book": BOOK[r], "page": r in ("common", "uncommon", "rare"),
                  "hidden": False} for i, r in enumerate(RAR)]
        sets.append({"id": sid, "released": True, "cards": cards})
    return {"sets": sets, "values": {"copy_marginals": [1.0, 0.25, 0.1], "page_bonus": 0.25, "master_bonus": 0.1}}


class Api:
    def __init__(self):
        self.offers, self.cancelled, self.threads, self.said, self.accepted, self.next = [], [], {}, [], [], 100

    def list_offer(self, give, want, venue=None, to=None, expires_in_ticks=40):
        self.next += 1
        o = {"id": self.next, "maker": "t13", "give": give, "want": {"types": [f"card:{want['cards'][0]}"]},
             "venue": venue, "status": "open"}
        self.offers.append(o)
        return o

    def cancel(self, oid):
        self.cancelled.append(oid)
        self.offers = [o for o in self.offers if o["id"] != oid]

    def open_thread(self, who, topic=None):
        self.next += 1
        self.threads[self.next] = {"id": self.next, "with": who, "status": "open", "standing_offers": [], "topic": topic}
        return self.threads[self.next]

    def say(self, tid, text, price=None):
        self.said.append((tid, price))

    def thread(self, tid):
        return self.threads[tid]

    def accept(self, oid):
        self.accepted.append(oid)

    def close_thread(self, tid):
        self.threads[tid]["status"] = "closed"


class Ctx:
    def __init__(self, cash=475, rnd=3):
        self.S = {k: v[0] for k, v in strategy.KNOBS.items()}
        self.api = Api()
        self.me = {"id": "t13", "cash": cash, "unlocked": ["abuela", "chato", "pilar", "picaros", "banco"],
                   "affinity": {"LAV": 1.1, "MAL": 1.3, "SAL": 1.6, "LAT": 0.5}, "assets": []}
        self.values = Values(catalog(), self.me)
        self.clock = {"tick": 1500, "round": rnd}
        self.dealers = [{"id": "picaros", "status": "active", "level": 4,
                         "menu": {"sells": [{"rarity": "rare", "list_price": 63}, {"rarity": "epic", "list_price": 162}]}}]
        self.threads, self.state, self.logs = [], {}, []

    @property
    def my_offers(self):
        return self.api.offers

    def reserve(self):
        return 40

    def take_accept(self, kind="team"):
        return True

    def log(self, module, action, **kw):
        self.logs.append((action, kw))

    def give(self, ref):
        self.me["assets"].append({"id": 900 + len(self.me["assets"]), "kind": "card", "ref": ref})
        self.values.update(self.me)


def check(name, ok, detail=""):
    print(("ok  " if ok else "FAIL"), name, detail if not ok else "")
    return ok


r = []
# targets: SAL-11 288, MAL-11 234, LAV-11 198 (LAT-11 90 is below the floor)
t = epics.targets(Ctx().values, 150)
r.append(check("targets by value", [x[0] for x in t] == ["SAL-11", "MAL-11", "LAV-11"] and t[0][1] == 288.0, t))

c = Ctx(rnd=2)
epics.Epics(c).step()
r.append(check("idle before round 3", not c.api.offers and not c.api.threads))

c = Ctx()
m = epics.Epics(c)
m.step()
bid = c.api.offers[0] if c.api.offers else {}
r.append(check("team bid for SAL-11 at the team cap 200", bid.get("give") == {"cash": 200} and bid["want"]["types"] == ["card:SAL-11"], bid))
r.append(check("one bid only (MAL-11 value-50 = 184 < 190 goes to the dealer)", len(c.api.offers) == 1))
h = c.state["epics"]["haggle"]
r.append(check("Pícaros haggle for MAL-11, opening 119, cap 162", h and h["ref"] == "MAL-11" and h["price"] == 119 and h["cap"] == 162, h))

# the dealer answers 150 after a few rounds: taken inside the cap
for i in range(4):
    c.clock["tick"] += 1
    m.step()
th = c.api.threads[h["thread"]]
th["standing_offers"] = [{"id": 555, "maker": "picaros", "status": "open", "give": {"types": ["card:MAL-11"]}, "want": {"cash": 150}}]
c.clock["tick"] += 1
m.step()
r.append(check("dealer ask 150 accepted", 555 in c.api.accepted, c.api.accepted))
c.give("MAL-11")
c.clock["tick"] += 1
m.step()
r.append(check("haggle done once the card arrives", c.state["epics"]["haggle"] is None and ("bought_from_dealer", ) == (c.logs[-1][0],) or
               any(a == "bought_from_dealer" for a, _ in c.logs), [a for a, _ in c.logs][-4:]))

# free cash: 475 - 40 reserve - 200 bid = 235: LAV-11 cap = min(162, 193, 235) = 162
r.append(check("LAV-11 next at the dealer", (c.state["epics"]["haggle"] or {}).get("ref") in ("LAV-11", None)))

# the SAL-11 bid fills: card held -> the record goes, no new bid
c.api.offers = [o for o in c.api.offers if o["want"]["types"] != ["card:SAL-11"]]
c.give("SAL-11")
c.clock["tick"] += 1
m.step()
r.append(check("filled bid: no new SAL-11 bid", not any(o["want"]["types"] == ["card:SAL-11"] for o in c.api.offers)
               and "SAL-11" not in c.state["epics"]["bids"]))

# without a team cap the bid is value - 50: the most that still banks the full +50
epics.team_caps = lambda: {}
c = Ctx()
epics.Epics(c).step()
r.append(check("no cap: SAL-11 bid at 288 - 50 = 238", c.api.offers and c.api.offers[0]["give"] == {"cash": 238}, c.api.offers))
epics.team_caps = lambda: {"SAL-11": 200}

# an unfilled bid: cancelled after epics_team_ticks, then FILL_WAIT ticks before the dealer may start on it
c = Ctx()
c.give("MAL-11"); c.give("LAV-11")  # only SAL-11 left
m = epics.Epics(c)
m.step()
oid = c.api.offers[0]["id"]
c.clock["tick"] += c.S["epics_team_ticks"]
m.step()
r.append(check("unfilled bid cancelled", oid in c.api.cancelled))
c.clock["tick"] += 1
m.step()
r.append(check("no dealer haggle while the cancelled bid may still settle", c.state["epics"]["haggle"] is None))
c.clock["tick"] += epics.FILL_WAIT
m.step()
h = c.state["epics"]["haggle"]
r.append(check("then Pícaros for SAL-11", h and h["ref"] == "SAL-11" and h["cap"] == 162, h))

# short of cash: a team bid needs 190; with 180 free cash only the dealer, within the cash
c = Ctx(cash=220)
epics.Epics(c).step()
h = c.state["epics"]["haggle"]
r.append(check("short of cash: no team bid, dealer for SAL-11 within free cash", not c.api.offers and h and h["ref"] == "SAL-11"
               and h["cap"] <= 180, (c.api.offers, h)))

# a dealer busy with another of our agents: no thread
c = Ctx(cash=240)
c.threads = [{"kind": "persona", "with": "picaros", "status": "open"}]
epics.Epics(c).step()
r.append(check("busy dealer: wait", c.state["epics"]["haggle"] is None))

# a team's ask that pays is taken at once: SAL-11 at 245 on El Rastro costs 259 with the fee (+29 for us)
c = Ctx()
c.boards = {"rastro": [{"id": 20117, "give": {"assets": [{"id": 1063, "ref": "SAL-11"}]}, "want": {"cash": 245}}]}
c.venue_fee = lambda vid: (500, 1)
m = epics.Epics(c)
m.step()
r.append(check("ask at 245 (+29 with the fee) taken, no bid for SAL-11", 20117 in c.api.accepted
               and not any(o["want"]["types"] == ["card:SAL-11"] for o in c.api.offers), (c.api.accepted, c.api.offers)))
c.clock["tick"] += 1
m.step()
r.append(check("no dealer haggle for SAL-11 while the taken ask settles", (c.state["epics"]["haggle"] or {}).get("ref") != "SAL-11"))
c.give("SAL-11")
c.clock["tick"] += 1
m.step()
r.append(check("taken ask logged as bought", any(a == "bought_from_team" for a, _ in c.logs)))
# an ask that leaves less than 20 is left alone
c = Ctx()
c.boards = {"rastro": [{"id": 1, "give": {"assets": [{"id": 9, "ref": "SAL-11"}]}, "want": {"cash": 260}}]}
c.venue_fee = lambda vid: (500, 1)
epics.Epics(c).step()
r.append(check("ask at 260 (274 with fee, +14) not taken", 1 not in c.api.accepted))
# our bid is up and a paying ask appears: the bid goes first, the ask is taken after the wait
c = Ctx()
m = epics.Epics(c)
m.step()
bid_id = next(o["id"] for o in c.api.offers if o["want"]["types"] == ["card:SAL-11"])
c.boards = {"v07": [{"id": 77, "give": {"assets": [{"id": 5, "ref": "SAL-11"}]}, "want": {"cash": 230}}]}
c.venue_fee = lambda vid: (0, 0)
c.clock["tick"] += 1
m.step()
r.append(check("bid cancelled for a paying ask", bid_id in c.api.cancelled and 77 not in c.api.accepted))
c.clock["tick"] += epics.FILL_WAIT + 1
m.step()
r.append(check("then the ask is taken", 77 in c.api.accepted))

print("epics ok" if all(r) else "EPICS FAILED")
sys.exit(0 if all(r) else 1)
