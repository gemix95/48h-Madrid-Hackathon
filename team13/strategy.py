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
    "enable_guard": (1, 0, 1, 1, "Modules", "Guard", "Last each tick: cancel any open offer of ours that loses value at our private values or breaks a team cap."),
    "enable_flipper": (1, 0, 1, 1, "Modules", "Flipper", "Buy a card a team sells below another team's bid and sell into that bid (profit after both fees)."),
    "enable_wtb": (1, 0, 1, 1, "Modules", "Want-to-buy asks", "Direct offers to teams that probably hold a card we need and do not collect its set."),
    "auto_flag_proven": (1, 0, 1, 1, "Modules", "Flag proven bad faith", "Flag a dealer message when its own structured offer proves the words false (price or card code). A correct flag scores, a wrong one costs."),
    "flag_bluffs": (0, 0, 1, 1, "Modules", "Also flag proven bluffs", "Flag a 'final' price the same dealer later beat in the same conversation. Off until we see how organisers score bluffs."),
    "flag_catalog": (0, 0, 1, 1, "Modules", "Also flag catalog contradictions", "Flag a stated print run that contradicts the catalog."),
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
    "wtb_price_share": (0.9, 0.5, 1.3, 0.05, "Trading", "Want-to-buy price (x book)",
                        "Never above our value minus the minimum gain, a team cap or free cash; never below 0.6 x book."),
    "wtb_swaps": (1, 0, 1, 1, "Trading", "Want-to-buy by swap when short of cash",
                  "Offer one of our spares from a set the holder collects, only if it still leaves us the minimum gain."),
    "wtb_max_open": (3, 0, 10, 1, "Trading", "Want-to-buy asks open at once", "One new ask per tick at most."),
    "wtb_ticks": (120, 10, 400, 10, "Trading", "Want-to-buy ask life (ticks)", "Long, so the holder's agent has time to see it."),
    "flip_min_gain": (4, 1, 30, 1, "Trading", "Minimum profit per flip (P)",
                      "A flip buys from one team and sells into another team's bid; it must clear this after both fees."),
    "flip_max_cash": (120, 0, 400, 10, "Trading", "Most cash one flip may use (P)",
                      "One flip at a time; never more than this, and never below the cash reserve."),
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
    "trade_seek_needed": (1, 0, 1, 1, "Trading", "Ask for cards we need",
                          "When a card that completes a page (or is worth 60+ P to us) is not listed anywhere, ask the team that "
                          "has it (seen in the public feed) with a structured offer that still leaves us most of its value."),
    "seek_keep_cash": (100, 0, 300, 10, "Trading", "Cash we always keep when asking for a page completer (P)",
                       "Completing a page is worth so much that it may use the market-bond reserve, but never below this."),
    "trade_haggles": (2, 0, 4, 1, "Trading", "Haggle with sellers (conversations at once)",
                      "Instead of paying a posted price, open a conversation with the seller (or buyer) and negotiate."),
    # duels
    "duel_rounds": (8, 2, 12, 1, "Duels", "Rounds to reach our limit",
                    "The pie shrinks every round (6-8%): fewer rounds = settle sooner."),
    "duel_anchor": (5.0, 0.2, 6.0, 0.1, "Duels", "Opening ambition",
                    "How far from our limit we open (× limit). Higher = greedier, riskier."),
    "duel_seller_cap": (2.2, 1.1, 3.0, 0.1, "Duels", "Seller's highest opening (x cost)",
                        "Lower closes sooner but captures less: simulator mean 0.528 at 2.2x vs 0.478 at 1.6x. Revisit with Duels I data."),
    "duel_accept": (0.5, 0.4, 1.0, 0.05, "Duels", "Accept threshold",
                    "Take the rival's offer when it gives us this share of what our next offer would."),
    # market
    "venue_fee_bps": (0, 0, 1000, 25, "Market", "Our market fee (bps)",
                      "0 = free (Saturday default). Fees never score and a positive fee blocks thin Market Test pairs "
                      "(ceil(bps*price/10000) on every match). El Duende / El Rastro Express are at 0%; Team 6 at 0.5%. "
                      "Safety: if a bench match is refused or fee-blocked, or a Market Test is upcoming, the agent forces 0%."),
    "day_budget": (120, 20, 400, 10, "Money", "Buying budget per game day (P)",
                   "Most we spend on dealer purchases, bids and posted offers per day (Friday, Saturday, Sunday each get "
                   "their own). Page completers are exempt. The ladder scores how well we buy, not how much."),
    "max_dealer_buy": (40, 10, 200, 5, "Money", "Most we pay for one dealer item (P)",
                       "Cheap items score the same on the ladder as expensive ones: skip 150 P packs."),
    "cash_floor": (40, 0, 200, 10, "Money", "Cash we always keep (P)",
                   "Never spent, even after our market bond is paid: room for a great trade or tomorrow's first deals."),
    "deals_per_dealer_day": (5, 1, 12, 1, "Money", "Most buys per dealer per day",
                             "Only our best 3 deals per level count each day; a couple more is enough to improve them."),
    "llm_effort": (0, 0, 2, 1, "AI", "Claude reasoning effort (0 low, 1 medium, 2 high)",
                   "How hard Claude thinks before each message. Low: ~4 s, ~$0.0075 a message, fits Sunday's 15 s ticks. "
                   "Medium/high: more careful wording, but slower (risk of missing a tick, then the template is used) and pricier."),
    "api_credit_usd": (0, 0, 1000, 1, "AI", "Claude API credit when you set this ($)",
                       "Copy your balance from console.anthropic.com > Billing. The dashboard subtracts what the agent spends "
                       "from here on (the API cannot report the balance with a user key). 0 = not set."),
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
