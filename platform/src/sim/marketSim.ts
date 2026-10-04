/**
 * Monte Carlo Market Test, same model as team13/tests/sim_broker.py: traders arrive over the session with hidden
 * limits, shade their quotes 5–25% away from them, relax toward them as patience runs out (30% never relax), and
 * leave when patience ends. Efficiency = realised gains between TRUE limits / the most gains possible.
 */
import { hybridPlan, maxCrossPlan, stallPlan, type Match, type Quote } from "../agents/marketBrain.js";

export type Strategy = "stall" | "maxcross" | "hybrid" | "oracle";

function rng(seed: number) {
  let s = seed >>> 0 || 1;
  return () => {
    s ^= s << 13;
    s ^= s >>> 17;
    s ^= s << 5;
    return ((s >>> 0) % 1_000_000) / 1_000_000;
  };
}

interface Trader { id: string; side: "ask" | "bid"; lim: number; shade: number; arr: number; pat: number; firm: boolean; alive: boolean; q: number }

export function simulate(seed: number, strategy: Strategy, opts: { n?: number; ticks?: number; arrivals?: boolean; hard?: boolean } = {}): number {
  const { n = 10, ticks = 16, arrivals = true, hard = false } = opts;
  const r = rng(seed * 7919 + 13);
  const tr: Trader[] = [];
  for (let i = 0; i < n; i++) {
    const arr = arrivals ? Math.floor(r() * 9) : 0;
    tr.push({
      id: `b1-${i}`, side: i % 2 === 0 ? "ask" : "bid", lim: 20 + r() * 60, shade: 0.05 + r() * 0.2, arr,
      pat: arr + (hard ? 2 : 3) + Math.floor(r() * (hard ? 5 : 8)), firm: r() < (hard ? 0.5 : 0.3), alive: true, q: 0,
    });
  }
  const costs = tr.filter((t) => t.side === "ask").map((t) => t.lim).sort((a, b) => a - b);
  const vals = tr.filter((t) => t.side === "bid").map((t) => t.lim).sort((a, b) => b - a);
  let best = 0;
  for (let i = 0; i < Math.min(costs.length, vals.length); i++) best += Math.max(0, vals[i] - costs[i]);
  if (best <= 0) return 1;
  const byId = new Map(tr.map((t) => [t.id, t]));
  let got = 0;
  for (let tick = 0; tick < ticks; tick++) {
    const quotes: Quote[] = [];
    for (const t of tr) {
      if (!t.alive || tick < t.arr) continue;
      if (tick >= t.pat) {
        t.alive = false;
        continue;
      }
      const relax = t.firm ? 0 : (tick - t.arr) / Math.max(1, t.pat - t.arr);
      const sh = t.shade * (1 - relax);
      t.q = Math.round(t.side === "ask" ? t.lim * (1 + sh) : t.lim * (1 - sh));
      quotes.push({ id: t.id, side: t.side, price: t.q, run: "b1" });
    }
    let plan: Match[];
    if (strategy === "stall") plan = stallPlan(quotes);
    else if (strategy === "maxcross") plan = maxCrossPlan(quotes);
    else if (strategy === "hybrid") {
      const leaving = new Set(tr.filter((t) => t.alive && t.pat - tick <= 1).map((t) => t.id));
      plan = hybridPlan(quotes, tick / ticks, leaving);
    }
    else plan = oraclePlan(quotes, byId, tick);
    for (const m of plan) {
      const s = byId.get(m.sell)!, b = byId.get(m.buy)!;
      if (s.alive && b.alive) {
        got += b.lim - s.lim;
        s.alive = b.alive = false;
      }
    }
  }
  return got / best;
}

/** Knows the true limits: the most gains any broker could take from these quotes, tick by tick. */
function oraclePlan(quotes: Quote[], byId: Map<string, Trader>, tick: number): Match[] {
  const A = quotes.filter((q) => q.side === "ask").sort((a, b) => byId.get(a.id)!.lim - byId.get(b.id)!.lim);
  const B = quotes.filter((q) => q.side === "bid").sort((a, b) => byId.get(b.id)!.lim - byId.get(a.id)!.lim);
  const used = new Set<string>();
  const plan: Match[] = [];
  for (const b of B) {
    for (const a of A) {
      if (used.has(a.id) || used.has(b.id) || b.price < a.price) continue;
      plan.push({ sell: a.id, buy: b.id, price: a.price });
      used.add(a.id).add(b.id);
      break;
    }
  }
  void tick;
  return plan;
}

export function compare(N = 2000, opts: { arrivals?: boolean; hard?: boolean } = {}) {
  const out: Record<Strategy, number> = { stall: 0, maxcross: 0, hybrid: 0, oracle: 0 };
  for (const s of ["stall", "maxcross", "hybrid", "oracle"] as Strategy[]) {
    let sum = 0;
    for (let i = 0; i < N; i++) sum += simulate(i, s, opts);
    out[s] = sum / N;
  }
  return out;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  for (const hard of [false, true]) {
    for (const arrivals of [false, true]) {
      const r = compare(3000, { arrivals, hard });
      console.log(`${hard ? "hard  " : "normal"} arrivals=${String(arrivals).padEnd(5)} stall ${r.stall.toFixed(3)}  maxcross ${r.maxcross.toFixed(3)}  hybrid ${r.hybrid.toFixed(3)}  oracle ${r.oracle.toFixed(3)}`);
    }
  }
}
