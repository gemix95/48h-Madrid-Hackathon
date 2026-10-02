# Runbook · Saturday 3 Oct and Sunday 4 Oct

One page for the team: who runs what, the rules the bots follow on their own, and what to do at each event.

## 0. Saturday plan in one screen

Cash never scores by itself; value created does. Cash is ammunition: the 270 P market bond (refundable) and a war
chest for a Salamanca epic or legendary if a higher-level dealer ("vault") appears. Earn it by flipping, not hoarding.

1. **09:00 open our market** (0% fee, `board`) as soon as the 150 P allowance lands; t12 already runs a 0% market,
   so we win on the broker, not the fee. First Market Test 10:00.
2. **Flip** (`team13/flipper.py`): buy a card a team sells below another team's bid, sell into that bid. It only
   flips cards worth less to us than the bid (it keeps the rest), pays both fees and still needs `flip_min_gain`.
   Friday prices between teams: commons 6 to 40 (median 9), uncommons 12 to 40 (22), rares 53 to 80 (70).
   Collectors pay up: t15 paid 18 per La Latina common, t04 bids 85 for LAV-10, t17 bids 78 for MAL-09.
3. **Malasaña page** (6/10): cheap pieces first (MAL-03, MAL-07), rares only inside caps. t17 and t08 compete for
   MAL-09, so its price will rise.
4. **War chest**: keep about 400 P after the bond for SAL-11 (epic, worth 288 to us) and SAL-12 (legendary, 720).
   Caps in `agent/caps.json`: SAL-11 200, SAL-12 500. Watch `levels` for a vault-type dealer.
5. **Duels I ~11:30, Duels II ~18:00** run on rules; check the first two.
6. **Sunday**: Chamberí is a 0.9 set for us, so sell what we pull to collectors in the first hours.

Who wants what (from the feed, Friday): LAV buyers t10, t07, t01, t14 · MAL t17, t12, t10 · LAT t15, t07, t14 ·
SAL t18, t16, t03. `agent/team_intel.py` refreshes this.

## 0b. Where else to push (from RULES.md, Friday night)

1. **Judges, 40 points**, the largest share, and nothing prepared yet. Build one page for them on Saturday: feed intel
   that guessed our own multipliers blind, "numbers by code, words by the LLM" (validator, guard, tick budget),
   dealer models from other teams' threads (Abuela's floor, Chato's step-for-step midpoint), proof-only flags, the dashboard.
2. **Ladder: zero buys from Chato.** Best three deals per level count, a missing one as zero, higher levels weigh
   more. First thing Saturday: three good Chato deals on cheap items (uncommons ~26 list, not rares at 90), opening
   low with 5-6 P steps (section 6). Be first at every new dealer: early unlock is a head start.
3. **Market making, 30 points, we have 0.** Keep v03 open all day (each Market Test counts the best venue open during
   it; closing after a good session keeps nothing). We have 0 trades on v03: 0% fee, invitations, and a broker that
   pairs other teams' crossing offers on our venue.
4. **Duels**: I (~11:30), II (~18:00), III and the Grand Final on Sunday. No deal is 0, a deal past our limit is
   negative. Merge `duels-wait-rule` if Duels I rivals concede like the practice ones.
5. Bookkeeping: Friday weighs 1/2, Saturday and Sunday 1 each (Saturday is ~40% of the total). Penalties are a share
   of the round score: no key sharing, no feeding another team. Gifts, pack luck, easter eggs and hidden cards never score.

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

**Stopping or restarting the agent:** `kill $(cat team13/logs/agent.lock)` (the pid of the one running agent).
Never `pkill -f "python -u agent.py"`: it matches the launching shell, not the Python process (on macOS it is `Python`),
and on Friday it left three stale agents with old code running for 15 minutes. The agent now holds a lock: a second
copy exits at once with "Another agent is already running".

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
| Never pay above a card's team cap | `agent/caps.json` | `SAL-11: 200`, `SAL-12: 500` (SAL-10 done) |
| Keep cash for the market bond | Strategy tab `reserve_cash` / `seek_keep_cash` | 270 P until our venue is open |
| A trade must gain us at least | Strategy tab `trade_min_gain` | 3 P after fees |
| Never give away a card of a nearly complete page cheaply | `values.py` page option | automatic |
| Cancel anything that loses value or breaks a cap | `guard` module, last each tick | automatic |
| Claude writes words only inside the tick budget | `agent.py` `tick_deadline` | 70% of the tick, 8 s max per call |
| Our market fee | Strategy tab `venue_fee_bps` | 0 (fees never score, and they block thin matches) |
| A flip must clear, after both fees | Strategy tab `flip_min_gain` / `flip_max_cash` | 4 P / 120 P per flip, one at a time |

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

## 6. El Chato (level 2): how he negotiates (from 10 threads on Friday)

