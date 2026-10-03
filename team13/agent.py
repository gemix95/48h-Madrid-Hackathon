"""Team 13's agent: observe -> decide -> act, once per tick, for the whole weekend.

    source ../bazaar.env && python3 agent.py            # live
    source ../bazaar.env && python3 agent.py --dry-run  # decide and log, never write

Several teammates on the same key (the server cannot tell agents apart): give each a disjoint role and its own budget,
    AGENT_ROLE=dealers AGENT_BUDGET=60 python3 agent.py   # duels + haggler (+ flags)
    AGENT_ROLE=market  AGENT_BUDGET=60 python3 agent.py   # venue + trader + flipper + wtb + tapas
AGENT_ROLE is all (default), dealers, market, or a comma list of modules. With a split role, guard only cancels the
offers this process made (threads it spoke in, offers it listed).

Modules, in priority order each tick (one accept per team per tick is shared between them):
  duels    the tournament: never cross our limit, settle before the pie decays
  haggler  the dealer ladder: Boulware concessions, kind words, take finals inside our cap
  workshop  three duplicates of one rarity become the next rarity, when that card is worth more to us
  trader   team trades at our private values: take good board offers, list spares, bid for what we value most
  tapas    El Menú: 2-card dump platters, public trueque, private page-closer offers
  market   open our own market at 0% fees as soon as we reach level 2, run the smart broker, invite every team

Every decision is appended to logs/decisions.jsonl; the dashboard shows it live.

One agent per person (one team key, scopes that never overlap), all set by environment:
    AGENT_NAME=sergio            own state-<name>.json and agent-<name>.lock; every log line carries "agent": name
    AGENT_ROLE=haggler           modules (as above)
    AGENT_DEALERS=abuela,pilar   the only dealers this agent opens, talks to and trades with
    AGENT_BUDGET_2H=40           margin call: per 2 game hours an agent may spend this plus what its own sales brought in;
                                 when it runs out it buys nothing until the next window (selling always goes on)
    AGENT_SLOT=0 AGENT_SLOTS=3   the team's single accept per tick goes to the agent whose slot is tick % slots
    AGENT_KNOBS=enable_haggler=1,workshop_accumulate=0   this agent's own strategy knobs on top of strategy.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import urllib.request
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bazaar_sdk import Bazaar, BazaarError
from duels import Duels
from market import Market
from haggler import Haggler
from trader import Trader
from guard import Guard
from loans import LoanDesk
from matchmaker import Matchmaker
from workshop import Workshop
from flipper import Flipper
from wtb import Asker
from tapas import Tapas
from flags import FlagHunter
from solvency import Solvency
import values as values_mod
from values import Values
from intel import Intel
from learner import Learner
from negotiator import Negotiator
import council
import strategy
from reserved import reserved_ids

HERE = Path(__file__).parent
LOGS = HERE / "logs"
STATE = HERE / "state.json"  # default for AGENT_ROLE=all; split roles use state-{role}.json
VENUE_BOND = 270


def agent_knobs() -> dict:
    """AGENT_KNOBS ("knob=value,knob=value"): this agent's own strategy, on top of the shared strategy.json."""
    raw = {}
    for part in os.environ.get("AGENT_KNOBS", "").split(","):
        if "=" in part:
            k, v = (x.strip() for x in part.split("=", 1))
            try:
                raw[k] = float(v) if "." in v else int(v)
            except ValueError:
                raw[k] = v
    return strategy.clean(raw)


def load_strategy() -> dict:
    return {**strategy.load(), **agent_knobs()}


def role_label(role_str: str) -> str:
    name = os.environ.get("AGENT_NAME", "").strip().lower()
    if name:  # one agent per person: its own state file and lock
        return "".join(ch for ch in name if ch.isalnum() or ch in "-_") or "custom"
    r = (role_str or "all").strip().lower()
    return r if r in ("all", "dealers", "market") else "custom"


