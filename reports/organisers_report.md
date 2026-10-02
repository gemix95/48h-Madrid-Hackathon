# The Bazaar: observations from Team 13 (Friday 2 Oct)

**From:** Team 13 · **For:** the Causa Prima organisers · **Date:** Saturday 3 Oct, before opening

We found these while playing normally on Friday. Nothing here came from probing the server: every item comes from
the public feed (3,757 public events collected from tick 0), our own team's API responses, ordinary public reads
(`/api/feed`, `/api/levels`, `/api/dealers`, `/api/personas`) and comparing them with `RULES.md`, `README.md` and
the SDK docstrings. We have not tried to exploit anything and we will not. If you would like us to test edge cases
on purpose, tell us the scope and we will stay inside it.

## Summary

| # | Finding | Kind | Who it affects |
|---|---|---|---|
| 1 | A venue's name can advertise a fee it no longer charges, or no longer advertise one it does | market integrity | every team choosing a market |
| 2 | Offers inside threads expire after 2 ticks; this is not documented | undocumented behaviour | every agent, deals silently lost |
| 3 | A settlement's `price` is the total for all its items, not per item | undocumented behaviour | anyone analysing the feed |
| 4 | `/api/feed` returns at most 500 events whatever `limit` asks for | docs vs behaviour | late joiners, analytics |
| 5 | The duel payload has no `id` (it is `duel`); the SDK names the field `deadline` but the payload says `deadline_tick` | docs vs behaviour | every agent built from the SDK |
| 6 | `/api/levels` uses `state`, `/api/dealers` uses `status` for the same idea | consistency | agent authors |
| 7 | Friday's first Market Test was scheduled at game hour 3.0, after the 23:00 close | schedule | market-making scores |
| 8 | How "a negotiated deal" is counted for level unlocks is unclear | question | level unlocks |

## Details

### 1. Venue names can misstate the fee (market integrity)

- **What we saw.** Our venue `v03` opened as "Mercado Trece · 1% fee" at 100 bps. Its fee then moved to 0 bps
  (pending at tick 136, applied after the public notice), but the name still says "1% fee" and so does its
  description. Names are set once at opening, while `PATCH /api/venues/{vid}` changes only the fee.
- **Why it matters.** It works the other way too: a venue could open as "zero fee", attract listings, then raise
  the fee after the notice while its name keeps saying "zero fee". Fee words in names are free text, so a name can
  misstate the fee.
- **Suggestion.** Show the live fee next to every venue name on the big screen and in `/api/venues`, or forbid fee
  words in names, or let the owner rename together with a fee change.

### 2. Thread offers expire after 2 ticks

- **What we saw.** In dealer threads and team-to-team threads, a structured offer's `expires_tick` is
  `created_tick + 2`, for example offer 926 in thread 119 (created tick 66, expired tick 68). Team agents that
  answer on their own schedule never see it: our swap offers to two teams expired unanswered.
- **Docs.** RULES and README describe `expires_in_ticks` (default 40) for board offers only; the 2-tick life of
  thread offers is not mentioned. On Saturday, with 30 s ticks, that is 60 s, and 30 s on Sunday.
- **Suggestion.** Document it (or make it `expires_in_ticks`-configurable for team threads).

### 3. `settlement.price` is the total for the whole settlement

- **What we saw.** Settlement at tick 45: t08 sold LAT-05, LAT-05, SAL-02, SAL-02 to Abuela with `price: 23`,
  that is 23 for all four cards, not 23 each. Nothing in the payload says so; per-item prices are not given.
- **Suggestion.** Document it, or add `unit_price` / per-item prices.

### 4. `/api/feed` caps at 500 events

- **What we saw.** `limit=10` returns 10 and `limit=500` returns 500, but `limit=501` and `limit=2000` also return
  500. By Friday's close the server served only the latest 500 of about 3,700 public events, so the early game is
  gone for anyone who did not collect it.
- **Suggestion.** Document the cap, or add `after=<event id>` paging (the broker asset listing already has `after`).

### 5. Duel payload vs SDK docstrings

- **What we saw.** `GET /api/duels` items have `duel` (the id), `deadline_tick`, `your_offer`, `rival_offer`,
  `messages[].from` (`"you"` or the rival's alias). The SDK docstring says "role, your_limit, rival_offer, deadline",
  and `duel_say(duel_id, ...)` invites code like `d["id"]`. Our agent crashed on every practice duel until we found
  the field is `duel`; we suspect other teams hit the same.
- **Suggestion.** One sample duel payload in README (as there is for offers) would save everyone the practice round.

### 6. `state` vs `status`

- `/api/levels` items carry `state` ("announced", "active"); `/api/dealers` items carry `status` for the same
  lifecycle. Minor, but an agent that reads one endpoint the way it reads the other misses a level activation.

### 7. Friday's Market Test fell after closing

- `/api/schedule` put "The Market Test" at game hour 3.0. Friday's clock started paused and reached game hour 1.37
  at 21:41, so hour 3.0 fell at about 23:20, after the 23:00 close. Fine if intended; worth a line on the screen,
  since market-making is 30 points and teams prepared brokers for it.

### 8. Question: what counts as a negotiated deal for unlocks?

- RULES: "a deal at the dealer's opening price does not count, a negotiated one does". Unlock events say, for
  example, "3 deals with abuela". From the public feed we could not match every unlocking team to three deals that
  closed below Abuela's first ask, but our matching of settlements to threads is approximate, so this is a question,
  not a finding. Does a deal count when Abuela's fixed "made for beginners" price is accepted after some talk?

## What we did not see

- No private data in public events: payload fields are offer cash, card refs and team ids only.
- No duplicate event ids; no zero or negative prices or fees; no item moving to its own owner.
- No team above a dealer's hourly deal limit (Abuela 8, Chato 6) in any game hour.
- Server restart at tick ~95: announced, about a minute of HTTP 502, all state kept as promised.

Thank you for a great game. Happy to walk you through any of this at the desk.
