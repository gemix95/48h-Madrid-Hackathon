"""Flag hunter: report dealer messages that the facts prove to be bad faith. A correct flag scores, a wrong one costs.

Friday's 912 dealer messages (Abuela, El Chato) had no provable lie: "some lie" is about the dealers still to come.
So only checks that the structure or the catalog can prove are flagged automatically; the rest are logged as
candidates until we see how a lying dealer behaves (Strategy tab `flag_bluffs` / `flag_catalog` switch them on).

Checks, on messages a dealer sent to us (`POST /api/flags` takes "a message sent to you"):
  price     the words name one price (``17 P``, ``17 primas``) and the structured offer carries another      auto
  item      the words name a card code (LAV-03) and the structured offer gives a different card          auto
  item_name the words name a card by name and the offer gives another (gifts get named too)       candidate
  item_topic we asked for one card and the offer gives another, the words naming none              candidate
  bluff     the dealer called a price final / last / its limit, then offered us a better one in the
            same conversation (proven by its own later structure)                                          candidate
  catalog   the words state a list price or print run that contradicts the dealer's menu or the catalog  candidate
"""
from __future__ import annotations

import re

from bazaar_sdk import BazaarError

PRICE = re.compile(r"(\d+)\s*(?:P\b|primas\b)", re.I)
FINAL = re.compile(r"\b(final (?:offer|price)|last (?:offer|price|word)|my last|lowest i go|won'?t go (?:lower|higher)|"
                   r"not (?:a prima|one) (?:less|more)|ni una (?:m[aá]s|menos)|ni una prima (?:m[aá]s|menos)|"
                   r"última oferta|mi última|precio final|mi palabra es firme)\b", re.I)
REF = re.compile(r"\b([A-Z]{3}-\d{2})\b")
PRINT_RUN = re.compile(r"only (\d+) (?:printed|exist|ever made|in the world)", re.I)
LIST_PRICE = re.compile(r"list(?:ed)? (?:price )?(?:is |at )?(\d+)", re.I)


class FlagHunter:
    def __init__(self, ctx):
        self.ctx = ctx

    def _cards(self):
        cat = getattr(self.ctx, "catalog", None) or {}
        cards = {c["id"]: c for s in cat.get("sets", []) for c in s.get("cards", [])}
        by_name = {c["name"].lower(): r for r, c in cards.items() if c.get("name")}
        return cards, by_name

    @staticmethod
    def _offer_price(o):
        return (o.get("want") or {}).get("cash") or (o.get("give") or {}).get("cash")

    @staticmethod
    def _offer_card(o):
        g = o.get("give") or {}
        t = [x.split(":", 1)[1] for x in g.get("types") or [] if x.startswith("card:")]
        t += [a.get("ref") for a in g.get("assets") or [] if isinstance(a, dict) and a.get("ref")]
        return t

    def findings(self, th: dict) -> list:
        """Every check on one of our dealer conversations: [(kind, message_id, reason, auto)]."""
        cards, by_name = self._cards()
        dealer = th.get("with")
        topic = ((th.get("topic") or {}).get("buy") or {}).get("card")
        out, finals = [], []
        msgs = [m for m in th.get("messages", []) if m.get("sender") == dealer]
        for m in msgs:
            text, o = m.get("text") or "", m.get("offer") or {}
            price, given = self._offer_price(o), self._offer_card(o)
            stated = {int(x) for x in PRICE.findall(text)}
            if price and len(stated) == 1 and abs(next(iter(stated)) - price) >= 2:
                out.append(("price", m["id"], f"Bad faith: the message says {next(iter(stated))} P but the structured "
                                              f"offer is {price} P.", True))
            refs = set(REF.findall(text))  # explicit card codes: provable
            names = {r for n, r in by_name.items() if len(n) > 6 and n in text.lower()}  # names: gifts get named too
            if given and refs and not refs & set(given):
                out.append(("item", m["id"], f"Bad faith: the message offers {', '.join(sorted(refs))} but the structured "
                                             f"offer gives {', '.join(given)}.", True))
            elif given and names and not names & set(given):
                out.append(("item_name", m["id"], f"The message names {', '.join(sorted(names))} and the structured offer "
                                                  f"gives {', '.join(given)}.", False))
            elif given and topic and topic not in given and not refs and not names:
                out.append(("item_topic", m["id"], f"We asked for {topic} and the structured offer gives {', '.join(given)}.", False))
            if price and FINAL.search(text):
                finals.append((m, price))
            for n in PRINT_RUN.findall(text):
                ref = (given or [topic] or [None])[0]
                if ref in cards and int(n) != cards[ref].get("print_run"):
                    out.append(("catalog", m["id"], f"Bad faith: the message says only {n} exist; the catalog print run "
                                                    f"of {ref} is {cards[ref]['print_run']}.", False))
        for m, p in finals:  # a "final" price beaten later by the same dealer in the same conversation
            later = [self._offer_price(x.get("offer") or {}) for x in msgs if x["id"] > m["id"]]
            later = [x for x in later if x]
            selling = bool(self._offer_card(m.get("offer") or {}))
            better = [x for x in later if (x < p if selling else x > p)]
            if better:
                out.append(("bluff", m["id"], f"Bad faith: called {p} P final, then offered {better[0]} P in the same "
                                              "conversation.", False))
        return out

    def step(self):
        ctx = self.ctx
        flagged = ctx.state.setdefault("flagged", [])  # shared with agent.maybe_flag: never flag one message twice
        allow = {"price": True, "item": True, "bluff": bool(ctx.S.get("flag_bluffs", 0)),
                 "catalog": bool(ctx.S.get("flag_catalog", 0)), "item_name": False, "item_topic": False}
        for th in ctx.threads:
            if th.get("kind") != "persona":
                continue
            for kind, mid, reason, _auto in self.findings(th):
                if mid in flagged:
                    continue
                flagged.append(mid)
                if not (allow[kind] and ctx.S.get("auto_flag_proven", 1)):
                    ctx.log("flag", "candidate", kind=kind, message=mid, thread=th["id"], dealer=th.get("with"), reason=reason)
                    continue
                try:
                    ctx.api.flag(mid, reason)
                    ctx.log("flag", "flagged", kind=kind, message=mid, thread=th["id"], dealer=th.get("with"), reason=reason)
                except BazaarError as e:
                    ctx.log("flag", "flag_refused", kind=kind, message=mid, error=str(e)[:160])
