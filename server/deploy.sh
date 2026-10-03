#!/bin/bash
# Auto-deploy: pull main, check it, copy code into the running app, restart, roll back if the agent will not start.
# Never touches server-only files: strategy.json, state.json, *.env, logs. Defers broker restarts during a Market Test.
set -u
SRC=/home/bazaar/src; APP=/home/bazaar/app; BRK=/home/bazaar/broker; LOG=/home/bazaar/deploy.log
export GIT_SSH_COMMAND="ssh -i /home/bazaar/.ssh/github_deploy -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/home/bazaar/.ssh/known_hosts"
say() { echo "$(date '+%F %T') $*" >> "$LOG"; }
cd "$SRC" || exit 0
export HOME=/root
git() { command git -c safe.directory="$SRC" "$@"; }
mkdir -p /home/bazaar/app.prev
git fetch -q origin main 2>>"$LOG" || { say "fetch failed"; exit 0; }
NEW=$(git rev-parse origin/main); OLD=$(cat /home/bazaar/deployed.sha 2>/dev/null || echo none)
BROKER_PENDING=$([ -f /home/bazaar/broker.pending ] && echo 1 || echo 0)
[ "$NEW" = "$OLD" ] && [ "$BROKER_PENDING" = 0 ] && exit 0
if [ "$NEW" != "$OLD" ]; then
  git reset -q --hard "$NEW"
  # 1) it must compile and the agent must import
  if ! python3 -m py_compile team13/*.py agent/*.py dashboard/*.py 2>>"$LOG"; then say "SKIP $NEW: does not compile"; echo "$NEW" > /home/bazaar/rejected.sha; exit 0; fi
  if ! (cd team13 && /home/bazaar/venv/bin/python -c "import agent, duels, trader, market, guard" ) 2>>"$LOG"; then say "SKIP $NEW: agent import fails"; echo "$NEW" > /home/bazaar/rejected.sha; exit 0; fi
  # 2) keep the running code for a rollback, then copy the new code (server-only files untouched)
  rsync -a --delete "$APP/team13/" /home/bazaar/app.prev/team13/ --exclude logs/ 2>/dev/null; rsync -a "$APP/agent/" /home/bazaar/app.prev/agent/; rsync -a "$APP/dashboard/" /home/bazaar/app.prev/dashboard/
  rsync -a --exclude '__pycache__' --exclude 'logs/' --exclude 'state.json' --exclude 'strategy.json' --exclude '*.env' "$SRC/team13" "$SRC/agent" "$SRC/dashboard" "$SRC/data" "$APP/"
  chown -R bazaar:bazaar "$APP" /home/bazaar/app.prev
  systemctl restart bazaar-agent bazaar-dashboard
  systemctl is-enabled -q bazaar-duel-tuner 2>/dev/null && systemctl restart bazaar-duel-tuner
  sleep 25
  if ! systemctl is-active -q bazaar-agent || journalctl -u bazaar-agent --since "-25s" --no-pager | grep -q "Failed with result"; then
    rsync -a /home/bazaar/app.prev/team13/ "$APP/team13/" --exclude logs/ --exclude state.json --exclude strategy.json; rsync -a /home/bazaar/app.prev/agent/ "$APP/agent/"; rsync -a /home/bazaar/app.prev/dashboard/ "$APP/dashboard/"
    systemctl restart bazaar-agent bazaar-dashboard; say "ROLLBACK from $NEW: agent did not stay up"; echo "$NEW" > /home/bazaar/rejected.sha; exit 0
  fi
  echo "$NEW" > /home/bazaar/deployed.sha; say "DEPLOYED $NEW ($(git log -1 --format='%an: %s' | cut -c1-90))"
  touch /home/bazaar/broker.pending
fi
# 3) broker: copy and restart only outside a Market Test (no bench book in the last 2 minutes)
if [ -f /home/bazaar/broker.pending ]; then
  RECENT=$(tail -n 400 "$BRK/logs/broker.jsonl" 2>/dev/null | python3 -c "
import sys,json,time
t=0
for l in sys.stdin:
    try: r=json.loads(l)
    except: continue
    if r.get('event') in ('bench_book','book') and r.get('ts'): t=max(t,r['ts'])
print(1 if time.time()-t<120 else 0)")
  if [ "$RECENT" = 1 ]; then say "broker update deferred: Market Test running"; exit 0; fi
  if ! cmp -s "$SRC/team13/smart_broker.py" "$BRK/smart_broker.py" || ! cmp -s "$SRC/team13/starter_plans.py" "$BRK/starter_plans.py" || ! cmp -s "$SRC/team13/bazaar_sdk.py" "$BRK/bazaar_sdk.py"; then
    cp "$SRC/team13/smart_broker.py" "$SRC/team13/starter_plans.py" "$SRC/team13/bazaar_sdk.py" "$BRK/"; chown bazaar:bazaar "$BRK"/*.py; systemctl restart bazaar-broker; say "broker updated and restarted"
  fi
  rm -f /home/bazaar/broker.pending
fi
