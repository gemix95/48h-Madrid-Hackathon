# 48h Madrid Hackathon · The Bazaar (Team 13)

- `bazaar-kit/`: the official kit (Python SDK, starter agent, starter broker, rules).
- `dashboard/`: a live local dashboard for our team.

Never commit the team key. Pass it through the environment:

```bash
export BAZAAR_URL=https://bazaar.causaprima.ai
export BAZAAR_KEY=tk-xxxx-xxxx
python3 bazaar-kit/starter_agent.py
python3 dashboard/server.py   # then open http://localhost:8765
```
