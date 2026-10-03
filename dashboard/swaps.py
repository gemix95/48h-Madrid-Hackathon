"""Swaps for the dashboard's Negotiations > Swaps tab: who may swap a card we need for one of our spares, what
swaps are open or done, and the one write the dashboard makes with our key (post_swap).

Opportunities: for every card we lack (Values.wishlist) and every team that probably holds it (agent/ledger.py,
public evidence only), pick the spare of ours (Values.spares) that the holder is most likely to accept, then price
the swap for both sides at our private values (exact) and at the holder's values (an estimate).

The holder's values are a guess. Every team has the same six set multipliers shuffled (ours are in /api/me), and
agent/team_intel.py says which sets a team keeps and which it dumps, so we rank the sets by that lean and hand out
the multipliers in order. Ties share the average of the multipliers they span. Page bonuses and unseen duplicates
are not known, so the figure only separates "plausible" from "they would refuse".
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT / "team13"))
sys.path.append(str(ROOT / "agent"))
import ledger  # noqa: E402  (agent/ledger.py: likely holders of any card)
import team_intel  # noqa: E402  (agent/team_intel.py: which sets each team keeps or dumps)
import values  # noqa: E402
from values import Values  # noqa: E402  (team13/values.py: our private value model)

ACTIVE_TICKS = 120  # same as team13/wtb.py: a team with no public event this recent is treated as asleep
EXPIRES_TICKS = 120  # life of a swap we post, like the want-to-buy asks
MAX_ROWS = 40  # per list: swaps that leave both sides ahead, and swaps that only help us
MIN_THEIRS = 1.0  # P the holder must come out ahead, by our rough estimate, for a swap to count as good for both
AUTO_DELAY = 30  # seconds a swap that clears the auto bar counts down on the tab before it sends itself
AUTO_GAP = 30  # at most one automatic send every 30 s
AUTO_MAX_OPEN = 8  # open swaps of ours (any source) at which nothing sends itself
AUTO_COOLDOWN = 600  # seconds before the same team is asked for the same card again
OFFER_LIMIT = 30  # the game refuses a 31st open offer (too_many_offers)
LONGSHOT_GAP = 90  # long shots (swaps only good for us): at most one every 90 s
LONGSHOT_COOLDOWN = 1800  # the same card from the same team again only after 30 min (the offer lives ~1 h anyway)
LONGSHOT_FREE_SLOTS = 4  # never take the last open-offer slots: the agent needs them
TEAM = re.compile(r"^t\d+$")
_recent: dict = {}  # (team, card, asset) -> time of the last post, so a double click cannot post twice


def _team(x) -> bool:
    return bool(x) and bool(TEAM.match(x))


def _ref(t) -> str:
    """'card:LAV-09' (how the server shows a wanted card) -> 'LAV-09'."""
    return str(t).split(":")[-1]


def swap_offer(o: dict) -> bool:
    """A card for a card: we give or want cards on both sides (cash may ride along)."""
    g, w = o.get("give") or {}, o.get("want") or {}
    return bool(g.get("assets")) and bool(w.get("assets") or w.get("types") or w.get("cards"))


def wanted_refs(side: dict) -> list:
    return [_ref(t) for t in (side.get("types") or []) + (side.get("cards") or [])] + [a["ref"] for a in side.get("assets") or []]


def estimate_mults(lean: dict, set_ids: list, mults: list) -> dict:
    """Multiplier per set for one team: rank the sets by lean (a team keeps what it pays for), best gets the top
    multiplier. Sets with the same lean share the average of the multipliers they span."""
    order = sorted(set_ids, key=lambda s: -lean.get(s, 0))
    mults = sorted(mults, reverse=True)
    out, i = {}, 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and lean.get(order[j + 1], 0) == lean.get(order[i], 0):
            j += 1
        avg = sum(mults[i:j + 1]) / (j - i + 1)
        for s in order[i:j + 1]:
            out[s] = avg
        i = j + 1
    return out


def last_seen(events: list) -> dict:
    """{team: tick of its latest public action} (same rule as wtb.Asker._active)."""
    seen = {}
    for e in events:
        p, t = e.get("payload") or {}, e.get("type")
        o = p.get("offer") if isinstance(p.get("offer"), dict) else {}
        if t == "settlement":
            who = [x for x in p.get("parties") or [] if x != p.get("persona")]
        elif t == "thread.message":
            who = [p.get("sender")]
        elif t == "offer.listed":
            who = [o.get("maker")]
        elif t == "thread.opened":
            who = [p.get("team")]
        else:
            continue
        for team in who:
            if _team(team):
                seen[team] = max(seen.get(team, -1), e.get("tick", -1))
    return seen


def pick_venue(venues: list, me_id: str, leaderboard=None, to=None):
    """Cheapest open market that is not ours (self_venue forbids ours) and not the addressee's (the game refuses a direct
    offer to the owner of the market it is posted on, also with self_venue); the side that accepts pays its fee. Among
    the equally cheap, the one whose owner scores least: a deal on a team's market counts toward its market-making."""
    score = {t["team"]: t.get("score") or 0 for t in (leaderboard or {}).get("teams", [])}
    best = None
    for v in venues or []:
        if v.get("status", "open") != "open" or v.get("owner") in (me_id, to):
            continue
        if v.get("venue") != "rastro" and score.get(v.get("owner"), 0) > score.get(me_id, 0) - 6:
            continue  # a close rival's market: a trade there scores for them
        cost = (v.get("fee_bps", 500), v.get("fee_per_card", 1), score.get(v.get("owner"), 0))
        if best is None or cost < best[0]:
            best = (cost, v)
    return best[1] if best else None


