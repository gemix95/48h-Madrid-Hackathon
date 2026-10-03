"""Read-only watcher for our venue's name and fee: prints one AGENT_LOOP_WAKE_venue line per decision point.

    source ../bazaar.env && ../.venv/bin/python -u venue_watch.py

Events (each fires once until its condition clears):
  rename_window   cash covers a new bond + opening fee and the next Market Test is far enough away
  test_over       a Market Test just finished (best moment to close / reopen or change the fee)
  fee_before_test a Market Test is near and our live fee is not 0%
  fee_drift       our live fee differs from strategy.json's venue_fee_bps
  pending_fee     a fee change is waiting out its notice
  venue_status    our venue is not open or carries a suspension reason
  rivals          a rival venue opened, closed or changed its fee
Never writes to the API, state.json or strategy.json.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
URL, KEY = os.environ["BAZAAR_URL"], os.environ["BAZAAR_KEY"]
POLL = 60
HEARTBEAT = 1800
REOPEN_CASH = 290            # 250 bond + 20 opening fee + margin
RENAME_MIN_GAP_HOURS = 0.75  # game hours to the next Market Test needed to close, reopen and settle the broker
BENCH_HOURS = 0.2            # a Market Test (16 ticks) is over this long after it starts
TARGET_NAME = "El Club · Where Madrid Trades"


def get(path, auth=False):
    req = urllib.request.Request(URL + path, headers={"X-Team-Key": KEY} if auth else {})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def emit(event, **detail):
    print("AGENT_LOOP_WAKE_venue " + json.dumps({"event": event, **detail,
          "prompt": "Venue watcher fired: decide whether to change our market's name or fee now."}), flush=True)


def main():
    fired, rivals, last_beat, last_bench = set(), None, time.time(), None
    while True:
        try:
            ours_id = json.loads((HERE / "state.json").read_text()).get("venue")
            want_fee = int(json.loads((HERE / "strategy.json").read_text()).get("venue_fee_bps", 0))
            venues = get("/api/venues")["venues"]
            me = get("/api/me", auth=True)
            sched = get("/api/schedule")
            now = float(sched.get("now_hours") or 0)
            benches = [u for u in sched.get("upcoming", []) if u.get("action") == "bench"]
            eta = float(benches[0]["at_hours"]) - now if benches else None
            ours = next((v for v in venues if v["venue"] == ours_id), None)
            conds = {}

            if ours:
                conds["venue_status"] = (ours["status"] != "open" or bool(ours.get("suspension_reason")),
                                         {"status": ours["status"], "reason": ours.get("suspension_reason")})
                conds["pending_fee"] = (ours.get("pending_fee") is not None, {"pending": ours.get("pending_fee")})
                conds["fee_drift"] = (int(ours["fee_bps"]) != want_fee, {"live": ours["fee_bps"], "strategy": want_fee})
                conds["fee_before_test"] = (eta is not None and eta <= 0.5 and int(ours["fee_bps"]) > 0,
                                            {"eta_h": eta, "live": ours["fee_bps"]})
                conds["rename_window"] = (ours["name"] != TARGET_NAME and me.get("cash", 0) >= REOPEN_CASH
                                          and (eta is None or eta >= RENAME_MIN_GAP_HOURS),
                                          {"cash": me.get("cash"), "eta_h": eta, "name": ours["name"]})

            if benches and (last_bench is None or last_bench > now):  # a started test may drop off "upcoming"
                last_bench = float(benches[0]["at_hours"])
            done = last_bench is not None and now >= last_bench + BENCH_HOURS
            conds["test_over"] = (done, {"bench_at": last_bench, "cash": me.get("cash")})
            if done:
                last_bench = None

            for name, (on, detail) in conds.items():
                if on and name not in fired:
                    fired.add(name)
                    emit(name, **detail)
                elif not on:
                    fired.discard(name)

            snap = {v["venue"]: (v["status"], v["fee_bps"], v.get("fee_per_card")) for v in venues if v["venue"] != ours_id}
            if rivals is not None and snap != rivals:
                emit("rivals", changes={k: snap.get(k) for k in set(snap) | set(rivals) if snap.get(k) != rivals.get(k)})
            rivals = snap

            if time.time() - last_beat >= HEARTBEAT:
                last_beat = time.time()
                emit("heartbeat", cash=me.get("cash"), eta_h=eta, fee=ours and ours["fee_bps"], name=ours and ours["name"])
        except Exception as e:  # keep watching through network blips
            print(f"venue_watch error: {e}", flush=True)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