def state_path_for(role_str: str) -> Path:
    label = role_label(role_str)
    return STATE if label == "all" else HERE / f"state-{label}.json"


def seed_split_state(label: str, spath: Path) -> None:
    """First run of dealers+market on one laptop: copy the old monolithic state.json into each role file.
    A named agent (AGENT_NAME) starts from everything the team knew: state.json, then the market and dealers files."""
    if spath.exists() or label == "all":
        return
    if os.environ.get("AGENT_NAME", "").strip():
        merged = {}
        for f in (STATE, HERE / "state-market.json", HERE / "state-dealers.json"):
            try:
                merged.update(json.loads(f.read_text()))
            except (OSError, ValueError):
                pass
        if merged:
            spath.write_text(json.dumps(merged, indent=1, default=str))
        return
    if not STATE.exists():
        return
    try:
        old = json.loads(STATE.read_text())
    except (OSError, ValueError):
        return
    market_keys = ("venue", "broker_key", "venue_keys", "fee_set", "announce_tick", "announce_n", "cashback",
                   "club_rewards", "invites", "nudged", "invite_threads", "bench_eta", "venue_try_tick")
    if label == "market":
        seed = {k: old[k] for k in market_keys if k in old}
    else:
        seed = {k: v for k, v in old.items() if k not in market_keys}
    if seed:
        spath.write_text(json.dumps(seed, indent=1, default=str))


class DryApi:
    """Wraps the SDK: reads pass through, writes are logged and skipped."""
    WRITES = {"open_thread", "say", "accept", "list_offer", "cancel", "open_pack", "duel_say", "duel_accept",
              "open_venue", "close_thread", "flag", "set_fee", "close_venue", "taller"}

    def __init__(self, api, log):
        self._api, self._log = api, log

    def __getattr__(self, name):
        fn = getattr(self._api, name)
        if name not in self.WRITES:
            return fn

        def skipped(*a, **k):
            self._log("dry", name, args=[str(x) for x in a], kwargs={k2: str(v) for k2, v in k.items()})
            return {"id": -1, "cards": [], "broker_key": None}
        return skipped


class OwnedApi:
    """Wraps the API: remembers the threads this process speaks in and the offers it lists, so that with several
    agents on one key each one's guard only cancels what it made itself."""
    def __init__(self, api, ctx):
        self._api, self._ctx = api, ctx

    def __getattr__(self, name):
        return getattr(self._api, name)

    def _remember(self, kind, value):
        seen = self._ctx.state.setdefault("owned", {}).setdefault(kind, [])
        if value is not None and value != -1 and value not in seen:
            seen.append(value)
            del seen[:-400]

    def say(self, thread_id, *a, **k):
        self._remember("threads", int(thread_id))
        return self._api.say(thread_id, *a, **k)

    def list_offer(self, *a, **k):
        o = self._api.list_offer(*a, **k)
        self._remember("offers", (o or {}).get("id"))
        return o


