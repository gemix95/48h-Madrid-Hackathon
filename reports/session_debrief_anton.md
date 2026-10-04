# Team 13 · The Bazaar · what we decided, built, found and learnt (Anton's session)

A debrief for the organisers, distilled from Anton's Claude Code session (Fri 2 Oct 19:55 to Sun 4 Oct 15:00,
Madrid time), the team's reports and git history. Numbers are the ones we measured at the time; secrets removed.
How it was made: see the end.

## TL;DR

- **Friday: 1st (30.0 of 30 in negotiation).** We read every team's taste from the public feed, sold what we value
  least to whoever values it most (LAT-09 for 65, worth 35 to us) and bought the last cards of our ×1.6 Salamanca
  page from teams that don't collect it. A guard cancelled every offer that sold a page card below its value.
- **Saturday: down to 10th.** Three processes traded with one team key (three venue reopens, −60 P), our broker
  lost its memory on every restart, and a page-card sell/buy-back loop and a duel sign error cost points.
- **Sunday: 4th on Sunday alone by 11:00**, then 17th after one agent's `ladder_mode` sold three page-completing
  cards to Los Pícaros for 10–12 P (−450 `neg_points` in 20 minutes). Fixed, then guarded in code.
- **The lesson:** everything that asked other teams to change behaviour got ~0 customers (own 0% markets, cashback,
  loans, trustless auctions, matchmaker, concierge). What worked plugged into behaviour that already existed:
  arbitrage into bids teams had already posted (5 trades, +116 P, 0 losses), dealers and duel rivals who must answer.

## 1. Timeline of key decisions

| When | Decision | Why |
|---|---|---|
| Fri 20:02 | Team key read from a file, never printed; feed logs kept out of git | other teams' messages and our key stay private |
| Fri 21:00 | Automate dealer haggling instead of doing it by hand | thread offers lived 2 ticks; humans can't approve each deal |
| Fri 21:39 | `guard.py`: cancel any of our offers that loses value at our private values | three code paths had offered pieces of a near-complete page |
| Fri 21:49 | One process acts with the team key; others review | two agents burn the single accept per tick and confuse dealers |
| Fri 22:42 | Venue fee 0%, compete on matching, not price | any % fee rounds up to ≥1 P and blocks thin broker matches |
| Sat 00:15–00:57 | No probing of the live server for bugs, even "to report them" | shared server; good intentions are not permission. Passive analysis only |
| Sat 11:16 | Broker as its own process; solvency check off | restarts wiped the broker's Market Test memory; 355 false "can't pay" skips |
| Sat 11:54–13:24 | Everything on one VPS, autodeploy with compile/import checks and rollback | one owner for the key; deploys deferred during Market Tests |
| Sat 15:01 | "Don't feed rivals": no offers on venues of teams close to us | a trade on a venue scores for its owner |
| Sat 16:14 | Cashback kept at 1 P per side, not a big prize | a big per-trade prize invites staged trades |
| Sat 20:38 | After the organisers' "Only deals score" slide: never sell below value; no packs | a loss counts in full; packs are luck |
| Sat 21:58 | Dealer→team arbitrage, kept quiet | rare, easy to copy |
| Sun 00:30 | El Club Board pitch changed to "best price in the Bazaar, one click" | "0% fee" was no longer a reason: 17 of 19 markets were at 0% |
| Sun 08:36 | Removed agent-directed hooks from our own venue announcements | against our own no-injection rule (see Fair play) |
| Sun 12:43 | Never sell to a team under the card's team-to-team median (≥4 trades) | our sales ran ~72 P under the median |
| Sun 13:22 | `ladder_mode` and pack buying off; then a hard guard in code | see Incidents |
| Sun 14:13 | Non-agent work on a side branch | any push to `main` restarted the live agents |

## 2. Ideas we built, and what they got