- **He mirrors the size of our step, nothing else.** "Six from you. Six from me." Small steps get mocked and earn
  0 to 1 P ("Three points. That is your big move?"). Words, speed of payment and long messages do not move him.
- **Openings are far above list:** rares 97 (list 77), uncommons 33 (list 26), silver pack 188 (list 150).
  With step-for-step mirroring the deal lands near the **midpoint of the two openings**: to land at X, open at
  about 2X minus his opening (for a rare at 97, open near 2 x 72 - 97 = 47 to land near 72).
- Being "Carmen's friend" (straight dealing with Abuela) gives a warmer greeting, not a lower price.
- Patience 0.35, memory 0.9, strictness 0.85: few rounds, steps of 5 to 6 P, no tricks, never repeat a price.
  Walk away politely when the midpoint is above the cap ("That is our limit for tonight"): he remembers.
- Friday: MAL-09 thread 195, we opened 58, he 97; walked at 74 vs 86 (midpoint 80 above our cap 75).

## 7. Duels: what the practice round showed (Fri 22:20, 12 of our duels)

- Payload: the id is `duel` (not `id`), rival messages are signed with an alias in `from`, ours with `"you"`,
  our standing offer is `your_offer`, and `result` is our surplus x (1 - decay)^rounds (duel 15: 46 x 0.94^2 = 40.6).
- Real rivals open near a fair price and **concede steadily, 3 to 8% of the price per round**, whatever we do
  (a buyer went 78, 84, 87, 90, 93, 96, 99, 102, 105 while we came down from 181). Some rivals never speak.
- Decay is 6% per round, so while our surplus is small, waiting beats accepting: one more round wins the rival's
  step x 0.94 and costs 6% of our surplus. We accepted 54 at once in duel 16 (surplus 20) against a rival moving
  about 5 P a round.
- Branch `duels-wait-rule`: keep talking while the rival's recent step beats decay / (1 - decay) x our surplus,
  with 3+ ticks left. Simulator: better against conceders, tough rivals and clones; worse against step-matchers
  (tit-for-tat 0.43 -> 0.37). Merge if Duels I rivals look like Friday's practice rivals.

## 8. Flags (a correct flag scores, a wrong one costs)

`team13/flags.py` runs every tick on dealer messages sent to us. It flags only what the dealer's own structured
offer proves false: a stated price that differs from the offer's price, or a card code (LAV-03) that differs from
the card the offer gives. Proven bluffs ("final", then a better price in the same conversation) and catalog
contradictions are logged as candidates (`flag_bluffs`, `flag_catalog` switch them on). Card names are never
flagged automatically: Abuela names her gifts. Friday's 912 dealer messages had no provable lie, so expect the
first real flags from the dealers still to come. Check `logs/decisions.jsonl` for `flag candidate` lines.

## 9. How the rivals scored on Friday (from the public feed at close)

Board at 23:00: t13 30.0, t12 26.8, t08 23.5, t17 21.1, t10 20.5 (market 0 for everyone: no Market Test yet).
A settlement's `price` is the total for all its items (t08 sold 4 commons to Abuela for 23 in one deal, not 23 each).

| Team | Main source of points |
|---|---|
| t12 | Sold its Salamanca rares to collectors (SAL-10 at 80, SAL-09 at 75), bought both Malasaña rares from Chato (MAL-09 at 90, MAL-10 at 89) and completed that page; 0% market |
| t08 | Sold LAV-10 to t10 at 70, bought MAL-10 from t14 at 53, cheap commons at 6 to 9 |
| t17 | Salamanca and Malasaña collector: SAL-09 at 75, SAL-06, SAL-08, MAL-07; one page complete |
| t10 | Most Abuela deals (10, good prices) and the Lavapiés page: LAV-10 at 70, LAV-09 from Chato at 90 |

What it means for Saturday:
- Every top-5 team completed a page: the page bonus is the big lever in team trades.
- Everyone gains by selling the sets they value least to the teams that value them most; t12 did it best.
- **Chato's real rare price is 82 to 90** (MAL-09 90, MAL-10 89, LAV-09 90, LAV-10 82); our 75 cap was below it.
- **Our Malasaña page (6/10) is contested**: t12 holds both rares, t17 bids 78 and t08 62 for MAL-09. Completing it
  needs MAL-03 (~10), MAL-07 (~25), MAL-09 and MAL-10 (~85 each at Chato): ~205 P for ~313 of value with the page
  bonus, about +108. Decide in the morning once the 150 P allowance is in and flips have started.
- Sell to collectors at their prices: LAV-08 (worth 27.5 to us) to t10 or t04, Salamanca duplicates to t17 or t18.
- Sell spares to Abuela in one bundle per deal (her limit is 8 deals per team per hour).

