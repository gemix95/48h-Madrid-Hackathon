"""Print the war room's state once a minute: score, rank, cash, new decisions and errors (python3 scripts/watch.py)."""
import json
import sys
import time
import urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8787/api/snapshot"
seen = set()
while True:
    try:
        d = json.load(urllib.request.urlopen(URL, timeout=20))
        st, c = d["story"], d["clock"]
        lead = st.get("leader") or {}
        print(f"{time.strftime('%H:%M:%S')} tick {c.get('tick')} {'open' if not c.get('paused') else 'paused'} | "
              f"#{st['rank']} {st['score']} (leader {lead.get('name')} {lead.get('score')}) | cash {d['me']['cash']} | "
              f"modes {','.join(a['id'] + '=' + a['mode'] for a in d['agents'])}", flush=True)
        for x in reversed(d["decisions"]):
            if x["id"] in seen:
                continue
            seen.add(x["id"])
            if x["outcome"] in ("done", "refused") or x["agent"] == "deal":
                print(f"    t{x['tick']} {x['agent']} {x['outcome']}: {x['title']} | {x['why'][:160]}"
                      + (f" | ERROR {x['error']}" if x.get("error") else ""), flush=True)
        for e in d["health"]["errors"][-3:]:
            if e["ts"] / 1000 > time.time() - 60:
                print(f"    api error {e['what']}: {e['error']}", flush=True)
    except Exception as e:  # keep watching through restarts
        print(f"{time.strftime('%H:%M:%S')} watch: {e}", flush=True)
    time.sleep(60)
