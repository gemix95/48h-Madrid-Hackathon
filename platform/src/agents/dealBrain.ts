/**
 * Deal brain: team trades and dealer haggling, at OUR private values.
 *
 * Team trades. A trade scores (value in + cash in − value out − cash out), a gain capped at +50, a loss in full.
 * On Saturday we lost 432.7 P of value in team trades: resting asks filled below what the cards were worth to us,
 * and cards bought back above their value. So: only take an offer whose score after the venue fee is at least
 * `minGain`, and never hand over a card for less than it is worth to us.
 *
 * Dealers (the ladder). Best 3 deals per dealer count, scored by the share of the dealer's price range we capture;
 * a buy above our value counts as a full loss. So: buy only what is worth more to us than we pay, open low
 * (30–45% of the opening ask got the lowest prices across every team's threads), move a little every message
 * (a dealer only moves when we move; the same price twice earns nothing), take a final offer only inside our cap.
 */
import type { Offer } from "../sdk/types.js";
import type { Values } from "../game/values.js";
import { teamTradeScore, venueFee } from "../game/scoring.js";

export interface OfferEval {
  offer: Offer;
  score: number;
  valueIn: number;
  valueOut: number;
  cashIn: number;
  cashOut: number;
  fee: number;
  giveAssets: number[]; // our asset ids to hand over when the offer asks for a card type
  why: string;
}

export function evaluateOffer(
  o: Offer,
  v: Values,
  ours: { id: number; ref?: string }[],
  venueFeeOf: (venue: string | null) => { bps: number; perCard: number },
  reserved: Set<string | number>,
): OfferEval | null {
  const giveRefs = (o.give?.assets ?? []).map((a) => a.ref!).filter(Boolean);
  if ((o.give?.types ?? []).length) return null; // "any copy" on their side: we cannot know which card arrives
  if ((o.want?.assets ?? []).length) return null; // they want a specific copy we do not hold
  const wantRefs = (o.want?.types ?? []).filter((t) => t.startsWith("card:")).map((t) => t.slice(5));
  const giveAssets: number[] = [];
  const pool = [...ours];
  for (const ref of wantRefs) {
    if (reserved.has(ref)) return null;
    const idx = pool.findIndex((a) => a.ref === ref && !reserved.has(a.id));
    if (idx < 0) return null;
    giveAssets.push(pool[idx].id);
    pool.splice(idx, 1);
  }
  const valueIn = v.gainOfAdding(giveRefs);
  const valueOut = wantRefs.length ? v.lossOfRemoving(wantRefs) : 0;
  if (!Number.isFinite(valueOut)) return null;
  const cashIn = o.give?.cash ?? 0;
  const cashOutPrice = o.want?.cash ?? 0;
  const { bps, perCard } = venueFeeOf(o.venue);
  const fee = venueFee(Math.max(cashIn, cashOutPrice), bps, perCard, giveRefs.length + wantRefs.length);
  const score = teamTradeScore(valueIn, cashIn, valueOut, cashOutPrice + fee);
  const parts = [
    giveRefs.length ? `we get ${giveRefs.join(", ")} (worth ${valueIn.toFixed(1)} to us)` : "",
    cashIn ? `we get ${cashIn} P` : "",
    wantRefs.length ? `we give ${wantRefs.join(", ")} (worth ${valueOut.toFixed(1)} to us)` : "",
    cashOutPrice ? `we pay ${cashOutPrice} P` : "",
    fee ? `fee ${fee} P` : "",
  ].filter(Boolean);
  return { offer: o, score, valueIn, valueOut, cashIn, cashOut: cashOutPrice + fee, fee, giveAssets, why: parts.join(", ") };
}

/** The lowest price at which a sale of `refs` still gains `minGain` after the fee: our listing floor. */
export function sellFloor(v: Values, refs: string[], minGain: number, feeBps = 0, perCard = 0): number {
  const loss = v.lossOfRemoving(refs);
  let p = Math.ceil(loss + minGain);
  while (p - venueFee(p, feeBps, perCard, refs.length) < loss + minGain) p++;
  return p;
}

export interface HaggleKnobs {
  openShare: number; // first offer as a share of the dealer's opening ask (buying)
  rounds: number;    // our offers before we reach our cap
  curve: number;     // >1 concedes slowly first (Boulware)
}

export const DEFAULT_HAGGLE: HaggleKnobs = { openShare: 0.4, rounds: 7, curve: 1.6 };

export type HaggleAction =
  | { kind: "accept"; offerId: number; price: number; why: string }
  | { kind: "offer"; price: number; why: string }
  | { kind: "walk"; why: string };

/**
 * One step of a buy haggle. `cap` is the most we pay (never above the item's value to us). `ourPrices` are our
 * earlier offers in this thread, `theirAsk` the dealer's standing price, `final` whether the dealer called it final.
 */
export function haggleBuyStep(
  opening: number,
  cap: number,
  ourPrices: number[],
  theirAsk: { id: number; price: number; final: boolean } | null,
  k: HaggleKnobs = DEFAULT_HAGGLE,
): HaggleAction {
  const n = ourPrices.length;
  const start = Math.max(1, Math.round(opening * k.openShare));
  const x = Math.min(1, n / k.rounds);
  let target = Math.round(start + (cap - start) * Math.pow(x, k.curve));
  target = Math.min(target, cap);
  if (theirAsk) {
    const inCap = theirAsk.price <= cap;
    if (theirAsk.final) {
      return inCap
        ? { kind: "accept", offerId: theirAsk.id, price: theirAsk.price, why: `final offer ${theirAsk.price} P is inside our cap ${cap} P` }
        : { kind: "walk", why: `final offer ${theirAsk.price} P is above our cap ${cap} P` };
    }
    // their ask is at or below what we would offer next: take it, waiting only risks the walk
    if (inCap && theirAsk.price <= target + 1) {
      return { kind: "accept", offerId: theirAsk.id, price: theirAsk.price, why: `their ${theirAsk.price} P is no more than our next step (${target} P)` };
    }
  }
  const last = ourPrices[n - 1];
  if (last != null && target <= last) target = last + 1; // a new price every message, or the dealer does not move
  if (target > cap) return { kind: "walk", why: `our next step would pass our cap ${cap} P` };
  const stage = n === 0 ? `open at ${Math.round(k.openShare * 100)}% of the ${opening} P ask` : `step ${n + 1}: concede slowly`;
  return { kind: "offer", price: target, why: `${stage}; cap ${cap} P` };
}

/** Dealer price as a share of the range we could capture (opening ask → our cap): 1 = paid our open, 0 = paid the ask. */
export function ladderShare(opening: number, floorGuess: number, price: number): number {
  if (opening <= floorGuess) return 0;
  return Math.max(0, Math.min(1, (opening - price) / (opening - floorGuess)));
}
