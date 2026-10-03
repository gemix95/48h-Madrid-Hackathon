"""Loan desk: cash now against a card we would gladly own, bought back later with interest.

The game has no loans, only structured offers, so a loan is two ordinary trades:
  1. issue:  a direct offer to the borrower, our cash P for its card X. Cash and card move in one settlement, all or
             nothing, so we can never pay without receiving the collateral.
  2. repay:  once we hold X, a direct offer back to the borrower, X for R = P + interest, open for the loan's term.
             If the borrower accepts, it has its card back and we earned the interest. If the offer expires, we keep X.

Risk rules (all enforced here, a request that breaks one is rejected and logged):
  - X must be worth at least P x (1 + loan_safety) to us right now: a default leaves us a card worth more than the cash.
  - R >= P x (1 + loan_rate) and R >= P + 2.
  - one open loan per borrower, total principal out <= loan_cap, cash after the loan stays above the reserve.
  - never to an untrusted team; posted only on markets that do not score for a close rival (venues.safe_markets).
  - the guard never cancels a repayment offer (it would read "card worth more to us than R" as a loss).

Requests come from people, the agent stays the only writer with the team key: append a JSON line to
team13/logs/loan_requests.jsonl (agent/lend.py does it; logs/ survives deploys) and the agent picks it up next tick:
  {"team": "t07", "ref": "LAV-09", "principal": 60, "repay": 70, "term": 120}
"""
from __future__ import annotations

import json
import os

from bazaar_sdk import BazaarError
from venues import safe_markets

HERE = os.path.dirname(os.path.abspath(__file__))
REQUESTS = os.path.join(HERE, "logs", "loan_requests.jsonl")
ISSUE_TICKS = 60  # how long the borrower has to take the cash


def repay_offer_ids(state: dict) -> set:
    """Offer ids the guard must leave alone."""
    return {str(L.get("repay_offer")) for L in (state.get("loans") or {}).values() if L.get("repay_offer")}


