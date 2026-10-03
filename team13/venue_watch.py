"""Read-only watcher for our venue's name and fee: prints one AGENT_LOOP_WAKE_venue line per decision point.

    source ../bazaar.env && ../.venv/bin/python -u venue_watch.py

Light on the API: venues + schedule most cycles; /api/me only when cash might unlock a rename.
On 429, backs off. Never writes to the API, state.json or strategy.json.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
URL, KEY = os.environ["BAZAAR_URL"], os.environ["BAZAAR_KEY"]
POLL = 300                   # 5 min steady state
POLL_429 = 900               # 15 min after a rate limit
HEARTBEAT = 3600
ME_EVERY = 3                 # hit /api/me every N successful cycles
REOPEN_CASH = 290
RENAME_MIN_GAP_HOURS = 0.75
BENCH_HOURS = 0.2
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
    cash, me_cycle, sleep_for = 0, 0, POLL
    while True:
        try:
            ours_id = json.loads((HERE / "state.json").read_text()).get("venue")
            want_fee = int(json.loads((HERE / "strategy.json").read_text()).get("venue_fee_bps", 0))
            venues = get("/api/venues")["venues"]
            sched = get("/api/schedule")
            me_cycle += 1
            if me_cycle >= ME_EVERY or cash < REOPEN_CASH:
                cash = int(get("/api/me", auth=True).get("cash") or 0)
                me_cycle = 0
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
                conds["rename_window"] = (ours["name"] != TARGET_NAME and cash >= REOPEN_CASH
                                          and (eta is None or eta >= RENAME_MIN_GAP_HOURS),
                                          {"cash": cash, "eta_h": eta, "name": ours["name"]})

            if benches and (last_bench is None or last_bench > now):
                last_bench = float(benches[0]["at_hours"])
            done = last_bench is not None and now >= last_bench + BENCH_HOURS
            conds["test_over"] = (done, {"bench_at": last_bench, "cash": cash})
            if done:
                last_bench = None

            for name, (on, detail) in conds.items():
                if on and name not in fired:
                    fired.add(name)
                    emit(name, **detail)
                elif not on:
                    fired.discard(name)

            snap = {v["venue"]: (v["status"], v["fee_bps"], v.get("fee_per_card")) for v in venues
                    if v["venue"] != ours_id and not v.get("starter")}
            if rivals is not None and snap != rivals:
                emit("rivals", changes={k: snap.get(k) for k in set(snap) | set(rivals) if snap.get(k) != rivals.get(k)})
            rivals = snap

            if time.time() - last_beat >= HEARTBEAT:
                last_beat = time.time()
                emit("heartbeat", cash=cash, eta_h=eta, fee=ours and ours["fee_bps"], name=ours and ours["name"])
            sleep_for = POLL
        except urllib.error.HTTPError as e:
            sleep_for = POLL_429 if e.code == 429 else POLL
            print(f"venue_watch error: {e} (sleep {sleep_for}s)", flush=True)
        except Exception as e:
            sleep_for = POLL
            print(f"venue_watch error: {e}", flush=True)
        time.sleep(sleep_for)


if __name__ == "__main__":
    main()
