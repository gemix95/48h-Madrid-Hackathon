import { BazaarError, type Bazaar, type Broker } from "../sdk/bazaar.js";
import type { World } from "../engine/world.js";
import type { AgentId, Decision, DecisionLog, Mode } from "../engine/log.js";

export interface Ctx {
  world: World;
  api: Bazaar;
  broker: Broker | null;
  log: DecisionLog;
  mode: (a: AgentId) => Mode;
  /** The team's one accept per tick (shared with every other process using our key). */
  takeAccept: () => boolean;
}

export interface Agent {
  id: AgentId;
  name: string;
  role: string;
  step(ctx: Ctx): Promise<void>;
  status(ctx: Ctx): Record<string, unknown>;
}

type Draft = Pick<Decision, "kind" | "title" | "why"> & Partial<Pick<Decision, "numbers" | "worth">>;

const lastSeen = new Map<string, number>();

/**
 * Record a decision and, in live mode, carry it out. The same decision repeated within `quietTicks` is not
 * logged again, so the feed shows changes, not noise.
 */
export async function decide(ctx: Ctx, agent: AgentId, d: Draft, action?: () => Promise<unknown>, quietTicks = 4): Promise<boolean> {
  const mode = ctx.mode(agent);
  if (mode === "off") return false;
  const tick = ctx.world.tick;
  const key = `${agent}|${d.kind}|${d.title}`;
  const seen = lastSeen.get(key);
  const quiet = seen != null && tick - seen < quietTicks;
  if (!action) {
    if (!quiet) {
      lastSeen.set(key, tick);
      ctx.log.add({ ...d, agent, tick, mode, outcome: "info" });
    }
    return false;
  }
  if (mode === "shadow") {
    if (!quiet) {
      lastSeen.set(key, tick);
      ctx.log.add({ ...d, agent, tick, mode, outcome: "shadow" });
    }
    return false;
  }
  lastSeen.set(key, tick);
  try {
    await action();
    ctx.log.add({ ...d, agent, tick, mode, outcome: "done" });
    return true;
  } catch (e) {
    const err = e instanceof BazaarError ? `${e.code}${e.message ? ": " + e.message : ""}` : String(e);
    ctx.log.add({ ...d, agent, tick, mode, outcome: "refused", error: err.slice(0, 240) });
    return false;
  }
}
