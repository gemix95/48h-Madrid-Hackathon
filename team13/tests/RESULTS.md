# Strategy tournament results (Friday 2 Oct, `python3 tests/tournament.py`)

Dealer score = share of the dealer's price range captured (the ladder metric), averaged over four dealer
personalities calibrated on our real Abuela threads (reciprocal, drop-and-hold, midpoint, impatient).
Duel score = share of the pie captured against four rival styles (tough, conceder, tit-for-tat, clone), 6% decay per round.

| Preset | Dealers mean | Dealers worst | Duels mean | Duels worst |
|---|---|---|---|---|
| Tournament winner | 0.81 | 0.76 | 0.60 | 0.54 |
| Previous defaults | 0.72 | 0.64 | 0.45 | 0.25 |
| Fast closer | 0.57 | 0.46 | 0.53 | 0.34 |
| Market first | 0.81 | 0.76 | 0.60 | 0.54 |

What made the difference:
- Dealers: open at 25% of list (not 45%) and take 18 rounds to reach our cap. Abuela is patient (85%) and holds at a
  floor, so a low anchor costs only time. The concession curve barely matters once the opening is low.
- Duels: open far from our limit (2x), concede over 8 rounds, and accept once the rival gives us 60% of what our next
  offer would. The old settings (accept at 90%) kept talking while the pie decayed.

Not tested: team-trading settings (they depend on what other teams post), and dealers that punish lowball offers.
Real Abuela behaviour so far: opened 12 on a 10-list common, dropped to 10 and held; opened 17 on a 25-list uncommon and held.