def _load_no_rebuy():
    """Cards the agent sold to a dealer (team13/state.json "no_rebuy", every day): never asked back in a swap."""
    try:
        st = json.loads((ROOT / "team13" / "state.json").read_text())
        values.NO_REBUY = set()  # off, see team13/agent.py: buying a page completer back is the best trade
    except (OSError, ValueError):
        pass


def _listed(me: dict, offers: list) -> dict:
    """Our asset id -> ids of our open offers that already promise it."""
    out = {}
    for o in offers:
        if o.get("maker") == me.get("id"):
            for a in (o.get("give") or {}).get("assets") or []:
                out.setdefault(a["id"], []).append(o["id"])
    return out


def _their_values(v: Values, mults: dict, held: int, ref: str):
    """What one more copy of `ref` is worth to a team with these multipliers that holds `held` copies, and what
    giving up one of `held` copies costs it."""
    c = v.cards[ref]
    base = c["book"] * mults.get(c["set"], 1.0)
    return base * v.copy_factor(held), base * v.copy_factor(max(0, held - 1))


def opportunities(me: dict, catalog: dict, events: list, leaderboard, offers: list, min_gain: float, now_tick: int) -> list:
    """Swaps worth proposing, best first. Each row: the card we get and from whom (with the evidence), the spare we
    give, and the net value of the swap to us (exact) and to them (estimate)."""
    _load_no_rebuy()
    v, listed = Values(catalog, me), _listed(me, offers)
    hold, lean, seen = ledger.build(events, leaderboard), team_intel.lean(events), last_seen(events)
    asked = {(o.get("to"), ref) for o in offers if o.get("maker") == me.get("id") for ref in wanted_refs(o.get("want") or {})}
    set_ids = list(me.get("affinity") or {})
    mults = list((me.get("affinity") or {}).values())
    spares = v.spares()
    if not spares or not mults:
        return []
    est = {}
    rows = []
    for ref, gain in v.wishlist(limit=40):
        if gain < min_gain:
            continue
        for team, cards in hold.items():
            c = cards.get(ref)
            if team == me.get("id") or not c or c[0] <= 0 or now_tick - seen.get(team, -10 ** 9) > ACTIVE_TICKS or (team, ref) in asked:
                continue
            tm = est.setdefault(team, estimate_mults(lean.get(team, {}), set_ids, mults))
            _, their_loss = _their_values(v, tm, c[0], ref)
            best = None
            for a in spares:
                ours = gain - v.loss_of_removing([a["ref"]])
                if ours < min_gain or a["ref"] == ref:
                    continue
                their_gain, _ = _their_values(v, tm, hold.get(team, {}).get(a["ref"], [0])[0], a["ref"])
                theirs = their_gain - their_loss
                # the swap the holder is most likely to accept at the least cost to us: the smallest acceptable surplus;
                # none acceptable: the least bad. An asset that is not already for sale wins a tie.
                ok = theirs >= MIN_THEIRS
                key = (ok, -theirs if ok else theirs, ours, a["id"] not in listed)
                if best is None or key > best[0]:
                    best = (key, a, ours, theirs)
            if best:
                _, a, ours, theirs = best
                rows.append({"team": team, "want": ref, "gain": round(gain, 1), "evidence": c[2], "count": c[0], "asset": a["id"],
                             "give": a["ref"], "loss": round(gain - ours, 1), "ours": round(ours, 1), "theirs": round(theirs, 1),
                             "both": theirs >= MIN_THEIRS, "listed": listed.get(a["id"], []), "idle": now_tick - seen[team]})
    both = sorted((r for r in rows if r["both"]), key=lambda r: -r["ours"])
    only_us = sorted((r for r in rows if not r["both"]), key=lambda r: -r["theirs"])
    return both[:MAX_ROWS] + only_us[:MAX_ROWS]