class Context:
    def __init__(self, api, dry=False, state_path: Path | None = None):
        LOGS.mkdir(exist_ok=True)
        self._logf = open(LOGS / "decisions.jsonl", "a", buffering=1)
        self._state_path = state_path or STATE
        self.state = json.loads(self._state_path.read_text()) if self._state_path.exists() else {}
        self.agent_id = None
        self.name = os.environ.get("AGENT_NAME", "").strip().lower() or None
        self.dealer_scope = {d.strip() for d in os.environ.get("AGENT_DEALERS", "").split(",") if d.strip()} or None
        self.budget_2h = int(os.environ["AGENT_BUDGET_2H"]) if os.environ.get("AGENT_BUDGET_2H") else None
        self.slot = (int(os.environ.get("AGENT_SLOT", 0)), max(1, int(os.environ.get("AGENT_SLOTS", 1))))
        self.api = OwnedApi(DryApi(api, self.log) if dry else api, self)
        self.raw = api
        self.shared = False  # True when AGENT_ROLE splits the modules with teammates' agents
        self.clock, self.me, self.threads, self.my_offers, self.board, self.dealers = {}, {}, [], [], [], []
        self.leaderboard = []
        self.venues, self.boards = [], {}
        self.values = None
        self._accepts = {}
        self.S = load_strategy()
        self.intel = None
        self.learner = Learner()
        self.agent_budget = None  # AGENT_BUDGET: this process's daily spend cap when teammates run agents too
        self.announce = None  # set before Negotiator: its connect log calls Context.log
        self.llm = Negotiator(log=self.log)

    # ---------------------------------------------------------------- logging & state
    def log(self, module, action, **detail):
        rec = {"ts": round(time.time(), 1), "tick": self.clock.get("tick"), "module": module, "action": action, **detail}
        if self.name:
            rec["agent"] = self.name
        # browsers reject bare Infinity/NaN, so non-finite numbers (a protected card's loss) are logged as null
        rec = {k: None if isinstance(v, float) and not math.isfinite(v) else v for k, v in rec.items()}
        line = json.dumps(rec, default=str)
        self._logf.write(line + "\n")
        print(line[:300], flush=True)
        if getattr(self, "announce", None):  # the negotiator logs while __init__ runs, before announce is set
            try:
                self.announce(rec)
            except Exception:  # the board must never stop a tick
                pass

    def save(self):
        path = self._state_path
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, default=str))
        os.replace(tmp, path)

    # ---------------------------------------------------------------- budgets & rules
    def limit(self, name, default):
        return (self.clock.get("limits") or {}).get(name, default)

    def accepts_left(self, kind="team"):
        return self._accepts.get(kind, 0) > 0

    def my_accept_tick(self) -> bool:
        """With several agents on one key, the team's single accept per tick belongs to one of them in turn."""
        slot, slots = self.slot
        return slots == 1 or (self.clock.get("tick") or 0) % slots == slot

    def take_accept(self, kind="team"):
        if self._accepts.get(kind, 0) <= 0:
            return False
        if kind == "team" and not self.my_accept_tick():
            return False
        self._accepts[kind] -= 1
        return True

    def reserve(self):
        """Cash we keep back: the venue bond until our market is open (it is worth up to 30 points), then a floor."""
        floor = self.S.get("cash_floor", 40)
        v = self.me.get("venue") or {}
        open_ours = (v.get("status") == "open" and v.get("owner") == self.me.get("id")) or bool(self.state.get("venue"))
        return floor if open_ours else max(floor, self.S["reserve_cash"])

    # ---------------------------------------------------------------- daily money plan
    def day_key(self):
        return self.clock.get("today") or "day"

    def spent_today(self):
        return self.state.setdefault("spent", {}).get(self.day_key(), 0)

    def window_key(self):
        """The 2-game-hour window we are in (AGENT_BUDGET_2H counts per window)."""
        return f"{self.day_key()}:{int(float(self.clock.get('t_hours') or 0) // 2)}"

    def spent_window(self):
        return self.state.setdefault("spent_2h", {}).get(self.window_key(), 0)

    def earned_window(self):
        return self.state.setdefault("earned_2h", {}).get(self.window_key(), 0)

    def record_income(self, amount, what):
        """Cash this agent's own sales brought in: it widens this agent's 2-hour allowance (AGENT_BUDGET_2H)."""
        if amount and amount > 0:
            e = self.state.setdefault("earned_2h", {})
            e[self.window_key()] = e.get(self.window_key(), 0) + amount
            self.log("money", "earned", amount=amount, what=what, window=self.window_key(), earned=e[self.window_key()],
                     spent=self.spent_window())

    def budget_left(self):
        """What we may still spend today: the day budget minus what we spent, never below the cash we keep.
        With several agents on one key, AGENT_BUDGET caps this process's own daily spend (spent is per state.json)
        and AGENT_BUDGET_2H its spend per 2-hour window: once it is used up this agent buys nothing until the next."""
        day = self.S.get("day_budget", 120)
        if self.agent_budget is not None:
            day = min(day, self.agent_budget)
        left = min(day - self.spent_today(), self.me.get("cash", 0) - self.reserve())
        b2h = getattr(self, "budget_2h", None)
        if b2h is not None:  # margin call: the allowance plus this agent's own sales in the window, minus its buys
            allowance = b2h + self.earned_window() - self.spent_window()
            if allowance <= 0 and self.state.get("margin_call") != self.window_key():
                self.state["margin_call"] = self.window_key()
                self.log("money", "margin_call", window=self.window_key(), spent=self.spent_window(),
                         earned=self.earned_window(), allowance=b2h)
            left = min(left, allowance)
        return max(0, left)

    def record_spend(self, amount, what):
        if amount and amount > 0:
            sp = self.state.setdefault("spent", {})
            sp[self.day_key()] = sp.get(self.day_key(), 0) + amount
            w = self.state.setdefault("spent_2h", {})
            w[self.window_key()] = w.get(self.window_key(), 0) + amount
            self.log("money", "spent", amount=amount, what=what, today=sp[self.day_key()], budget=self.S.get("day_budget", 120))

    def owns(self, offer):
        """Whether this process made the offer (listed it, or spoke in its thread)."""
        own = self.state.get("owned", {})
        return offer.get("id") in own.get("offers", []) or (offer.get("thread") or -1) in own.get("threads", [])

    def locked_assets(self, reserved: bool = True):
        """Assets no module may give away: promised in an open offer or a dealer sale, and (reserved=True) the cards
        reserved for swap strategies (reserved.json). reserved=False only counts what is promised, for the Workshop's
        "do we still need fuel" maths, so reserved spares never trigger pack buying."""
        ids = set()
        for o in self.my_offers:
            for a in (o.get("give") or {}).get("assets") or []:
                ids.add(a["id"] if isinstance(a, dict) else a)
        for th in self.threads:
            topic = (th.get("topic") or {}).get("sell") or {}
            if th["status"] == "open":
                ids.update(topic.get("assets") or ([topic["asset"]] if topic.get("asset") else []))
        if reserved:
            ids |= reserved_ids((self.values.assets if self.values else None) or self.me.get("assets", []), self.state)
        return ids

    def speak(self, situation, band, fallback, effort="low"):
        """Message + price for a negotiation: Claude inside the safe band when enabled, else the rules.
        Claude only gets the time left in this tick's budget (ticks shrink to 15 s on Sunday): a late call is
        a missed tick and an expired offer, so past the budget the rules write the message."""
        left = getattr(self, "tick_deadline", time.time() + 8) - time.time()
        who = self.counterparty_id(situation)
        if who and self.is_untrusted(who):
            return fallback[0], fallback[1], "rules-untrusted"  # they tried to manipulate us today: templates only
        if self.S.get("llm_negotiator", 1) and self.llm.ready() and left >= 2.5:
            effort = ["low", "medium", "high"][int(self.S.get("llm_effort", 0))]  # Strategy tab > Advanced > AI
            msg, price, src = self.llm.propose(situation, band, fallback, effort=effort, timeout=min(8.0, left - 1.0))
            if src == "rules-injection" and who:
                self.mark_untrusted(who)
            return msg, price, src
        if left < 2.5:
            self.log("llm", "skipped_tick_budget", left_s=round(left, 1))
        return fallback[0], fallback[1], "rules"

    def catalog_loaded(self):
        return bool(getattr(self, "catalog", None)) and bool(self.intel and self.intel.rarity)

    def public_get(self, path):
        """Public reads go without the team key (their own 60/s limit), so the board scan never slows our bots."""
        with urllib.request.urlopen(self.raw.url + path, timeout=10) as r:
            return json.load(r)

    def read_markets(self):
        """Every open market and its order book: El Rastro, the starter stalls and every team's venue."""
        try:
            if self.clock.get("tick", 0) - self.state.get("venues_tick", -99) >= 3 or not self.venues:
                self.venues = [v for v in self.public_get("/api/venues").get("venues", []) if v.get("status") == "open"]
                self.state["venues_tick"] = self.clock.get("tick", 0)
        except Exception:
            pass
        boards = {}
        venues = [v for v in self.venues if v["venue"] != self.state.get("venue")]  # our own market: we may not trade there

        def read(v):
            try:
                return v["venue"], [{**o, "venue": o.get("venue") or v["venue"]}
                                    for o in self.public_get(f"/api/venues/{v['venue']}/offers").get("offers", [])]
            except Exception:
                return v["venue"], self.boards.get(v["venue"], [])
        with ThreadPoolExecutor(max_workers=8) as pool:  # one venue per team on Saturday: read the books in parallel
            boards.update(pool.map(read, venues))
        self.boards = boards

    def venue_fee(self, venue):
        v = next((x for x in self.venues if x["venue"] == venue), None)
        return (v.get("fee_bps", 500), v.get("fee_per_card", 1)) if v else (500, 1)

    def watch_levels(self):
        """Log every newly announced or activated level and dealer, once."""
        seen = self.state.setdefault("levels_seen", {})
        for l in (self.levels or []):
            key = str(l.get("id") or l.get("name"))
            st = l.get("status")
            if seen.get(key) != st:
                seen[key] = st
                self.log("levels", "level_" + str(st), level=key, name=l.get("name"), line=l.get("line"), how=l.get("how"))
        for d in self.dealers:
            key = "dealer:" + d["id"]
            if seen.get(key) != d.get("status"):
                seen[key] = d.get("status")
                self.log("levels", "dealer_" + str(d.get("status")), dealer=d["id"], name=d.get("name"), level=d.get("level"),
                         unlock=d.get("unlock"))

    def maybe_flag(self):
        """Words vs structure in OUR dealer conversations: flag only when switched on, each message once."""
        flagged = self.state.setdefault("flagged", [])
        mine = {t["id"] for t in self.threads}
        for s in (self.intel.summary().get("suspects", []) if self.intel else []):
            if s["thread"] not in mine or s["message"] in flagged:
                continue
            flagged.append(s["message"])
            if not self.S.get("auto_flag", 0):
                self.log("flag", "candidate", **s)
                continue
            try:
                self.api.flag(s["message"], f"Bad faith: the message says {s['stated']} P but the structured offer is {s['structured']} P.")
                self.log("flag", "flagged", **s)
            except BazaarError as e:
                self.log("flag", "flag_refused", error=str(e)[:160], **s)

    # ---------------------------------------------------------------- manipulation defence
    def counterparty_id(self, situation):
        import re as _re
        txt = str(situation.get("counterparty", ""))
        m = _re.search(r"\b(t\d+)\b", txt)
        if m:
            return m.group(1)
        for d in self.dealers:
            if d.get("name") and d["name"] in txt:
                return d["id"]
        return None

    def scan_manipulation(self):
        """Every tick: read what others wrote in our open conversations; mark anyone attempting manipulation."""
        import security
        seen = self.state.setdefault("scanned_msgs", [])
        for th in self.threads:
            if th.get("status") != "open":
                continue
            for m in th.get("messages", []):
                mid = m.get("id")
                if mid in seen or m.get("sender") == self.me.get("id"):
                    continue
                seen.append(mid)
                hits = security.detect(m.get("text") or "")
                if hits:
                    who = m.get("sender") or th.get("with")
                    self.log("security", "injection_detected", patterns=hits, who=who, thread=th["id"], text=(m.get("text") or "")[:160])
                    if who and (str(who)[:1] == "t" and str(who)[1:].isdigit()):
                        self.mark_untrusted(who)
        del seen[:-3000]

    def is_untrusted(self, who):
        return (self.state.get("untrusted", {}).get(who) or {}).get("day") == self.day_key()

    def mark_untrusted(self, who):
        u = self.state.setdefault("untrusted", {})
        if (u.get(who) or {}).get("day") != self.day_key():
            u[who] = {"day": self.day_key(), "tick": self.clock.get("tick")}
            self.log("security", "untrusted", who=who, rule="templates only and double minimum gain for the rest of the day")

    def open_new_packs(self):
        try:
            me = self.raw.me()
        except BazaarError:
            return
        for a in me.get("assets", []):
            if a.get("kind") == "pack":
                try:
                    res = self.api.open_pack(a["id"])
                    self.log("pack", "opened", pack=a["ref"], cards=[(c.get("ref"), c.get("rarity"), c.get("serial")) for c in res.get("cards", [])])
                except BazaarError as e:
                    self.log("pack", "open_refused", pack=a["id"], error=str(e))

    # ---------------------------------------------------------------- observe
    def observe(self, full=False):
        api = self.raw
        self.S = load_strategy()  # the dashboard's Strategy tab writes strategy.json
        self.me = api.me()
        if self.intel is None:
            self.intel = Intel(LOGS / "feed_events.jsonl", url=self.raw.url)
        if self.clock.get("tick", 0) - self.state.get("intel_tick", -99) >= 2:  # the whole public feed, every 2 ticks
            self.intel.refresh()
            self.state["intel_tick"] = self.clock.get("tick", 0)
            if self.catalog_loaded():
                summ = self.intel.summary()
                m = self.learner.fit(summ["dealer_threads"], self.me["id"], summ.get("now_tick"))
                if m.get("best_first") != self.state.get("learned_first"):
                    self.state["learned_first"] = m.get("best_first")
                    self.log("learn", "model", conversations=m["n"], deals=m["deals"], best_first=m.get("best_first"),
                             final_max=m.get("final_max_vs_opening"), ours=m.get("ours"), lessons=m.get("lessons"))
                if getattr(self, "shared", False) and m.get("lessons"):
                    sig = (m.get("n"), m.get("best_first"), m.get("deals"))
                    if self.state.get("council_learn_sig") != sig:
                        self.state["council_learn_sig"] = sig
                        council.post("haggler", "learn", " ".join(m["lessons"][:2]),
                                     {"n": m.get("n"), "best_first": m.get("best_first"), "deals": m.get("deals"),
                                      "capture": m.get("dealer_capture")},
                                     tick=self.clock.get("tick", 0))
        if getattr(self, "shared", False) and self.clock.get("tick", 0) % 8 == 0:
            ex = self.agent_id
            self.state["peer_claims"] = council.peer_claims(exclude_agent=ex)
            self.state["peer_lessons"] = council.peer_lessons(exclude_agent=ex, n=8)
        self.threads = api.my_threads().get("threads", [])
        self.my_offers = api.my_offers().get("offers", [])
        self.read_markets()
        self.board = self.boards.get("rastro", [])
        if full or not self.dealers:
            self.dealers = api.dealers().get("personas", [])
            self.catalog = api.catalog()
            self.levels = api.levels().get("levels", [])
            self.leaderboard = api.leaderboard().get("teams", [])
        if self.values is None or full:
            self.values = Values(self.catalog, self.me)
            self.intel.set_catalog(self.catalog)
        else:
            self.values.update(self.me)
        # Off: the organisers' scoring slide (Sat 20:25) makes buying back a page-completing card the best trade in the
        # game (+50); the real loss was selling below value, which the dealer sell floor now forbids.
        values_mod.NO_REBUY = set()
        self.state["ladder_deals"] = sum(1 for t in self.threads if t.get("kind") == "persona" and t["status"] == "deal")
        self._accepts = {"team": self.limit("accepts_per_team_per_tick", 1), "duel": 3}


