# Rules for every agent on this repo (the bots, Claude Code, Codex, Cursor)

- Pull before you start whenever there are no conflicts; push after every change.

## Who runs which agent (one key, disjoint roles)

- **Sergio's Mac: `AGENT_ROLE=market`** (venue, trader, flipper, wtb), since Saturday 11:40.
- **Emmanuele's agent must run `AGENT_ROLE=dealers`** (duels, haggler): pull, then
  `kill $(cat team13/logs/agent.lock); source bazaar.env && cd team13 && AGENT_ROLE=dealers python3 agent.py`.
  Two agents with role `all` haggle with the same dealers and trade the same cards.
- The AI negotiator is Claude Opus 5.5 at medium effort (`llm_effort` 1). It needs the `anthropic` SDK and the key
  in `anthropic.env` at the repo root (git-ignored). The agent picks the key up within 30 s, with no restart.

## El Consejo: the shared board

- The board is the `council` branch, checked out at `.council/` by `team13/council.py` on first use. Never edit it,
  or the old `team13/logs/council.jsonl`, by hand.
- Pull it every 15 s and push every note at once. Without this, each laptop has its own board and agents step on each
  other's deals. The agent, the tuner (`council.py run`) and the dashboard do it by themselves;
  `python3 team13/council.py sync` does it on demand. Each process writes its own file, so pushes never conflict.
- Notes keep the current notation. `author` is the module (haggler, trader, duels, flipper, ...) or `tuner`. Every note
  also says who wrote it:
  - `agent` is the market agent's unique id: a famous businessperson's surname picked at start (Rockefeller, Fugger, ...).
    It stays the same until that agent restarts.
  - `role` is its `AGENT_ROLE` (all, dealers, market, or a list of modules).
- Anyone not trading in the market posts as **Admin**: a human, an analysis script, or a coding session.
  `python3 team13/council.py post "t04 always opens Abuela at 18 P" --topic rivals`
- What to post:
  - Mainly the deals you open, close or walk away from, so the others don't take the same deal. The agents post theirs
    automatically.
  - Also which strategies work and which don't.
- Before any deal by hand (`agent/hand.py`, a script), read the board: `python3 team13/council.py read`.
