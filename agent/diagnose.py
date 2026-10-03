"""Diagnose a day from the Host's logs: where team-trade value was lost, and how the broker did in each Market Test.

Run on the Host (where the agent writes its logs), no API calls:

    python3 agent/diagnose.py                       # since 09:00 today
    python3 agent/diagnose.py --since "2026-10-03 09:00"

Reads team13/logs/decisions.jsonl (the agent) and team13/logs/broker.jsonl (the broker thread).
"""
import argparse
import collections
import datetime as dt
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "..", "team13", "logs")


def rows(name, since):
    path = os.path.join(LOGS, name)
    if not os.path.exists(path):
        print(f"(no {path})")
        return []
    out = []
    with open(path) as f:
        for line in f:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("ts", 0) >= since:
                out.append(r)
    return out


def trades(dec):
    print("\n== Trades with teams and value-changing actions (agent decisions) ==")
    keys = {("trade", "accept"), ("trade", "accept_team"), ("flip", "buy"), ("flip", "sell"), ("wtb", "asked"),
            ("market", "club_reward"), ("trade", "list_ask"), ("trade", "list_bid"), ("guard", "cancelled"),
            ("trade", "skip_insolvent")}
    counts = collections.Counter()
    for r in dec:
        k = (r.get("module"), r.get("action"))
        if k not in keys:
            continue
        counts[k] += 1
        t = dt.datetime.fromtimestamp(r["ts"]).strftime("%H:%M")
        gain = r.get("gain") if r.get("gain") is not None else r.get("expected") or r.get("profit")
        detail = r.get("detail") or {}
        what = (r.get("ref") or ",".join(detail.get("give") or []) + (" for " + ",".join(detail.get("want") or [])
                if detail.get("want") else "") or r.get("offer"))
        flag = "  <-- NEGATIVE" if isinstance(gain, (int, float)) and gain < 0 else ""
        if k in {("trade", "list_ask"), ("trade", "list_bid")}:
            continue  # counted only
        print(f"  {t} t{r.get('tick')} {k[0]}/{k[1]} {what} gain={gain} price={r.get('price')} "
              f"loss={r.get('our_loss') or r.get('loss')}{flag}")
    print("  counts:", dict(counts))


def broker(brk):
    print("\n== Broker (Market Tests and our venue) ==")
    ev = collections.Counter(r.get("event") for r in brk)
    print("  events:", dict(ev))
    by_err = collections.Counter(str(r.get("error"))[:80] for r in brk if r.get("event") in ("match_refused", "probe_refused"))
    if by_err:
        print("  refusals by reason:")
        for e, n in by_err.most_common(8):
            print(f"    {n:4}  {e}")
    for name in ("fee_blocked", "smart_plan_failed", "read_failed", "session_ticks", "probe_disabled"):
        hits = [r for r in brk if r.get("event") == name]
        if hits:
            r = hits[-1]
            print(f"  last {name}: {json.dumps({k: v for k, v in r.items() if k != 'ts'}, default=str)[:220]}")
    matches = [r for r in brk if r.get("event") in ("match", "probe_match")]
    bench = [r for r in matches if str(r.get("sell", "")).startswith("b")]
    print(f"  matches: {len(matches)} (bench {len(bench)}, public {len(matches) - len(bench)})")
    books = [r for r in brk if r.get("event") in ("book", "bench_book")]
    if books:
        print(f"  bench books seen: {len(books)}; first at {dt.datetime.fromtimestamp(books[0]['ts']):%H:%M}, "
              f"last at {dt.datetime.fromtimestamp(books[-1]['ts']):%H:%M}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=dt.date.today().isoformat() + " 09:00")
    a = ap.parse_args()
    since = dt.datetime.fromisoformat(a.since).timestamp()
    dec, brk = rows("decisions.jsonl", since), rows("broker.jsonl", since)
    print(f"since {a.since}: {len(dec)} agent decisions, {len(brk)} broker records")
    trades(dec)
    broker(brk)


if __name__ == "__main__":
    main()
