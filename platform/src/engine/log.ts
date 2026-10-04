import { appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

export type AgentId = "deal" | "duel" | "market";
export type Mode = "off" | "shadow" | "live";

/** One thing an agent decided, written for a human: a title, the reason, and the numbers behind it. */
export interface Decision {
  id: number;
  ts: number;
  tick: number;
  agent: AgentId;
  kind: string;
  title: string;
  why: string;
  numbers?: Record<string, number | string | null>;
  /** Expected points (or P of value) this decision is worth, when we can say. */
  worth?: number;
  mode: Mode;
  outcome: "done" | "shadow" | "refused" | "skipped" | "info";
  error?: string;
}

export class DecisionLog {
  private items: Decision[] = [];
  private seq = 0;
  constructor(private file: string | null, private cap = 800) {
    if (file) mkdirSync(dirname(file), { recursive: true });
  }

  add(d: Omit<Decision, "id" | "ts">): Decision {
    const full: Decision = { ...d, id: ++this.seq, ts: Date.now() };
    this.items.push(full);
    if (this.items.length > this.cap) this.items.splice(0, this.items.length - this.cap);
    if (this.file) {
      try {
        appendFileSync(this.file, JSON.stringify(full) + "\n");
      } catch {
        /* a full disk must not stop the agents */
      }
    }
    return full;
  }

  recent(n = 200, agent?: AgentId): Decision[] {
    const xs = agent ? this.items.filter((d) => d.agent === agent) : this.items;
    return xs.slice(-n).reverse();
  }
}
