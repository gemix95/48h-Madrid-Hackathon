"""Our strategy knobs, in one place. The dashboard's Strategy tab edits team13/strategy.json; the agent reloads it
every tick, so a change takes effect within one tick without a restart.

Each knob: (default, min, max, step, group, label, help). Presets override a few knobs at once.
"""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).with_name("strategy.json")

KNOBS = {
    # modules on/off
    "enable_haggler": (1, 0, 1, 1, "Modules", "Dealer haggling", "Negotiate with dealers (ladder points, unlocks the next level)."),
    "enable_trader": (1, 0, 1, 1, "Modules", "Team trading", "Trade with other teams on El Rastro at our private values."),
    "enable_duels": (1, 0, 1, 1, "Modules", "Duels", "Play the duel tournament automatically."),
    "enable_venue": (1, 0, 1, 1, "Modules", "Open our market", "Open our own market as soon as we reach level 2."),
    "llm_negotiator": (1, 0, 1, 1, "Modules", "AI negotiator (Claude Opus 5.5)",
                       "Claude writes each message and picks the price inside the safe band the rules allow; "
                       "falls back to templates if the API is slow or unavailable."),
    "auto_flag": (0, 0, 1, 1, "Modules", "Flag dealers that lie",
                  "Flag a dealer message in our conversations whose stated price contradicts its structured offer. "
                  "A correct flag scores, a wrong one costs: off by default, candidates are shown on the Intel tab."),
    "learn_conversations": (1, 0, 1, 1, "Modules", "Learn from every conversation",
                            "Study every dealer conversation (ours and every rival's): open at the first offer that works best, "
                            "explore around it with a bandit over our own results, take finals up to the learned threshold, "
                            "and give Claude the lessons."),
    "use_intel": (1, 0, 1, 1, "Modules", "Learn from other teams",
                  "Read every team's deals in the public feed: never pay above what others typically get, close at once "
                  "when the dealer's ask matches the best price anyone got, spend a new dealer's fixed first-deal price on our best item."),
    # dealers
    "haggle_open": (0.25, 0.15, 0.9, 0.05, "Dealers", "First offer (share of list price)",
                    "Lower = more of the dealer's range captured, but more rounds."),
    "haggle_cap": (1.0, 0.6, 1.2, 0.05, "Dealers", "Most we pay (share of list price)",
                   "We never accept above this, final offers included."),
    "haggle_rounds": (18, 4, 30, 1, "Dealers", "Rounds to reach our cap",
                      "More rounds = slower concessions. Abuela is patient (85%)."),
    "haggle_curve": (2.2, 1.0, 4.0, 0.1, "Dealers", "Concession curve",
                     "1 = steady steps; higher = tiny steps first, bigger near the cap (Boulware)."),
    "haggle_buy_packs": (1, 0, 1, 1, "Dealers", "Buy packs", "Packs: 3 per hour from Abuela."),
    "haggle_sell_spares": (1, 0, 1, 1, "Dealers", "Sell spares to dealers", "Sell duplicates/low-value cards to dealers."),
    "haggle_buy_cards": (1, 0, 1, 1, "Dealers", "Buy single cards", "Buy cards we value most (Salamanca, Malasaña)."),
    # trading
    "trade_min_gain": (3, 0, 20, 1, "Trading", "Minimum gain per trade (P)",
                       "A trade must create at least this much value for us, at our values, after fees."),
    "trade_bid_share": (0.4, 0, 1, 0.05, "Trading", "Share of free cash for bids",
                        "The rest stays free for dealer deals."),
    "trade_max_asks": (8, 0, 30, 1, "Trading", "Spares listed at once", ""),
    "trade_max_bids": (6, 0, 30, 1, "Trading", "Bids open at once", ""),
    "trade_ask_start": (1.25, 0.8, 2.0, 0.05, "Trading", "Spare asking price (× book)",
                        "Starts here, then drops toward a floor that still gains us value."),
    "trade_bid_start": (0.6, 0.3, 1.0, 0.05, "Trading", "Opening bid (× book)",
                        "Starts here, then rises toward what the card is worth to us minus our minimum gain."),
    "trade_reprice_ticks": (8, 2, 40, 1, "Trading", "Ticks between price changes", ""),
    "trade_all_markets": (1, 0, 1, 1, "Trading", "Trade on every market",
                          "Scan every market (El Rastro, starter stalls, team venues), value offers after each market's fee, "
                          "and spread our listings over the busiest, cheapest ones."),
    "trade_haggles": (2, 0, 4, 1, "Trading", "Haggle with sellers (conversations at once)",
                      "Instead of paying a posted price, open a conversation with the seller (or buyer) and negotiate."),
    # duels
    "duel_rounds": (8, 2, 12, 1, "Duels", "Rounds to reach our limit",
                    "The pie shrinks every round (6-8%): fewer rounds = settle sooner."),
    "duel_anchor": (2.0, 0.2, 3.0, 0.1, "Duels", "Opening ambition",
                    "How far from our limit we open (× limit). Higher = greedier, riskier."),
    "duel_accept": (0.6, 0.4, 1.0, 0.05, "Duels", "Accept threshold",
                    "Take the rival's offer when it gives us this share of what our next offer would."),
    # market
    "venue_fee_bps": (0, 0, 1000, 25, "Market", "Our market fee (bps)",
                      "0 = free. The fee rounds UP per trade (ceil(bps x price / 10000)), so even 1% costs 1 P on every match, and a broker may only "
                      "match when price + fee <= bid: any fee kills thin-margin Market Test pairs. Fees earned never score; free attracts trades, which do."),
    "reserve_cash": (270, 0, 400, 10, "Market", "Cash kept for the market bond (P)",
                     "The bond is 250 + 20. Set 0 to spend everything on deals."),
}

