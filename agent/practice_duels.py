"""Play the practice duels (session 1, not scored) with team13/duels.py from this machine, then stop.
The Host's agent is on old code that crashes on duels, so there is no second writer in duels. Practice only."""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "team13"))
from bazaar_sdk import Bazaar, BazaarError  # noqa: E402  (team13's copy of the SDK)
import strategy  # noqa: E402
from duels import Duels  # noqa: E402

LOG = os.path.join(os.path.dirname(__file__), "..", "logs", "practice_duels.jsonl")
api = Bazaar(os.environ["BAZAAR_URL"], os.environ["BAZAAR_KEY"], wait_on_tick=False)


class Ctx:
    def __init__(self):
        self.api = self.raw = api
        self.me = api.me()
        self.state, self.clock = {}, {}
        self.S = strategy.load()

    def log(self, area, ev, **k):
        with open(LOG, "a") as f:
            f.write(json.dumps({"ts": time.time(), "tick": self.clock.get("tick"), "area": area, "ev": ev, **k}, default=str) + "\n")
        print(area, ev, {x: k[x] for x in k if x in ("duel", "role", "limit", "price", "rival_price", "k", "error", "our_surplus")}, flush=True)

    def take_accept(self, kind="team"):
        return True


ctx, last = Ctx(), None
D = Duels(ctx)
once = "--once" in sys.argv
while True:
    ctx.clock = api.clock()
    live = [d for d in api.duels().get("duels", []) if d.get("session") == 1 and d.get("status") == "live"]
    if not live:
        print("no live practice duels: stop", flush=True)
        break
    if ctx.clock["tick"] != last:
        last = ctx.clock["tick"]
        ctx.S = strategy.load()
        D.step()
        if once:
            break
    time.sleep(3)
