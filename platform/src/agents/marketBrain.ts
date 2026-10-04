/**
 * Market Test brain. Every two game hours every venue gets the same synthetic book: buyers with hidden values,
 * sellers with hidden costs, both quoting away from their limits and relaxing as patience runs out. A broker scores
 * realised gains / possible gains. The free auto stall is the half-points floor.
 *
 * The stall pairs the highest bid with the lowest ask, the second highest with the second lowest, and stops at the
 * first pair that does not cross. That wastes traders: bids 10, 3 and asks 2, 9 give the stall one pair (10↔2) and
 * strand 3 and 9. Pairing the k highest bids with the k lowest asks in REVERSE order (10↔9, 3↔2) clears both.
 * `maxCross` finds the largest k that pairs that way, which is the most gains the current quotes allow.
 */
import type { BenchOffer, Offer } from "../sdk/types.js";

export interface Quote {
  id: string;
  side: "ask" | "bid";
  price: number;
  run: string;
}

export interface Match {
  sell: string;
  buy: string;
  price: number;
}

export function benchQuotes(bench: BenchOffer[]): Quote[] {
  const out: Quote[] = [];
  for (const o of bench) {
    const run = String(o.id).split("-")[0];
    if (o.want?.cash) out.push({ id: o.id, side: "ask", price: o.want.cash, run });
    else if (o.give?.cash) out.push({ id: o.id, side: "bid", price: o.give.cash, run });
  }
  return out;
}

/** Highest price ≤ midpoint at which the buyer still covers the fee; null if none. */
export function priceWithFee(ask: number, bid: number, fee: (p: number) => number): number | null {
  if (bid < ask) return null;
  for (let p = Math.floor((ask + bid) / 2); p >= ask; p--) if (p + fee(p) <= bid) return p;
  return null;
}

function byRun(quotes: Quote[]) {
  const runs = new Map<string, { asks: Quote[]; bids: Quote[] }>();
  for (const q of quotes) {
    const r = runs.get(q.run) ?? { asks: [], bids: [] };
    (q.side === "ask" ? r.asks : r.bids).push(q);
    runs.set(q.run, r);
  }
  return runs;
}

/** The free stall's plan, verbatim: best bid with best ask, in order, until a pair does not cross. */
export function stallPlan(quotes: Quote[], fee: (p: number) => number = () => 0): Match[] {
  const plan: Match[] = [];
  for (const { asks, bids } of byRun(quotes).values()) {
    const A = [...asks].sort((a, b) => a.price - b.price);
    const B = [...bids].sort((a, b) => b.price - a.price);
    for (let i = 0; i < Math.min(A.length, B.length); i++) {
      if (B[i].price < A[i].price) break;
      const p = priceWithFee(A[i].price, B[i].price, fee);
      if (p != null) plan.push({ sell: A[i].id, buy: B[i].id, price: p });
    }
  }
  return plan;
}

/** The most pairs the quotes allow: the k lowest asks against the k highest bids, paired in reverse order. */
export function maxCrossPlan(quotes: Quote[], fee: (p: number) => number = () => 0): Match[] {
  const plan: Match[] = [];
  for (const { asks, bids } of byRun(quotes).values()) {
    const A = [...asks].sort((a, b) => a.price - b.price);
    const B = [...bids].sort((a, b) => b.price - a.price);
    const feasible = (k: number) => {
      for (let i = 0; i < k; i++) if (priceWithFee(A[k - 1 - i].price, B[i].price, fee) == null) return false;
      return true;
    };
    let best = 0;
    for (let k = Math.min(A.length, B.length); k >= 1; k--) {
      if (feasible(k)) {
        best = k;
        break;
      }
    }
    for (let i = 0; i < best; i++) {
      const a = A[best - 1 - i];
      const b = B[i];
      plan.push({ sell: a.id, buy: b.id, price: priceWithFee(a.price, b.price, fee)! });
    }
  }
  return plan;
}

/**
 * What the market agent runs: the stall's order while there is time (stranded traders usually cross next tick as
 * they relax), the most pairs once the session is late or a trader is about to leave. Never fewer pairs than the
 * stall, so never below the half-points floor.
 */
export function hybridPlan(quotes: Quote[], progress: number, leavingIds: Set<string>, fee: (p: number) => number = () => 0, late = 0.55): Match[] {
  const stall = stallPlan(quotes, fee);
  const urgent = progress >= late || quotes.some((q) => leavingIds.has(q.id));
  if (!urgent) return stall;
  const max = maxCrossPlan(quotes, fee);
  return max.length > stall.length ? max : stall;
}

/** Real offers on our venue: one card for cash against a bid for that card, lowest ask first, midpoint. */
export function publicPlan(offers: Offer[], feeBps: number, feePerCard: number): Match[] {
  const fee = (p: number) => Math.ceil((feeBps * p) / 10000) + feePerCard;
  const bids = offers
    .filter((o) => o.give?.cash && (o.want?.types ?? []).length === 1)
    .sort((a, b) => b.give.cash - a.give.cash);
  const plan: Match[] = [];
  const asks = offers.filter((o) => (o.give?.assets ?? []).length === 1 && o.want?.cash).sort((a, b) => a.want.cash - b.want.cash);
  for (const s of asks) {
    const card = `card:${s.give.assets[0].ref}`;
    const idx = bids.findIndex((b) => b.want.types[0] === card && b.maker !== s.maker && s.want.cash + fee(s.want.cash) <= b.give.cash);
    if (idx < 0) continue;
    const b = bids.splice(idx, 1)[0];
    const p = priceWithFee(s.want.cash, b.give.cash, fee);
    if (p != null) plan.push({ sell: String(s.id), buy: String(b.id), price: p });
  }
  return plan.slice(0, 10);
}
