# 48h Madrid Hackathon · The Bazaar (Team 13)

- `team13/`: our agent (dealer haggler, team trader, duel bot, venue opener) and our Market Test broker.
- `dashboard/`: the live war room (timeline, next moves, scores, album, deals, market, rivals, agent decisions).
- `bazaar-kit/`: the official kit (SDK, starter agent, starter broker, rules).

The team key is in `bazaar.env` (this repo is private: keep it to Team 13).

## Run it

```bash
source bazaar.env
cd team13 && python3 agent.py              # the agent, all weekend (add --dry-run to log decisions without acting)
cd dashboard && python3 server.py          # the war room on http://localhost:8765
cd team13 && python3 smart_broker.py       # once we are level 2 and the agent has opened our venue
```

Run only ONE agent per team key: two agents would fight over the one accept per tick.

## How we play to win

Score = 30 negotiating + 30 market-making + 40 judges. Only value created counts, never activity or pack luck.

| Where points come from | What our code does |
|---|---|
| Dealer ladder (share of the price range we capture, best 3 deals per level) | `haggler.py`: Boulware concessions (open at ~45% of list, concede slowly, a new price every message), kind and varied words for Abuela, take a final offer only inside our cap, learn per dealer and item. In simulation it pays ~12% less than the starter. |
| Unlocking levels early (3 negotiated deals) | The haggler always negotiates: it never accepts the opening price. |
| Trades with teams, at our private values | `trader.py` + `values.py`: exact value model (matches the server's `your_value` on every card), takes board offers that gain us ≥ 3 P after fees, lists spares from book × 1.25 down to a floor that still gains us value, bids below value for our high-multiplier sets. Reads only the structured offer, never the words. |
| Duels | `duels.py`: never crosses our limit, anchors, converges in ~6 rounds because the pie decays, and in two-issue duels gives away the delivery days we care little about in exchange for price. |
| Market Test (up to 30 points) | `agent.py` opens a `board` venue as soon as we reach level 2 (cash is reserved for the bond). `smart_broker.py` estimates hidden limits and the clearing price and matches efficient pairs first, falling back to the stall's plan so it never does worse. `tests/sim_broker.py` compares it with the stall and a perfect-knowledge oracle. |
| Judges (40) | Every decision goes to `team13/logs/decisions.jsonl`, shown live in the dashboard's Agent panel. |

Tests: `python3 tests/sim_haggle.py`, `python3 tests/sim_broker.py` (from `team13/`).
