import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Bazaar, Broker, sleep } from "../sdk/bazaar.js";
import { World } from "./world.js";
import { DecisionLog, type AgentId, type Mode } from "./log.js";
import type { Agent, Ctx } from "../agents/base.js";
import { DealAgent, setPackSlots } from "../agents/dealAgent.js";
import { DuelAgent } from "../agents/duelAgent.js";
import { MarketAgent } from "../agents/marketAgent.js";

export interface RunnerConfig {
  url: string;
  key: string;
  brokerKey: string | null;
  dataDir: string;
  reservedFile: string | null;
  /** "any", "odd" or "even": which ticks this process may use the team's one accept (the Python agents use slots). */
  acceptParity: "any" | "odd" | "even";
  initialModes: Partial<Record<AgentId, Mode>>;
}

export class Runner {
  world: World;
  log: DecisionLog;
  api: Bazaar;
  broker: Broker | null;
  agents: Agent[];
  modes: Record<AgentId, Mode>;
  private acceptedTick = -1;
  private modesFile: string;
  started = Date.now();
  loops = 0;

  constructor(public cfg: RunnerConfig) {
    this.api = new Bazaar(cfg.url, cfg.key);
    this.broker = cfg.brokerKey ? new Broker(cfg.url, cfg.brokerKey) : null;
    this.world = new World(this.api, cfg.dataDir, cfg.reservedFile);
    this.log = new DecisionLog(join(cfg.dataDir, "decisions.jsonl"));
    this.agents = [new DealAgent(), new DuelAgent(), new MarketAgent()];
    this.modesFile = join(cfg.dataDir, "modes.json");
    let saved: Partial<Record<AgentId, Mode>> = {};
    try {
      saved = JSON.parse(readFileSync(this.modesFile, "utf8"));
    } catch {
      /* first run */
    }
    this.modes = { deal: "shadow", duel: "shadow", market: "shadow", ...saved, ...cfg.initialModes };
    if (!cfg.key) for (const a of Object.keys(this.modes) as AgentId[]) if (this.modes[a] === "live") this.modes[a] = "shadow";
  }

  setMode(a: AgentId, m: Mode) {
    this.modes[a] = m;
    try {
      writeFileSync(this.modesFile, JSON.stringify(this.modes));
    } catch {
      /* best effort */
    }
    this.log.add({ agent: a, tick: this.world.tick, kind: "mode", title: `${a} agent switched to ${m.toUpperCase()}`, why: m === "live" ? "it now acts with the team key" : m === "shadow" ? "it decides and explains, but does not act" : "it is idle", mode: m, outcome: "info" });
  }

  ctx(): Ctx {
    return {
      world: this.world,
      api: this.api,
      broker: this.broker,
      log: this.log,
      mode: (a) => this.modes[a],
      takeAccept: () => {
        const t = this.world.tick;
        if (this.acceptedTick === t) return false;
        if (this.cfg.acceptParity === "odd" && t % 2 === 0) return false;
        if (this.cfg.acceptParity === "even" && t % 2 === 1) return false;
        this.acceptedTick = t;
        return true;
      },
    };
  }

  async loop() {
    const ctx = this.ctx();
    for (;;) {
      this.loops++;
      try {
        await this.world.refresh(!!this.cfg.key);
        if (this.world.catalog) setPackSlots(this.world.catalog.packs);
        for (const a of this.agents) {
          if (this.modes[a.id] === "off") continue;
          try {
            await a.step(ctx);
          } catch (e) {
            this.log.add({ agent: a.id, tick: this.world.tick, kind: "error", title: `${a.name} hit an error`, why: String(e).slice(0, 300), mode: this.modes[a.id], outcome: "refused" });
          }
        }
      } catch (e) {
        this.world.errors.push({ ts: Date.now(), what: "loop", error: String(e).slice(0, 200) });
      }
      await sleep(this.world.open ? 1200 : 5000);
    }
  }
}