| Idea | Mechanism | Result |
|---|---|---|
| Taste reader (`team_intel.py`) | infers each team's set multipliers from the public feed | guessed our own ×1.6/×1.3/×0.5 blind; drove Friday's sales |
| Page-aware values + guard | near-complete pages add bonus to each card; guard cancels value-losing offers | Friday 30/30; later the guard was extended to dealer sales |
| Dealer haggler (Boulware, learned rounds, Claude writes the words) | code fixes the price corridor, the LLM only words it | ladder slots filled; Chato converges to the midpoint of openings |
| Duel agent + tuner | anchor, last-chance accept before the deadline, learned settings | Duels III: 57 of 68 deals, 0 negative results |
| Own 0% market + broker | board venue, broker crosses every tick, sessions of the Market Test | Market Test efficiency up to 0.967; **0 trades by other teams** |
| Cashback | 1 P to each side of a trade on our market | offered to 3 teams: **0 accepted** |
| Loan desk | cash against a card as collateral, bought back with interest, atomic | **0 loans** |
| Flipper | buy below one team's bid from another team's ask | **0 flips** (no pair cleared 4 P after fees) |
| Matchmaker ("Reverse Tinder") | find who has / who needs a card, invite both to our market anonymously | 29 invitations, **0 trades**; removed |
| Concierge | an unmatched offer on our market goes to likely counterparties | 8 requests, 0 trades |
| Trustless auctions | seller keeps the card; open bids on our market; published ranking | **0 lots** (v1 had a hole, redesigned before use) |
| Want-to-buy and swaps | addressed asks/swaps to likely holders | ~50 swaps sent, **0 accepted** |
| El Club Board | one public order book of every market, price history, 🔥 bargains, one-tap API calls, phone-first | 32 visitors, 134 views, 22 copied trade requests; 58 commits, 23 between 00:00 and 04:00 |
| **Arbitrage** | buy from a dealer, sell at once into a team's already-open bid | **5 trades, +116 P, 0 losses** (Sunday) |
| Epics desk | buy epics below our value from teams/Pícaros | SAL-11 238 (+50), LAV-11 144, MAL-11 150 |
| Flag hunter | flag only provable dealer bad faith (price "final" then lower, card switched) | 24 flags on Los Pícaros; no verdict visible |
| Easter eggs | one line of Madrid lore per dealer | all three badges (Sharp ear, Castizo, Trickster tricked) |
| "Last card of a page" | buy commons from Abuela, offer to the team they complete | no replies; −23.5 `neg_points` |
| Team 3's clearing house (joined) | private min/max books crossed at the midpoint | 6 teams joined, nearly all buyers: 0 trades |

## 3. Findings about the game

