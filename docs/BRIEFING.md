# Team 13 · Briefing: everything we know (Saturday morning, 3 Oct)

## 1. The game in one page

**The Bazaar · Cromos de Madrid**, hosted by Causa Prima. 18 teams of 3. Agents collect Madrid trading cards, haggle
with card dealers, trade with other teams' agents, and run their own markets, all through one HTTP API and a team key.

- **Hours:** Fri 19:00–23:00 (60 s ticks) · Sat 09:00–23:00 (30 s) · Sun 09:00–15:00 (15 s). Nothing ticks when the
  doors are closed or the organisers pause the clock. Per tick: 1 accept per team, 1 message per conversation,
  12 new listings, at most 6 open conversations and 30 open offers.
- **Words persuade, structure binds:** only a structured offer the other side accepts moves cards or cash, on the
  next tick. Prompt injection between agents is allowed; dealers' prices never change because of words.
- **Score = 100 points:** 30 negotiating (dealer ladder: share of each dealer's price range we capture, best 3 deals
  per level; duels; value gained trading with teams at our private values) + 30 market-making (Market Test
  efficiency of our broker; value created between other teams on our market) + 40 judges (ideas and craft).
  Never counts: number of trades, fees earned, pack luck, gifts. Each day is a round, averaged; Friday counts half.
- **Cards:** 6 neighbourhoods × 12 cards (5 commons, 3 uncommons, 2 rares, 1 epic, 1 legendary). A page = commons +
  uncommons + rares; a full page earns a bonus. Values are private: book × our set multiplier × copy factor
  (1st copy ×1, 2nd ×0.25, 3rd ×0.1) + page bonus.
- **Our multipliers:** Salamanca ×1.6, Malasaña ×1.3, Lavapiés ×1.1, Chamberí ×0.9, El Retiro ×0.7, La Latina ×0.5.
  So we collect Salamanca/Malasaña and trade La Latina/Retiro away.

## 2. Where we stand

- **Rank #1, 30.0 points** (all negotiating so far; nobody has market-making points yet). Level 2.
- **Album:** Salamanca 10/10 (complete, page bonus earned), Malasaña 7/10, Lavapiés 6/10, La Latina 3/10.
- **Cash ~17 P** after paying the market bond (250 P refundable + 20 P fee). Saturday grant: +150 P when the clock
  starts (also a pack; El Retiro released).
- **Our market:** *Mercado Trece · 1% fee* (venue v03), open since Friday tick 129. Fee back to 1% at Emmanuele's
  request (Anton prefers 0%: a fee can block thin-margin Market Test pairs). Name for any future reopening:
  *El Club · Where Madrid Trades*. Rival markets: El Duende (0%), Team 2 · El Rastro Express (0%), Mercado Team 6 (0.5%).
- **Saturday morning:** doors open but the organisers have paused the clock (tick 159). Our agent is running and
  waiting.

## 3. What we learned from the data (public feed, every team)

- **Abuela Carmen** (level 1): honest (0 of 258 priced messages contradicted her offer). Every team's first deal is a
  fixed "welcome" price (~70% of list: 17 P pack/uncommon, 7 P common), not negotiable, likely worthless for the ladder.
  Haggled deals: pack 19–24 P (she opens at 30), uncommon 21–25 P (opens 29), common 9–12 P (opens 12). She gives back
  ~1 P for each 1 P we move and names a final offer after ~5 of our offers. She buys commons/uncommons; she gives a
  free card after a team's first deals.
- **El Chato** (level 2, open to all since game hour 2.63): sells silver packs (list 150), uncommons (26) and rares
  (77); buys uncommons and rares. He mirrors your step size and lands near the midpoint of the openings.
- **Best first offer** across 50+ finished conversations: 30–45% of the dealer's opening → lowest price (~77% of the
  opening). Our deals so far: ~79% vs ~80% for other teams.
- **Market:** El Rastro ~465 P/hour of volume, average trade ~24.5 P, every team lists spare commons at ~10 P. Nothing
  on sale has been worth more to us than its price lately.
- **Duels** (practice round, Friday): the payload calls the id `duel`, the rival signs messages with an alias
  ("Rival Rojo"), our last offer is in `your_offer`. Scored duels: Duels I (game hour 6.5, Saturday), Duels II
  (price + delivery day, 13.0), Duels III (Sunday 20.0), Grand Final (23.0).

## 4. What we built (repo `gemix95/48h-Madrid-Hackathon`, private)

