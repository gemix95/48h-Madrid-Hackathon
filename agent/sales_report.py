"""Dealer sales: cash raised and value kept. Run where the agent writes its log (the server), no API calls:

    python3 agent/sales_report.py                 # today
    python3 agent/sales_report.py --since-tick 850

Every sale since the hard floor (haggler: never below what the card is worth to us + sell_min_gain) logs its cost
and net; a sale with net < 0 is a bug. Older sales have no cost and show "?".
"""
import argparse
import json
import os

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "team13", "logs", "decisions.jsonl")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-tick", type=int, default=0)
    a = ap.parse_args()
    sales, skipped, raised = [], 0, 0
    with open(LOG) as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("module") != "haggle" or (r.get("tick") or 0) < a.since_tick:
                continue
            if r.get("action") == "deal" and ":sell:" in str(r.get("key")):
                sales.append(r)
            skipped += r.get("action") == "sale_skipped_below_value"
            raised += r.get("action") == "sell_floor_raised"
    print(f"{'tick':>5}  {'dealer':8} {'card':7} {'price':>5} {'cost':>6} {'net':>6}")
    for r in sales:
        net = r.get("net")
        flag = "  <-- BELOW VALUE" if net is not None and net < 0 else ""
        print(f"{r.get('tick'):>5}  {str(r.get('key')).split(':')[0]:8} {str(r.get('ref') or '?'):7} {r.get('price'):>5} "
              f"{str(r.get('cost', '?')):>6} {str(net if net is not None else '?'):>6}{flag}")
    known = [r for r in sales if r.get("net") is not None]
    print(f"\nsales {len(sales)} | cash raised {sum(r.get('price') or 0 for r in sales)} P | net over our value "
          f"{sum(r['net'] for r in known):+.1f} P ({len(known)} with cost) | below value: {sum(1 for r in known if r['net'] < 0)}")
    print(f"sales skipped because the dealer would pay less than the card is worth to us: {skipped} | floors raised: {raised}")


if __name__ == "__main__":
    main()
