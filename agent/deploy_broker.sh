#!/bin/sh
# Copy the broker's three files to our server and restart it if it runs. Key: ~/.ssh/bazaar_vps.
#   sh agent/deploy_broker.sh
set -e
H=root@217.160.143.83; K="$HOME/.ssh/bazaar_vps"; cd "$(dirname "$0")/../team13"
scp -q -i "$K" smart_broker.py starter_plans.py bazaar_sdk.py "$H:/home/bazaar/broker/"
ssh -i "$K" "$H" 'chown bazaar:bazaar /home/bazaar/broker/*.py; systemctl is-active -q bazaar-broker && systemctl restart bazaar-broker; echo "broker: $(systemctl is-active bazaar-broker)"'
