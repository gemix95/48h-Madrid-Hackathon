import type { Agent, Ctx } from "./base.js";
import { decide } from "./base.js";
import { decideDuel, duelMessage, DEFAULT_DUEL_KNOBS } from "./duelBrain.js";

export class DuelAgent implements Agent {
  id = "duel" as const;
  name = "Duel agent";
  role = "Plays every 1-vs-1 duel: picks the delivery day that grows the pie, anchors, concedes on a clock, accepts when waiting costs more than it wins.";
  knobs = { ...DEFAULT_DUEL_KNOBS };
  private lastTick = -1;

  async step(ctx: Ctx) {
    const w = ctx.world;
    if (!w.open || w.tick === this.lastTick) return;
    this.lastTick = w.tick;
    const live = w.duels.filter((d) => d.status === "open" || d.status === "live" || d.status === "active");
    for (const d of live) {
      const a = decideDuel(d, w.tick, this.knobs);
      const head = `${d.role === "seller" ? "Selling" : "Buying"} “${d.item}” vs ${d.rival}`;
      const numbers = { limit: d.your_limit, days_weight: d.your_days_weight, rival_price: d.rival_offer?.price ?? null, rival_days: d.rival_offer?.days ?? null, ticks_left: d.deadline_tick - w.tick };
      if (a.kind === "accept") {
        await decide(ctx, "duel", { kind: "accept", title: `${head}: accept`, why: a.why, numbers, worth: a.surplus }, () => ctx.api.duelAccept(d.duel), 1);
      } else if (a.kind === "offer") {
        const k = d.messages.filter((m) => m.from === "you").length;
        await decide(ctx, "duel", { kind: "offer", title: `${head}: offer ${a.price} P${a.days != null ? ` · day ${a.days}` : ""}`, why: a.why, numbers, worth: a.surplus },
          () => ctx.api.duelSay(d.duel, duelMessage(a, k), a.price, a.days), 1);
      } else {
        await decide(ctx, "duel", { kind: "wait", title: `${head}: wait`, why: a.why, numbers });
      }
    }
  }

  status(ctx: Ctx) {
    const w = ctx.world;
    const live = w.duels.filter((d) => d.status === "open");
    return { live: live.length, finished: w.duelsDone.length };
  }
}
