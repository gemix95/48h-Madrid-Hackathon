# Team 13 war room (TypeScript)

One process that explains the game, shows where our points come from and where they leak, and runs three agents:
**deal** (team trades and the dealer ladder), **duel** (the 1-to-1 duels), and **market** (our venue in the Market Tests).

```bash
cd platform
npm install
npm run build        # web UI → web/dist, then a typecheck
npm start            # http://localhost:8787
npm test             # brain unit tests
npm run sim:market   # matching rules compared on 3,000 simulated Market Tests
```

It reads `BAZAAR_URL` and `BAZAAR_KEY` from `../bazaar.env`, so with no setup it sees the live game read-only.

## Safety: shadow mode by default

The Python agents on the server trade with the same team key. Two processes on one key send double accepts, and both
talk to the same dealer. So every agent here starts in **shadow** mode: it decides and logs "would do …", but it
does not act. In the UI, the Agents page switches each agent to `off`, `shadow` or `live`, and the choice persists in `data/modes.json`.

Before switching an agent to live, turn off the Python module that does the same job:

| TS agent | Disable in Python first |
|---|---|
| duel | `enable_duels: 0` in `team13/strategy.json` |
| deal (team trades) | `enable_trader: 0` in `team13/strategy.json` |
| deal (dealer haggling) | only the dealers in `DEAL_DEALERS`. Pick dealers outside the Python agents' scope (Sergio: Abuela + Pilar, Emmanuele: Chato + Pícaros), e.g. `DEAL_DEALERS=banco` |
| market | stop the `bazaar-broker` service and set `BROKER_KEY` |

Live mode from the browser requires `WAR_ROOM_PASSWORD` (HTTP basic auth on the whole UI). The other way to go live is
`LIVE_AGENTS=duel,market` at start.

## Environment

| Var | Default | Meaning |
|---|---|---|
| `PORT` | 8787 | HTTP port |
| `WAR_ROOM_PASSWORD` | – | password for the UI, required to go live from the browser |
| `LIVE_AGENTS` | – | agents to start live, e.g. `duel,market` |
| `BROKER_KEY` | – | the venue broker key, needed by the market agent |
| `DEAL_DEALERS` | – | dealers the deal agent may haggle with (comma list) |
| `DEAL_MIN_GAIN` | 3 | the smallest gain at our values for a team trade |
| `ACCEPT_PARITY` | any | `odd` / `even`: accept only on those ticks, to share the 1-accept-per-tick limit with the Python agents |
| `ROUND_START_TICK` | auto | override the first tick of the current round |
| `DATA_DIR` | `./data` | decision log, feed cache, leaderboard history |
| `RESERVED_FILE` | `../team13/reserved.json` | cards no agent may sell, offer or swap |

## Layout

- `src/sdk`: typed client for the Bazaar API and the broker API, with a shared rate limiter.
- `src/game`: private values (book × multiplier × copy factor, page and master bonus) and the scoring formulas.
- `src/agents/*Brain.ts`: pure decision functions, unit-tested in `test/`.
- `src/agents/*Agent.ts`: these wrap the brains with live state, and every decision is logged with its reason.
- `src/engine`: the world state (one refresh loop for everyone), the runner, the decision log, and the insights behind the UI.
- `src/sim/marketSim.ts`: the Market Test simulator.
- `web/`: the React UI (Now, Guide, Agents, Duels, Dealers, Market, Album, Rivals).
