# Runbook · Saturday 3 Oct and Sunday 4 Oct

One page for the team: who runs what, the rules the bots follow on their own, and what to do at each event.

## 1. Who runs what

**Everything runs on Emmanuele's laptop. Exactly one process writes with our team key: `team13/agent.py`.**
Two writers fight over the one accept per tick, repeat each other's prices to dealers and sell the same card twice
(it happened on Friday). Nobody runs bots, collectors or watchers from another laptop.

| Process | Command (from the repo root, after `source bazaar.env`) | Writes with the key? |
|---|---|---|
| Agent: duels, dealers, our market + broker, team trades, guard | `cd team13 && ../.venv/bin/python agent.py` | **yes, the only writer** |
| Dashboard (war room, Strategy tab) | `cd dashboard && DASHBOARD_PASSWORD=... python3 server.py` | only through `strategy.json` |
| Tunnel for the second pair of eyes | `cloudflared tunnel --url http://localhost:8765` | no |
| Watcher, optional (alerts in a terminal) | `python3 agent/watch.py 30` | no |

The agent also keeps the public feed (`team13/logs/feed_events.jsonl`, every 2 ticks) and loads the tick-0 snapshot
in `data/feed.jsonl`, so `agent/collect.py` is not needed. Retired: `agent/abuela_bot.py` (the haggler does this),
`agent/close_sal09.py` (one-off), `agent/guard.py` (now the `guard` module; a fallback only if the agent is down).

Code changes: commit and push from anywhere, then the Host runs `git pull` and restarts the agent (state survives in
`team13/state.json`). Strategy changes need no restart: the Strategy tab writes `team13/strategy.json`, read every tick.

## 2. Start of day (Emmanuele, 08:45)

```bash
cd ~/.../48h-Madrid-Hackathon && git pull
source bazaar.env
cd team13 && ../.venv/bin/python tests/check_guard_live.py     # must print OK
../.venv/bin/python agent.py --dry-run                          # one or two ticks, read the log, then Ctrl-C
../.venv/bin/python agent.py                                    # live, all day
cd ../dashboard && DASHBOARD_PASSWORD=... python3 server.py     # new terminal
cloudflared tunnel --url http://localhost:8765                  # new terminal; send the link to Anton
```

Before 09:00: `pgrep -fl agent.py` shows exactly one agent; the dashboard shows tick, cash and our open offers.

## 2b. Two pairs of eyes (from when Anton arrives)

Emmanuele drives, Anton reviews. Both watch the same dashboard (Anton through the tunnel link, with the password).

| | Emmanuele (driver) | Anton (reviewer, with Claude) |
|---|---|---|
| Watches | agent log, Market panel during Market Tests, duels as they run | score and rank, every deal against our values, rivals, levels |
| Changes | Strategy tab knobs, restarts after `git pull` | code via git (pushes, then asks for a restart), `agent/caps.json` via git |
| Never | trades by hand in a thread the agent runs | runs any process with the team key |

Rules for the pair: say a change out loud before making it; one person changes one knob at a time; after any change,
both look at the next two ticks of the log. If the two disagree on a trade, the rules (caps, min gain) win until
they agree on a new rule.

## 3. Rules the bots follow without asking

Ticks are 30 s on Saturday and 15 s on Sunday, and offers in dealer and team threads **expire after 2 ticks**
(60 s Saturday, 30 s Sunday). There is no time to ask a human, so decisions live in rules:

| Rule | Where | Now |
|---|---|---|
| Never pay above a card's team cap | `agent/caps.json` | `SAL-10: 70` |
| Keep cash for the market bond | Strategy tab `reserve_cash` / `seek_keep_cash` | 270 P until our venue is open |
| A trade must gain us at least | Strategy tab `trade_min_gain` | 3 P after fees |
| Never give away a card of a nearly complete page cheaply | `values.py` page option | automatic |
| Cancel anything that loses value or breaks a cap | `guard` module, last each tick | automatic |
| Claude writes words only inside the tick budget | `agent.py` `tick_deadline` | 70% of the tick, 8 s max per call |
| Our market fee | Strategy tab `venue_fee_bps` | 0 (fees never score, and they block thin matches) |

To change a rule, change it there, not by hand in a thread. Emergency stop for one module: set its `enable_*` to 0 on
the Strategy tab (applied next tick). Stop everything: Ctrl-C the agent; open offers stay until they expire.

Prefer **direct offers on the board** (`to: tXX`, long `expires_in_ticks`) over offers in threads for anything that
needs the other team to think: thread offers die after 2 ticks, board offers stay. The side that accepts pays the
venue fee, so post our price and let them accept.

## 4. Saturday timeline (Madrid time; one game hour = one wall hour on Saturday)

| Time | Event | Ticks | What we do |
|---|---|---|---|
| 09:00 | Round 2 starts (weight 1, Friday was 0.5), **El Retiro** released, everyone gets a pack + 150 P | 30 s | Agent opens the pack. RET is a 0.7 set for us: sell RET cards to teams that collect it. Big value trades count fully from now. |
| 09:00+ | **El Chato** (level 2) may activate | | Watcher prints `LEVEL`. The haggler opens with him at once (head start). Warm words, small steps, never the same price twice. |
| as soon as level 2 | Open our venue (0%, `board`) | | `market.py` does it if cash ≥ 270. Check the dashboard Market panel. |
| 10:00 and every 2 h (12, 14, 16, 18, 20) | **Market Test**, 16 ticks = 8 min | 30 s | Broker must act every tick. Afterwards: Intel compares our efficiency with the stall's from the feed. |
| ~11:30 | **Duels I**: price only, one round-robin (~32 duels each), 16 ticks, 6% decay | 30 s | `duels.py`. Operator watches the first two: limit never crossed, deals in ≤ 6 rounds. |
| ~18:00 | **Duels II**: price + delivery days, 2 rounds, 8% decay | 30 s | Every priced message needs `days`. Trade days we care little about for price. |
| 20:00 | **Hard Market Test**: firmer, more impatient traders | 30 s | Match intramarginal pairs early; impatient traders leave. |
| 23:00 | Close. Round 3 (Sunday, Chamberí, 15 s ticks) at 09:00 | | Host: `git pull`, restart, check the latency log (`llm skipped_tick_budget` lines). |

## 5. Friday lessons that shaped these rules

- Abuela's offers and team-thread offers expire after 2 ticks: a human reply came too late twice.
- Score: team-trade gains are scored against the leader. Our first +30 gave +15 points; later gains gave much less.
- Three code paths tried to sell Salamanca page cards (SAL-07, SAL-08) for a few primas each, which would have cost
  the ~100 P page bonus. Fixed in `values.py`; the guard catches the next one.
- A one-off script accepted the other side's offer, so we paid the 5 P fee. Post our price instead.
- `agent/team_intel.py` read our own preferences right (likes SAL, MAL; dislikes LAT) without seeing our values:
  the same read on other teams tells us whom to trade with.