def waiting(offers: list, me_id: str) -> int:
    """Our live card-for-card offers, each waiting for the other team's answer."""
    return sum(1 for o in offers if o.get("maker") == me_id and swap_offer(o) and o.get("status", "open") == "open")


def auto_bar(n_waiting: int, min_gain: float) -> tuple:
    """How good a swap must be to send itself, for us (exact) and for them (estimate). It rises with every swap of
    ours already waiting, so a run of borderline swaps cannot pile up: 0 waiting -> +3 / +2, 4 waiting -> +7 / +4."""
    return max(min_gain, 3) + n_waiting, 2 + n_waiting / 2


class Auto:
    """Swaps that are good for both and clear auto_bar send themselves AUTO_DELAY seconds after they show up, at
    most one every AUTO_GAP seconds. A row can be cancelled, and the whole thing switched off, from the tab."""

    def __init__(self, on: bool = True):
        self.on, self.armed, self.off, self.tried, self.why, self.log, self.last = on, {}, set(), {}, {}, [], -1e9
        self.bar = (None, None)
        self.last_longshot = -1e9
        self.longshot_ids = set()  # offers we sent as long shots: they do not raise the auto bar

    @staticmethod
    def key(r: dict) -> str:
        return f"{r['team']}|{r['want']}|{r['asset']}"

    def waiting_both(self, offers: list, me_id: str) -> int:
        return waiting([o for o in offers if o.get("id") not in self.longshot_ids], me_id)

    def check(self, r: dict, now: float, offers: list, me_id: str, min_gain: float):
        """Why this swap may not send itself right now, or None."""
        mine = [o for o in offers if o.get("maker") == me_id]
        bar = auto_bar(self.waiting_both(offers, me_id), min_gain)
        if not self.on:
            return "auto-send is off"
        if self.key(r) in self.off:
            return "auto-send cancelled"
        if r["ours"] < bar[0] or r["theirs"] < bar[1]:
            return f"below the auto bar (us ≥ {bar[0]:g} P, them ≥ {bar[1]:g} P)"
        gives = lambda o: [a["id"] if isinstance(a, dict) else a for a in (o.get("give") or {}).get("assets") or []]
        # a long shot never blocks a good swap: when both promise the same spare only one can settle
        if any(r["want"] in wanted_refs(o.get("want") or {}) for o in mine
               if o.get("id") not in self.longshot_ids or r["asset"] not in gives(o)):
            return "we already ask for this card"
        if any(r["asset"] in gives(o) for o in mine if swap_offer(o) and o.get("id") not in self.longshot_ids):
            return "that spare is already in a swap"
        if len(mine) >= OFFER_LIMIT:
            return f"the game's limit of {OFFER_LIMIT} open offers"
        if self.waiting_both(offers, me_id) >= AUTO_MAX_OPEN:
            return f"{AUTO_MAX_OPEN} swaps already waiting"
        if now - self.tried.get((r["team"], r["want"]), -1e9) < AUTO_COOLDOWN:
            return "asked recently"
        return None

    def plan(self, now: float, rows: list, offers: list, me_id: str, min_gain: float):
        """Arm the countdown of every swap that may send itself, drop the rest; return the one due now, if any."""
        self.bar = auto_bar(self.waiting_both(offers, me_id), min_gain)
        both = {self.key(r): r for r in rows if r.get("both")}
        why = {}
        for k, r in both.items():
            w = self.check(r, now, offers, me_id, min_gain)
            if w:
                why[k] = w
                self.armed.pop(k, None)
            else:
                self.armed.setdefault(k, now + AUTO_DELAY)
        for k in list(self.armed):
            if k not in both:
                del self.armed[k]  # gone: its countdown starts again if it comes back
        self.why = why
        if now - self.last < AUTO_GAP:
            return None
        due = sorted((at, k) for k, at in self.armed.items() if at <= now)
        return both[due[0][1]] if due else None

    def longshot(self, now: float, rows: list, offers: list, me_id: str, S: dict):
        """A swap only good for us (by our estimate the holder loses), sent anyway: a refusal costs nothing, and teams
        that listed the card for sale value it less than we guess. Several may promise the same spare: the game
        settles one, the others fail. Returns the row to send now, or None."""
        if not self.on or not S.get("longshot_swaps", 1) or now - self.last_longshot < LONGSHOT_GAP:
            return None
        mine = [o for o in offers if o.get("maker") == me_id]
        if len(mine) >= OFFER_LIMIT - LONGSHOT_FREE_SLOTS or waiting(offers, me_id) >= S.get("longshot_max_waiting", 16):
            return None
        asked = {(o.get("to"), ref) for o in mine for ref in wanted_refs(o.get("want") or {})}
        cand = [r for r in rows if not r.get("both") and r["ours"] >= S.get("longshot_min_us", 9)
                and (r["team"], r["want"]) not in asked and self.key(r) not in self.off
                and now - self.tried.get((r["team"], r["want"]), -1e9) >= LONGSHOT_COOLDOWN]
        cand.sort(key=lambda r: ("listed" in str(r.get("evidence")), r["ours"]), reverse=True)
        return cand[0] if cand else None

    def done(self, now: float, r: dict, res: dict) -> None:
        if not r.get("both"):
            self.last_longshot = now
            if res.get("offer"):
                self.longshot_ids.add(res["offer"])
        self.last = now
        self.tried[(r["team"], r["want"])] = now
        self.armed.pop(self.key(r), None)
        self.log.append({"ts": now, "ok": bool(res.get("ok")), "team": r["team"], "want": r["want"], "give": r["give"],
                         "offer": res.get("offer"), "error": res.get("error")})
        del self.log[:-20]

    def state(self, r: dict):
        """For the tab: when a good-for-both row sends itself (a real time, the 30 s gap included), or why it won't."""
        if not r.get("both"):
            return None
        k = self.key(r)
        if k in self.armed:
            return {"at": max(self.armed[k], self.last + AUTO_GAP)}
        return {"why": self.why.get(k, "")}

    def cancel(self, k: str) -> None:
        self.off.add(k)
        self.armed.pop(k, None)

    def set_on(self, on: bool) -> None:
        self.on = on
        if on:
            self.off.clear()  # switching it back on re-arms everything

    def snapshot(self) -> dict:
        return {"on": self.on, "bar": list(self.bar), "delay": AUTO_DELAY, "gap": AUTO_GAP, "log": self.log[-5:]}


