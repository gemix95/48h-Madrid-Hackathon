#!/bin/bash
# Two agents on one laptop: dealers + market. They coordinate via El Consejo (council.py sync).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source bazaar.env
cd team13

stop_one() {
  local label=$1
  local lock=logs/agent-${label}.lock
  if [[ -f $lock ]]; then
    kill "$(cat "$lock")" 2>/dev/null || true
    rm -f "$lock"
  fi
}

stop_one dealers
stop_one market
stop_one all
rm -f logs/agent.lock

echo "Starting dealers agent..."
AGENT_ROLE=dealers python3 agent.py &
sleep 2
echo "Starting market agent..."
AGENT_ROLE=market python3 agent.py &

echo "Dealers PID: $(cat logs/agent-dealers.lock 2>/dev/null || echo '?')"
echo "Market PID:  $(cat logs/agent-market.lock 2>/dev/null || echo '?')"
echo "Logs: tail -f team13/logs/decisions.jsonl"
