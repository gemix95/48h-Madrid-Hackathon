"""Queue a loan request for the agent's loan desk (team13/loans.py). Run it on the server:

    ssh -i ~/.ssh/bazaar_vps root@217.160.143.83 \
        "sudo -u bazaar python3 /home/bazaar/app/agent/lend.py t07 LAV-09 60 70 120"

    args: borrower card principal repay term_ticks

The agent checks every risk rule on its next tick (collateral worth >= principal + 10% to us, interest >= 10% and
>= 2 P, one loan per team, total cap, cash reserve) and logs "loan offered" or "loan rejected" with the reason.
"""
import json
import os
import sys

REQ = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "team13", "logs", "loan_requests.jsonl")

if len(sys.argv) not in (5, 6):
    print(__doc__)
    sys.exit(2)
team, ref, principal, repay = sys.argv[1], sys.argv[2].upper(), int(sys.argv[3]), int(sys.argv[4])
term = int(sys.argv[5]) if len(sys.argv) == 6 else 120
with open(REQ, "a") as f:
    f.write(json.dumps({"team": team, "ref": ref, "principal": principal, "repay": repay, "term": term}) + "\n")
print(f"queued: lend {principal} P to {team} against {ref}, repay {repay} P within {term} ticks")