def deals(me: dict, events: list, offers: list) -> dict:
    """Swaps in flight (our open card-for-card offers and the ones addressed to us) and swaps settled with us, plus
    how many card-for-card trades any team has settled so far."""
    me_id = me.get("id")
    open_ = [{"id": o["id"], "venue": o.get("venue"), "maker": o.get("maker"), "to": o.get("to"), "give": wanted_refs(o.get("give") or {}),
              "want": wanted_refs(o.get("want") or {}), "expires": o.get("expires_tick")}
             for o in offers if swap_offer(o) and (o.get("maker") == me_id or o.get("to") == me_id)]
    done, market = [], 0
    for e in events:
        if e.get("type") != "settlement":
            continue
        p = e.get("payload") or {}
        cards = [i for i in p.get("items") or [] if i.get("kind") == "card"]
        if len({i.get("frm") for i in cards}) < 2:
            continue
        market += 1
        if me_id in (p.get("parties") or []):
            done.append({"tick": e.get("tick"), "venue": p.get("venue"), "with": next((x for x in p["parties"] if x != me_id), None),
                         "gave": [i["ref"] for i in cards if i.get("frm") == me_id], "got": [i["ref"] for i in cards if i.get("to") == me_id]})
    return {"open": open_, "done": done[::-1][:30], "market_swaps": market, "waiting": waiting(offers, me_id)}