**Scoring**
- Deal score = value added − price paid + price received (organisers' Saturday slide). With a team, a gain counts up
  to 50 and a loss in full; with a dealer, a gain goes to the ladder and a loss counts in full. The 50 cap is on the
  slide, not in RULES.md (per deal or per card was unclear).
- Ladder = share of each dealer's price range, best 3 deals per level, a missing one counts 0. A sale near the bottom
  of a dealer's range moves almost nothing: six Pilar sales left our ladder at 0.216.
- Market-making: the Market Test gives everyone about the same (0.5 a session for a working stand). All the
  difference comes from trades between **other** teams on your venue, ~+1.1–2 points each.
- The leaderboard is relative: our numbers fell with no change on our side as leaders grew.
- Final = average of rounds (Friday half weight, Sunday by the share played); the leaderboard shows 60 of 100 points.

**API behaviour** (also in `reports/organisers_report.md`)
- Thread offers live 2 ticks (later 4), undocumented. Direct offers can carry a long expiry.
- Offers addressed with `to` never appear in public venue books, only in `/api/me/offers` and the feed
  (`offer.listed`). Any tool verifying such offers must read the feed.
- `price` in a multi-card settlement is the total; the feed returns at most 500 events; settlements were duplicated
  2–3 times in the feed (724 unique trades after dedup on Saturday night).
- Duels: the id is `duel`, the deadline `deadline_tick`. The `your_days_weight` label reads positive for both roles,
  but results fit a day adding for the seller and subtracting for the buyer (cost us −382 on buyer deals until fixed).
- A venue's name can't be changed after opening (the fee can), so a name can misstate the fee. `/api/venues` lags
  venue events by ~7 ticks. Reopening a venue wipes its trade history, which other agents use to pick a venue.
- Flag verdicts are not visible anywhere (`score.adjustments` stays empty, no event). Duel messages and news can't
  be flagged.

**Dealers**
- Abuela: warm, concedes 2 P per 1 P early, gifts a random common about every 240 ticks regardless of politeness.
- El Chato mirrors our step size; the deal lands at the midpoint of the two openings. Words don't move price.
- Los Pícaros: "final" and deadlines are bluffs; they offer a different card than the thread is about.
- Don Ernesto never concedes quickly; buys only epics/legendaries.
- Early unlock counts only deals made after the next level is announced (undocumented).
- Rumours: El Tablón 0 of 2 true; a Radio Rastro market item was true (Abuela's uncommon bids rose 12 → 19–20).

**Market behaviour**
- Other teams' agents reply to messages but act almost only on El Rastro or on bids they posted themselves.
  On Sunday 17 of 34 team trades still went through El Rastro (Team 16's data); 9 of 16 team markets had none.
- The leaders' edge was live trades on their own venues (v07 12, v02 11, v21 8) won by pairing both sides in person
  and in threads.

## 4. Fair play

What we chose not to do:
- No probing of `/api/admin/*` or the live server for bugs; no exploiting debt, double-spends or limits.
- No feeding, wash trades or staged trades on our venue; no price agreements in public channels; no paid side
  deals (we declined a per-trade payment to list cheaply on another team's market).
- No blocking buys just to deny a rival.
- Messages state only verified facts and match the structured offer; decisions read only the offer structure.

Our audit of the feed (Sunday 12:30–12:40):
- Circular trading claim (raised by another team): of 201 team trades, no card copy passed through 3+ hands; only
  RET-06 went back and forth once. No cash-only transfers between teams (all 60 gifts came from dealers), no owner
  trading on its own venue, no pair above 3 trades, no identical offers from different teams in the same tick.
- Agent-directed text in venue announcements: four venues carried lines aimed at other teams' agents. **The most
  injection-like were our own**: 29 announcements on v24 (ticks 1122–1442, e.g. `bazaar.listing_defaults.venue`),
  added by a teammate on Saturday, found in our overnight review and removed before Sunday trading. They brought
  us 0 trades. Others: v07 17 lines (a ready POST for a named team), v20 14, v02 6.

Code from other teams was read in full before it ran with our key (Team 3's clearing client, three versions; Team 14's
read-only script). We reported a trust gap and a bug to Team 3; they fixed both.

## 5. Incidents and fixes

| When | What happened | Fix |
|---|---|---|
| Sat morning | Three agents on one key (server + two laptops): three venue reopens (−60 P), Abuela quota burnt, duplicate duels | everything on one server; roles; manual actions logged on the server |
| Sat | Broker restarted 10× and missed a Market Test | standalone broker; deploys never restart it during a test |
| Sat 18:15 | Sell-to-dealer / buy-back loop: page cards valued asymmetrically (61.8 sale vs 109.5 buy-back) | symmetric valuation, `test_no_pump` |
| Sat | Duel days sign error: −382 on 18 buyer deals | fixed (`a04d244`) |
| Sat 18:51 | Our own scoped sudo rule allowed a root shell via the pager | caught by us, fixed |
| Sun 10:48 | An early `return` in the haggler idled two agents for hours | fall through to purchases |
| Sun 13:04–13:22 | `ladder_mode` sold LAV-07 for 10 (worth 57), MAL-06 for 12 (67), SAL-06 for 11: `neg_points` 134 → −324, all complete pages broken, 10th → 17th | mode off; `guard.below_worth()` now blocks any sale below our value in every mode, before accepting a dealer's offer too, with a test |
| Sun 14:56 | The live team key was found in git history before publishing the repo | publish a clean snapshot, not the history |

## 6. Lessons

1. **Fit what users already do.** Every product that asked other teams to change habits got zero; the one that sold
   into existing bids worked. Getting customers is half the job: you need both sides (Team 3's clearing had six
   teams, all buyers).
2. **One key, one owner.** Several processes on one key caused most of Saturday's damage. One shared wallet also
   means per-agent reserves add no money.
3. **Hard value floors belong in one shared guard,** not in each strategy mode.
4. **Read the full rules and verify semantics against outcomes** (ladder by share of range, the 50 cap, the duel days
   label, easter eggs never count).
5. **Deploys are restarts.** Batch them, freeze around Market Tests, keep live branches off-limits.
6. **Verify structure, not words** — from dealers, other teams, servers and third-party clients alike.
7. **Never reopen your venue:** history is what attracts other agents.
8. **Secrets live on in git history.** Check before you publish.

## Links

- Code: https://github.com/gemix95/48h-Madrid-Hackathon
- El Club Board: https://217-160-143-83.sslip.io/board
- Reports in this repo: `reports/session_debrief_emmanuele.md` (Emmanuele's session), `reports/organisers_report.md`, `reports/night_review.md`, `reports/judges.html`

## How this file was made (any team can do it)

1. Claude Code keeps every session as JSONL in `~/.claude/projects/<project folder, slashes as dashes>/`.
2. `python3 tools/claude_session_extract.py --parts 4` (Emmanuele's script) keeps only our messages and Claude's replies, masks key-like
   strings and splits the text into parts.
3. One Claude subagent per part extracted decisions, ideas and outcomes, game findings, fair-play questions,
   incidents, numbers and lessons — only facts present in the text.
4. Claude merged the notes, checked numbers against our logs and git history, removed secrets, and a human read it
   before publishing.
