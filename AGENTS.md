# Rules for every agent on this repo (the bots, Claude Code, Codex, Cursor)

- Pull before you start whenever there are no conflicts; push after every change.

## Who runs which agent (one key, one agent per person, on the server)

Do not start `agent.py` on a laptop. Both agents run on the server as `bazaar-agent@<name>` and share the team key.
A local process would trade as the same team. `pkill -f agent.py` then `pgrep -fl agent.py` must print nothing.

- **Sergio** (`server/agents/sergio.env`): L1 Abuela + L3 Pilar, no 2-hour cap (`team13/agents.json`), buys cards and
  packs and sells spares with Boulware, accepts on odd ticks (`AGENT_SLOT=1`).
- **Emmanuele** (`server/agents/emmanuele.env`): L2 Chato + L4 Pícaros, 30 P per 2 game hours, accepts on even ticks (`AGENT_SLOT=0`).
- Scope and budget live in `/home/bazaar/agents/<name>.env` on the server (root). `team13/agents.json` overrides any
  `AGENT_*` of a named agent from the repo: edit it and push, and auto-deploy restarts the agents.
- Code changes go to `main`. Auto-deploy restarts both agents.
- A laptop dashboard has no live agent log. Add `DASHBOARD_REMOTE=http://217.160.143.83` and
  `DASHBOARD_REMOTE_PASSWORD=<dashboard password>` to your `bazaar.env`. Each message of ours then shows who sent it:
  🤖 sergio / emmanuele / anton, 📜 script (server scripts), ✋ manual, or ⚠ not ours (no log on the server has it:
  a laptop or another program used our key).
- Emmanuele's log: `ssh <you>@217.160.143.83 'tail -f /home/bazaar/app/team13/logs/decisions.jsonl' | grep '"agent": "emmanuele"'`
- The AI negotiator is Claude Opus 5.5 at medium effort (`llm_effort` 1). It needs the `anthropic` SDK and the key
  in `anthropic.env` at the repo root (git-ignored). The agent picks the key up within 30 s, with no restart.

## Reserved cards

- `team13/reserved.json`: cards reserved for swap strategies (refs, asset ids, every common we hold twice, every
  Workshop pull). No agent module and no dashboard auto-swap sells, offers, swaps or burns them; we trade them by hand.
  Edit and push: the server deploys it within a minute.

## El Consejo: the shared board

- The board is the `council` branch, checked out at `.council/` by `team13/council.py` on first use. Never edit it,
  or the old `team13/logs/council.jsonl`, by hand.
- Pull it every 15 s and push every note at once. Without this, each laptop has its own board and agents step on each
  other's deals. The agent, the tuner (`council.py run`) and the dashboard do it by themselves;
  `python3 team13/council.py sync` does it on demand. Each process writes its own file, so pushes never conflict.
- Notes keep the current notation. `author` is the module (haggler, trader, duels, arbitrage, ...) or `tuner`. Every note
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