def view(me: dict, catalog: dict, events: list, leaderboard, offers: list, venues: list, min_gain: float, now_tick: int) -> dict:
    v, opps = pick_venue(venues, me.get("id"), leaderboard), opportunities(me, catalog, events, leaderboard, offers, min_gain, now_tick)
    for r in opps:  # the market a swap really goes to differs from the default only when the default is the addressee's own
        rv = pick_venue(venues, me.get("id"), leaderboard, r["team"])
        r["via"] = rv["name"] if rv and v and rv["venue"] != v["venue"] else None
    return {"opportunities": opps, "deals": deals(me, events, offers),
            "venue": v and {"id": v["venue"], "name": v.get("name"), "fee_bps": v.get("fee_bps", 0), "fee_per_card": v.get("fee_per_card", 0)},
            "min_gain": min_gain, "tick": now_tick}


def post_swap(post, now: float, me: dict, catalog: dict, offers: list, venues: list, min_gain: float,
              team: str, want: str, asset: int, leaderboard=None) -> dict:
    """Offer one of our spares to `team` for `want`, straight to the game with our key (`post(path, body)`).
    Re-checks everything on the server side; the page's numbers are never trusted."""
    v, listed = Values(catalog, me), _listed(me, offers)
    if not _team(team) or team == me.get("id"):
        return {"ok": False, "error": "that is not another team"}
    card = next((a for a in me.get("assets", []) if a.get("id") == asset and a.get("kind") == "card"), None)
    if not card:
        return {"ok": False, "error": "we no longer hold that card"}
    c = v.cards.get(want)
    if not c or not c["released"] or v.held[want]:
        return {"ok": False, "error": f"{want} is not a card we are missing"}
    net = v.gain_of_adding([want]) - v.loss_of_removing([card["ref"]])
    if net < min_gain:
        return {"ok": False, "error": f"giving {card['ref']} for {want} would leave us {net:.1f} P, under the {min_gain} P minimum"}
    dup = next((o["id"] for o in offers if o.get("maker") == me.get("id") and o.get("to") == team and asset in
                [a["id"] for a in (o.get("give") or {}).get("assets") or []] and want in wanted_refs(o.get("want") or {})), None)
    if dup or now - _recent.get((team, want, asset), -1e9) < 60:
        return {"ok": False, "error": f"already offered{f' (#{dup})' if dup else ' a moment ago'}"}
    venue = pick_venue(venues, me.get("id"), leaderboard, team)
    if not venue:
        return {"ok": False, "error": "no open market other than ours"}
    _recent[(team, want, asset)] = now
    res = post("/api/offers", {"give": {"assets": [asset]}, "want": {"cards": [want]}, "venue": venue["venue"], "to": team,
                               "expires_in_ticks": EXPIRES_TICKS})
    if res.get("_error"):
        _recent.pop((team, want, asset), None)
        try:  # the game answers {"error": <code>, "message": <why>}
            body = json.loads(res.get("_body") or "{}")
            why = f"{body['error']}: {body.get('message', '')}".strip(": ")
        except (ValueError, KeyError, TypeError):
            why = str(res.get("_body") or res["_error"])
        return {"ok": False, "error": f"the game refused it ({why})"}
    return {"ok": True, "offer": res.get("id"), "venue": venue["venue"], "venue_name": venue.get("name"), "give": card["ref"],
            "also_listed": listed.get(asset, [])}
