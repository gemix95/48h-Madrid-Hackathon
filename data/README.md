# data/

`feed.jsonl` is a snapshot of the public feed (`GET /api/feed`), one event per line, deduplicated by `id`,
collected from tick 0 by `agent/collect.py`. The server only serves the latest 500 events, so the early game
exists only here. Public scope only: no keys, no private values.

To keep collecting on your machine (it appends only new ids):

```bash
mkdir -p logs && cp data/feed.jsonl logs/feed.jsonl
source bazaar.env && python3 agent/collect.py 10
```

Readers: `agent/team_intel.py` (each team's set preferences), `agent/abuela_stats.py` (dealer price sequences).