def single_instance(label: str = "all"):
    """One agent process per role label on this machine (dealers + market may run together)."""
    import fcntl
    LOGS.mkdir(exist_ok=True)
    lock = LOGS / f"agent-{label}.lock"
    f = open(lock, "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.seek(0)
        raise SystemExit(f"Another agent-{label} is already running (pid {f.read().strip() or '?'}). Stop it: "
                         f"kill $(cat team13/logs/agent-{label}.lock)")
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f  # keep the file object alive for the life of the process


# Several teammates may run an agent on the same key from different machines. The server cannot tell them apart,
# so each agent owns a disjoint set of modules: two agents never haggle with the same dealer or hit the same offer.
ROLES = {
    "all": {"duels", "haggler", "venue", "trader", "flipper", "wtb", "tapas", "loans", "workshop", "matchmaker"},
    "dealers": {"duels", "haggler", "matchmaker"},   # workshop runs on the market agent so two processes never double-craft
    "market": {"venue", "trader", "flipper", "wtb", "tapas", "loans", "workshop"},
}


def parse_role(role: str) -> set:
    """A preset name (all, dealers, market) or a comma list of modules (e.g. "haggler,trader"). guard always runs."""
    role = (role or "all").strip().lower()
    if role in ROLES:
        return set(ROLES[role])
    mods = {m.strip() for m in role.split(",") if m.strip()}
    unknown = mods - ROLES["all"]
    if unknown or not mods:
        raise SystemExit(f"AGENT_ROLE: unknown {sorted(unknown) or role!r}; use {sorted(ROLES)} or modules {sorted(ROLES['all'])}")
    return mods


def main():
    # Laptop processes share the team key with the server agents. While this file exists they exit
    # before any trade. Remove it only to run a local agent on purpose.
    stop = Path(__file__).resolve().parent / "logs" / "STOP_LOCAL_AGENTS"
    if stop.exists():
        raise SystemExit(
            f"local agent stopped ({stop.name}); server runs bazaar-agent@emmanuele (chato, picaros) "
            "and bazaar-agent@sergio (abuela, pilar)"
        )
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-trade", action="store_true", help="skip team trading")
    ap.add_argument("--role", default=os.environ.get("AGENT_ROLE", "all"),
                    help="modules this agent runs: all | dealers | market | comma list (env AGENT_ROLE)")
    ap.add_argument("--budget", type=int, default=int(os.environ["AGENT_BUDGET"]) if os.environ.get("AGENT_BUDGET") else None,
                    help="most this agent spends per day, on top of day_budget (env AGENT_BUDGET)")
    args = ap.parse_args()
    role = parse_role(args.role)
    if args.no_trade:
        role -= {"trader", "flipper", "wtb", "loans"}
    label = role_label(args.role)
    spath = state_path_for(args.role)
    if not args.dry_run:
        seed_split_state(label, spath)
    _lock = None if args.dry_run else single_instance(label)  # noqa: F841 (held until exit)
    api = Bazaar(os.environ.get("BAZAAR_URL", "https://bazaar.causaprima.ai"), os.environ["BAZAAR_KEY"], wait_on_tick=False)
    ctx = Context(api, dry=args.dry_run, state_path=spath)
    ctx.agent_budget = args.budget
    ctx.shared = role != ROLES["all"]
    build = [("loans", LoanDesk),  # first: lock a collateral that just arrived before any module could list it
             ("duels", Duels), ("haggler", Haggler), ("venue", Market),
             ("workshop", Workshop),  # before the trader lists the duplicates we are about to burn
             ("trader", Trader),
             ("tapas", Tapas),  # El Menú: platters, public trueque, page-closer DMs (after trader so it sees the book)
             ("flipper", Flipper),  # buy below another team's bid, sell into it
             ("wtb", Asker),  # ask likely holders that do not collect a set for the cards we need
             ("matchmaker", Matchmaker)]  # brings a buyer and a seller of one card to our market, anonymously
    modules = [(name, cls(ctx)) for name, cls in build if name in role]
    modules.append(("guard", Guard(ctx)))  # last: undo anything this tick left open that loses value
    switch = {"duels": "enable_duels", "haggler": "enable_haggler", "trader": "enable_trader", "venue": "enable_venue",
              "guard": "enable_guard", "flipper": "enable_flipper",
              "wtb": "enable_wtb", "tapas": "enable_tapas", "loans": "enable_loans",
              "matchmaker": "enable_matchmaker", "workshop": "enable_workshop"}
    ctx.solvency = Solvency(ctx)  # public-feed cash bounds: skip offers whose maker cannot pay
    flagger = FlagHunter(ctx)  # proven bad faith in dealer messages to us: a correct flag scores
    # El Consejo: a unique id for this agent (fixed until it restarts), then announce every deal we make
    agent_id = None if args.dry_run else council.identify(args.role)
    ctx.agent_id = agent_id
    ctx.log("agent", "start", dry=args.dry_run, role=sorted(role), agent_budget=args.budget, agent_id=agent_id,
            name=ctx.name, dealers=sorted(ctx.dealer_scope) if ctx.dealer_scope else "all", budget_2h=ctx.budget_2h,
            knobs=agent_knobs(),
            accept_slot=list(ctx.slot),
            state_file=spath.name, lock=f"agent-{label}.lock")
    if agent_id:
        council.start_sync()
        council.post("agent", "joined", f"{agent_id} started on {council.HOST}: role {args.role} ({', '.join(sorted(role))})"
                     + (f", budget {args.budget} P/day" if args.budget else "")
                     + (f", {ctx.budget_2h} P per 2 h" if ctx.budget_2h is not None else "")
                     + (f", dealers {', '.join(sorted(ctx.dealer_scope))}" if ctx.dealer_scope else "")
                     + (f", owner {ctx.name}" if ctx.name else "") + ".")
        ctx.announce = lambda rec: council.announce(rec, {d.get("id"): (d.get("name") or d.get("id", "")).split()[0]
                                                          for d in ctx.dealers})
    flags = bool(role & {"duels", "haggler"})  # one flagger per team: the agent that talks to dealers
    if ctx.name:  # agents split by person: only the one running duels flags, so no message is flagged twice
        flags = "duels" in role
    last_tick, n = None, 0
    while True:
        try:
            ctx.clock = api.clock()
            if ctx.clock.get("paused") or ctx.clock.get("doors") != "open":
                if last_tick != "idle":
                    ctx.log("agent", "idle", paused=ctx.clock.get("paused"), doors=ctx.clock.get("doors"))
                    last_tick = "idle"
                time.sleep(5)
                continue
            if ctx.clock["tick"] == last_tick:
                time.sleep(max(0.2, min(5.0, float(ctx.clock.get("next_tick_in", 1)) + 0.3)))
                continue
            last_tick = ctx.clock["tick"]
            # budget for slow work (Claude messages) in this tick: 70% of the time left before the next tick
            ctx.tick_deadline = time.time() + 0.7 * float(ctx.clock.get("next_tick_in") or ctx.clock.get("tick_seconds") or 30)
            ctx.observe(full=(n % 20 == 0))
            n += 1
            for extra in (ctx.watch_levels, ctx.scan_manipulation) + ((ctx.maybe_flag, flagger.step) if flags else ()):
                try:
                    extra()
                except Exception as e:
                    ctx.log("agent", "crash", where=extra.__name__, error=repr(e))
            for name, mod in modules:
                if not ctx.S.get(switch[name], 1):
                    continue
                try:
                    mod.step()
                except BazaarError as e:
                    if e.code not in ("wait_for_tick", "rate_limited"):
                        ctx.log(name, "error", error=str(e))
                except Exception as e:
                    ctx.log(name, "crash", error=repr(e), trace=traceback.format_exc()[-800:])
            ctx.save()
        except BazaarError as e:
            ctx.log("agent", "api_error", error=str(e))
            time.sleep(3)
        except KeyboardInterrupt:
            ctx.save()
            sys.exit(0)
        except Exception as e:
            ctx.log("agent", "crash", error=repr(e), trace=traceback.format_exc()[-800:])
            time.sleep(3)


if __name__ == "__main__":
    main()