**`team13/` — the agent (the only process that writes with our key, runs on Emmanuele's laptop):**

| Module | What it does |
|---|---|
| `values.py` | Exact private-value model (matches the server's `your_value`), incl. page bonus and near-complete page option |
| `haggler.py` | Dealer ladder: Boulware concessions, learned first offer + bandit, take finals up to a learned threshold, never past today's budget or the bond reserve |
| `trader.py` | Team trades on every market (fees per market), list spares just under rival prices, bids for cards we need, haggle with the team behind a listing, ask holders for page completers, team price caps (`agent/caps.json`), never sell a card from a page at 8/10+ |
| `duels.py` | Never crosses our limit; opens at most 60% away (buyer ~40% of value); settles within ~8 rounds; Anton's formula + our message attribution |
| `market.py` | Opens our market at level 2, runs the smart broker in-process, syncs the fee, FOMO announcements and invitations (true claims only), fee safety for the Market Test |
| `smart_broker.py` | Market Test broker: estimates hidden limits and the clearing price, matches efficient pairs first, falls back to the stall's plan |
| `negotiator.py` | Claude Opus 5.5 writes messages and picks the price inside the rules' safe band; reasoning effort low/medium/high; 8 s timeout and 70% of a tick |
| `security.py` | Prompt-injection defence: sanitises their text, detects manipulation (overrides, fake system lines, fake JSON offers, requests for our limits, pressure, fake authority), blocks any reply leaking numbers; manipulators untrusted for the day |
| `intel.py`, `learner.py`, `advisor.py` | Learn from every team's conversations in the public feed; propose better settings when they win by a clear margin |
| `guard.py`, `flipper.py` (Anton) | Cancel any of our offers that loses value or breaks a cap; buy below another team's bid and sell into it |
| `strategy.py` + `strategy.json` | Every knob, editable live from the dashboard's Strategy tab |

**Money plan:** 120 P buying budget per day, 40 P max per dealer item, 40 P cash floor, max 5 buys per dealer per day;
page completers exempt (but never below `seek_keep_cash`).

**`dashboard/` — the war room** (http://localhost:8765, login `team13`, password in `dashboard/.password`):
Overview, Strategy (plan, advisor, AI negotiator with spend and reasoning buttons, our market), Deals, Album, Market
(every market's offers), Intel (every team's prices), Rivals (leaderboard with spend, plain-language game feed), Agent
(right now, live conversations, today's results, lessons, the story).

**Tests:** `tests/tournament.py` (dealer and duel strategy tournament), `tests/sim_haggle.py`, `tests/sim_broker.py`,
`tests/check_guard_live.py`. Results in `tests/RESULTS.md`. Judges' write-up: `docs/HOW_WE_PLAY.md`. Operations:
`RUNBOOK.md`.

## 5. How to run it (own Terminal windows, no time limit)

```bash
cd ~/Desktop/"48h Madrid Hackathon" && git pull && source bazaar.env
cd team13 && ../.venv/bin/python -u agent.py                                  # agent (only one: it holds a lock)
cd dashboard && DASHBOARD_PASSWORD="$(cat .password)" ../.venv/bin/python -u server.py   # dashboard
cloudflared tunnel --url http://localhost:8765                               # link for teammates (changes on restart)
caffeinate -dims                                                             # keep the Mac awake
```
Stop the agent with `kill $(cat team13/logs/agent.lock)` (never `pkill -f "python -u agent.py"`).
Claude key: `anthropic.env` (git-ignored, loaded automatically). Spend so far: 264 calls, $1.99.

## 6. Lessons from Friday's incidents

- Stale agents kept running after restarts (wrong `pkill` pattern) → single-instance lock.
- A learned rule bought a pack while we were below the bond → every buy now checks today's budget and the reserve.
- Selling a card from our 9/10 Salamanca page was attempted → page protection + guard.
- Duel bot crashed on the real payload and made negative offers → fixed and verified live (surplus 20 and 46 in practice).
- Invitations were refused on our own market (self_venue) → they go through El Rastro.
- Processes started from a Claude Code session stop after 2 hours → run them in your own Terminal.

## 7. Saturday plan

- When the clock starts: +150 P grant, El Retiro released (×0.7 for us: trade it away), 30 s ticks.
- Market Test about every 2 game hours (first ~game hour 3.0): our market + smart broker play; fee safety on.
- Duels I at game hour 6.5: watch the Agent tab; the bot never crosses our limits.
- Keep exactly one agent; teammates watch the dashboard through the tunnel; code changes: push, then the host pulls and
  restarts the agent.
- Judges (40 points): rehearse a 3-minute demo of the war room; key ideas in `docs/HOW_WE_PLAY.md`.
