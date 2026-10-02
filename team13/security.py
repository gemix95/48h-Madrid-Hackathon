"""Defences against prompt injection and manipulation by other teams' agents (and lying dealers).

Prompt injection between agents is allowed in this game, so we assume every counterparty may try it. The real
protection is structural (rules set the price band and every accept; we only act on structured offers valued at our
own private values; the guard cancels any of our offers that would lose value). This module closes the remaining
gaps around the AI negotiator:

  sanitize()     their text cannot break out of its <their_message> wrapper or smuggle control characters
  detect()       spots manipulation attempts (instruction overrides, fake system/role text, requests to reveal our
                 limits, fake offers written as JSON, pressure to accept "now")
  leaks()        refuses any message of ours that states a number other than our chosen price or their own prices
                 (Claude must never name a limit, a value or a budget, however it is asked)
A counterparty caught trying is marked untrusted for the day: no Claude with them (templates only) and double the
minimum gain on trades.
"""
from __future__ import annotations

import re

PATTERNS = {
    "override": r"\b(ignore|disregard|forget)\b.{0,40}\b(instruction|rule|prompt|previous|above|everything)",
    "role_play": r"\b(you are now|act as|pretend to be|new instructions|developer mode|jailbreak)\b",
    "fake_system": r"(^|\n|\s)(system|assistant|developer)\s*[:>\]]|<\s*/?\s*(system|assistant|their_message|instructions?)\b|\[\s*(system|inst)\s*\]",
    "reveal": r"\b(reveal|tell me|what is|what's|share|show)\b.{0,30}\b(your )?(limit|maximum|max|budget|reserve|value|cost|instructions|prompt|strategy)\b",
    "fake_offer": r"\{\s*\"?(give|want|price|accept)\"?\s*:",
    "pressure": r"\b(accept|pay|send)\b.{0,20}\b(now|immediately|right away)\b.{0,40}\b(or else|last chance|admin|organi[sz]er|referee)\b",
    "authority": r"\b(organi[sz]ers?|admin|referee|game master)\b.{0,40}\b(say|said|require|order|instruct)",
}
_COMPILED = {k: re.compile(v, re.I | re.S) for k, v in PATTERNS.items()}
# greedy on purpose: an attacker's own "</their_message>" inside the text must stay inside the sanitized part
_TAGGED = re.compile(r"<their_message>(.*)</their_message>", re.S)


def sanitize(text: str, limit: int = 600) -> str:
    """Their text as inert data: no angle brackets (no fake tags), no control characters, bounded length."""
    t = str(text or "")
    t = "".join(ch for ch in t if ch == "\n" or ch >= " ")
    t = t.replace("<", "‹").replace(">", "›")
    t = re.sub(r"\s+", " ", t).strip()
    return t[:limit]


def detect(text: str) -> list:
    t = str(text or "")
    return [k for k, rx in _COMPILED.items() if rx.search(t)]


def clean_situation(obj):
    """Walk a negotiation situation: sanitize every <their_message>…</their_message> and collect manipulation hits."""
    hits = []

    def walk(x):
        if isinstance(x, str):
            def repl(m):
                inner = m.group(1)
                hits.extend(detect(inner))
                return f"<their_message>{sanitize(inner)}</their_message>"
            return _TAGGED.sub(repl, x)
        if isinstance(x, list):
            return [walk(v) for v in x]
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        return x
    return walk(obj), sorted(set(hits))


def numbers_in(text: str) -> set:
    # whole numbers, also when followed by punctuation ("24." "24," "24!"), not parts of words or decimals
    return {int(n) for n in re.findall(r"(?<![\w.])(\d{1,6})(?!\d)(?!\.\d)(?![A-Za-z])", str(text or ""))}


def leaks(message: str, price, allowed=()) -> list:
    """Numbers our message would reveal beyond our chosen price and the other side's own prices (1-digit days and
    round counts like '2 sentences' excluded only when they match allowed)."""
    ok = {int(price)} if price is not None else set()
    ok |= {int(a) for a in allowed if a is not None}
    return sorted(n for n in numbers_in(message) if n not in ok and n > 10)