# "Tournament winner" = the defaults: best robust settings in tests/tournament.py (see tests/RESULTS.md)
PRESETS = {
    "Tournament winner": {},
    "Previous defaults": {"haggle_open": 0.45, "haggle_rounds": 12, "duel_rounds": 6, "duel_anchor": 0.6, "duel_accept": 0.9},
    "Fast closer": {"haggle_open": 0.6, "haggle_rounds": 7, "haggle_curve": 1.5, "trade_min_gain": 2,
                    "trade_ask_start": 1.1, "trade_bid_start": 0.75, "duel_rounds": 4, "duel_accept": 0.8},
    "Market first": {"reserve_cash": 270, "trade_bid_share": 0.2, "haggle_buy_cards": 0, "venue_fee_bps": 0},
}

# Simulated scores per preset (tests/tournament.py, see tests/RESULTS.md)
RESULTS = {'Tournament winner': {'dealers': 0.81, 'dealers_worst': 0.76, 'duels': 0.6, 'duels_worst': 0.54}, 'Previous defaults': {'dealers': 0.72, 'dealers_worst': 0.64, 'duels': 0.45, 'duels_worst': 0.25}, 'Fast closer': {'dealers': 0.57, 'dealers_worst': 0.46, 'duels': 0.53, 'duels_worst': 0.34}, 'Market first': {'dealers': 0.81, 'dealers_worst': 0.76, 'duels': 0.6, 'duels_worst': 0.54}}

_cache = {"mtime": None, "data": {}}


def defaults() -> dict:
    return {k: v[0] for k, v in KNOBS.items()}


def clean(raw: dict) -> dict:
    """Keep known knobs only, clamp each to its range."""
    out = {}
    for k, v in (raw or {}).items():
        if k not in KNOBS:
            continue
        d, lo, hi, step, *_ = KNOBS[k]
        try:
            x = float(v)
        except (TypeError, ValueError):
            continue
        x = min(hi, max(lo, x))
        out[k] = int(round(x)) if isinstance(d, int) and float(step).is_integer() else round(x, 4)
    return out


def load() -> dict:
    """Defaults overlaid with strategy.json (re-read only when the file changes)."""
    try:
        m = PATH.stat().st_mtime
    except FileNotFoundError:
        return defaults()
    if m != _cache["mtime"]:
        try:
            _cache["data"] = clean(json.loads(PATH.read_text()))
            _cache["mtime"] = m
        except (ValueError, OSError):
            pass
    return {**defaults(), **_cache["data"]}


def save(values: dict) -> dict:
    data = clean(values)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    tmp.replace(PATH)
    return {**defaults(), **data}


def describe() -> dict:
    return {"knobs": {k: {"default": v[0], "min": v[1], "max": v[2], "step": v[3], "group": v[4], "label": v[5], "help": v[6]}
                      for k, v in KNOBS.items()},
            "presets": PRESETS, "results": RESULTS, "current": load()}