class LoanDesk:
    def __init__(self, ctx):
        self.ctx = ctx

    # ------------------------------------------------------------------ requests
    def _read_requests(self):
        if not os.path.exists(REQUESTS):
            return []
        done = self.ctx.state.setdefault("loan_requests_seen", 0)
        with open(REQUESTS) as f:
            lines = [line for line in f if line.strip()]
        self.ctx.state["loan_requests_seen"] = len(lines)
        out = []
        for line in lines[done:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                self.ctx.log("loan", "bad_request", line=line[:200])
        return out

    def check(self, req: dict) -> str | None:
        """Why this request breaks a risk rule, or None if it is safe."""
        ctx, S, v = self.ctx, self.ctx.S, self.ctx.values
        team, ref = req.get("team"), req.get("ref")
        try:
            P, R, term = int(req["principal"]), int(req["repay"]), int(req.get("term", 120))
        except (KeyError, TypeError, ValueError):
            return "principal, repay (and term) must be whole primas / ticks"
        if not team or not (team[:1] == "t" and team[1:].isdigit()) or team == ctx.me.get("id"):
            return "borrower must be another team id like t07"
        if ref not in v.cards:
            return f"unknown card {ref}"
        if P < 1 or term < 10:
            return "principal >= 1 and term >= 10 ticks"
        worth = v.gain_of_adding([ref])
        if worth < P * (1 + S.get("loan_safety", 0.10)):
            return f"{ref} is worth {worth:.1f} to us: below principal {P} + {int(S.get('loan_safety', 0.10) * 100)}% safety"
        if R < max(P + 2, P * (1 + S.get("loan_rate", 0.10))):
            return f"repay {R} below principal + {int(S.get('loan_rate', 0.10) * 100)}% (and at least +2)"
        loans = ctx.state.get("loans") or {}
        if any(L["team"] == team and L["status"] in ("offered", "active") for L in loans.values()):
            return f"{team} already has an open loan"
        out = sum(L["principal"] for L in loans.values() if L["status"] in ("offered", "active"))
        if out + P > S.get("loan_cap", 60):
            return f"total principal out would be {out + P} > cap {S.get('loan_cap', 60)}"
        if ctx.me.get("cash", 0) - P < ctx.reserve():
            return f"cash {ctx.me.get('cash', 0)} - {P} would go below the reserve {ctx.reserve()}"
        if hasattr(ctx, "is_untrusted") and ctx.is_untrusted(team):
            return f"{team} is untrusted today"
        return None

    # ------------------------------------------------------------------ step
    def step(self):
        ctx = self.ctx
        if ctx.values is None:
            return
        loans = ctx.state.setdefault("loans", {})
        tick = ctx.clock.get("tick", 0)
        for req in self._read_requests():
            why = self.check(req)
            if why:
                ctx.log("loan", "rejected", request=req, why=why)
                continue
            venue = safe_markets(ctx, req["team"])[0]
            try:
                o = ctx.api.list_offer({"cash": int(req["principal"])}, {"cards": [req["ref"]]}, venue=venue,
                                       to=req["team"], expires_in_ticks=ISSUE_TICKS)
            except BazaarError as e:
                ctx.log("loan", "issue_refused", request=req, error=str(e)[:160])
                continue
            lid = str(o.get("id"))
            loans[lid] = {"team": req["team"], "ref": req["ref"], "principal": int(req["principal"]), "repay": int(req["repay"]),
                          "term": int(req.get("term", 120)), "venue": venue, "status": "offered", "issued_tick": tick,
                          "held_before": ctx.values.held[req["ref"]]}
            ctx.log("loan", "offered", loan=lid, **{k: loans[lid][k] for k in ("team", "ref", "principal", "repay", "term", "venue")})
        mine = {str(o.get("id")) for o in ctx.my_offers}
        for lid, L in loans.items():
            if L["status"] == "offered" and lid not in mine and tick > L["issued_tick"]:
                if ctx.values.held[L["ref"]] > L["held_before"]:  # the borrower took the cash: we hold the collateral
                    self._post_repay(lid, L, tick)
                    continue
                gone = L.setdefault("gone_tick", tick)  # an accepted offer settles a tick later: wait before giving up
                if tick - gone > 3:
                    L["status"] = "not_taken"
                    ctx.log("loan", "not_taken", loan=lid, team=L["team"], ref=L["ref"])
            elif L["status"] == "collateral_held":  # posting the repayment offer failed last tick: retry
                self._post_repay(lid, L, tick)
            elif L["status"] == "active":
                if not any(a["id"] == L.get("asset") for a in ctx.values.assets):
                    L["status"] = "repaid"
                    ctx.log("loan", "repaid", loan=lid, team=L["team"], ref=L["ref"], interest=L["repay"] - L["principal"])
                elif L.get("repay_offer") not in mine and tick - L.setdefault("repay_gone_tick", tick) > 3:
                    L["status"] = "defaulted"  # expired: we keep a card worth more to us than the cash we lent
                    ctx.log("loan", "defaulted_kept_collateral", loan=lid, team=L["team"], ref=L["ref"], principal=L["principal"])

    def _post_repay(self, lid, L, tick):
        ctx = self.ctx
        copies = [a for a in ctx.values.assets if a["ref"] == L["ref"]]
        asset = max(copies, key=lambda a: a["id"])  # the copy that just arrived has the newest id
        try:
            o = ctx.api.list_offer({"assets": [asset["id"]]}, {"cash": L["repay"]}, venue=L["venue"], to=L["team"],
                                   expires_in_ticks=L["term"])
            L.update(status="active", asset=asset["id"], repay_offer=str(o.get("id")), active_tick=tick)
            ctx.log("loan", "active", loan=lid, team=L["team"], ref=L["ref"], repay=L["repay"], until=tick + L["term"])
        except BazaarError as e:
            L["status"], L["tries"] = "collateral_held", L.get("tries", 0) + 1
            L["venue"] = "rastro"  # the next try goes to the house market, which takes any team
            ctx.log("loan", "repay_offer_refused", loan=lid, tries=L["tries"], error=str(e)[:160])
