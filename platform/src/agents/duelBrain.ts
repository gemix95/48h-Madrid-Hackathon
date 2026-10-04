/**
 * Duel brain: one rival, one private limit, a pie that shrinks every round.
 *
 * What our 136 finished duels taught (Fri + Sat):
 *  - Points = our surplus × (1 − decay)^rounds. A deal past the limit is negative, no deal is 0.
 *  - Deals settle around 0.88 × limit for a buyer and 1.21 × cost for a seller, after 2–3 rounds.
 *  - Two-issue duels: a seller GAINS its weight per delivery day, a buyer PAYS its weight per day.
 *    Buyers' weights (median 3.6) are usually bigger than sellers' (median 2.1), so day 0 is usually the
 *    efficient day. Taking day 10 as a buyer cost us up to −60 points per duel on Saturday.
 *  - 18 of 24 no-deals were rivals that never spoke: open once, then wait for them.
 *
 * The plan: pick the delivery day that grows the pie, open far from our limit, concede on a schedule that closes
 * in ~4 rounds, accept as soon as the rival's offer beats what one more round is likely to bring.
 */
import type { Duel } from "../sdk/types.js";
import { duelSurplus } from "../game/scoring.js";

export const RIVAL_WEIGHT_PRIOR = { seller: 2.1, buyer: 3.6 };

export interface DuelKnobs {
  rounds: number;      // our offers before we reach the floor
  anchor: number;      // opening surplus as a share of the limit
  floorShare: number;  // the smallest surplus we offer, as a share of the limit
  acceptShare: number; // accept when the rival's offer gives this share of our next offer's (decayed) surplus
}

export const DEFAULT_DUEL_KNOBS: DuelKnobs = { rounds: 4, anchor: 0.45, floorShare: 0.03, acceptShare: 0.62 };

export type DuelAction =
  | { kind: "accept"; surplus: number; why: string }
  | { kind: "offer"; price: number; days: number | null; surplus: number; why: string }
  | { kind: "wait"; why: string };

export function decideDuel(d: Duel, nowTick: number, knobs: DuelKnobs = DEFAULT_DUEL_KNOBS): DuelAction {
  const seller = d.role === "seller";
  const L = d.your_limit;
  const twoIssue = (d.issues ?? []).includes("days");
  const w = twoIssue ? Math.abs(d.your_days_weight ?? 0) : 0;
  const decay = d.decay_per_round ?? 0.08;
  const ticksLeft = d.deadline_tick ? d.deadline_tick - nowTick : 99;
  const util = (p: number, days: number | null) => duelSurplus(d.role, L, p, twoIssue ? days : 0, w);

  const mine = d.messages.filter((m) => m.from === "you" && m.price != null);
  const theirs = d.messages.filter((m) => m.from !== "you" && m.price != null);
  const rival = d.rival_offer ?? (theirs.length ? { price: theirs[theirs.length - 1].price!, days: theirs[theirs.length - 1].days ?? 0, tick: theirs[theirs.length - 1].tick, id: 0 } : null);
  const k = mine.length;

  // 1. The delivery day that grows the pie.
  let days: number | null = null;
  let dayNote = "";
  if (twoIssue) {
    const rivalW = seller ? RIVAL_WEIGHT_PRIOR.buyer : RIVAL_WEIGHT_PRIOR.seller;
    // a seller gains w per day and the buyer pays rivalW: days are worth it only if we gain more than they pay
    const daysGrowPie = seller ? w > rivalW : w < rivalW;
    days = daysGrowPie ? 10 : 0;
    dayNote = seller
      ? daysGrowPie
        ? `each day pays us ${w.toFixed(2)} P, more than a buyer usually pays: day 10`
        : `each day pays us only ${w.toFixed(2)} P, a buyer usually pays ~${rivalW}: give day 0 and charge for it in price`
      : daysGrowPie
        ? `each day costs us only ${w.toFixed(2)} P, a seller usually gains ~${rivalW}: give day 10 and take it off the price`
        : `each day costs us ${w.toFixed(2)} P: day 0`;
  }

  // 2. Our target surplus this round: from anchor down to the floor, faster when decay is high or time is short.
  const R = Math.max(2, decay >= 0.09 || ticksLeft <= 12 ? Math.min(knobs.rounds, 4) : knobs.rounds);
  const U0 = knobs.anchor * L;
  const Ufloor = Math.max(1, knobs.floorShare * L);
  const lastChance = ticksLeft <= 3 || k >= R;
  const x = Math.min(1, k / R);
  let target = lastChance ? Ufloor : U0 - (U0 - Ufloor) * Math.pow(x, 1.3);

  // the rival's offer bounds what we ask: never ask for less than they already give
  const uRival = rival ? util(rival.price, rival.days ?? days) : null;
  if (uRival != null && uRival > target) target = uRival;

  const priceFor = (U: number) => {
    const dayCash = w * (days ?? 0);
    return seller ? Math.ceil(L + U - dayCash) : Math.floor(L - U - dayCash);
  };
  let price = Math.max(1, priceFor(target));
  // never a price outside our limit once days are counted
  if (util(price, days) <= 0) price = Math.max(1, priceFor(Ufloor));
  const uOurs = util(price, days);

  // 3. Accept when the rival's offer beats one more round.
  if (rival && uRival != null && uRival > 0) {
    const nextWorth = uOurs * (1 - decay);
    const why = `their ${rival.price} P${twoIssue ? ` at day ${rival.days}` : ""} gives us +${uRival.toFixed(1)}; our next offer would give +${uOurs.toFixed(1)} before ${Math.round(decay * 100)}% decay`;
    if (ticksLeft <= 2) return { kind: "accept", surplus: uRival, why: `last ticks: ${why}. Any positive deal beats 0.` };
    if (uRival >= knobs.acceptShare * nextWorth) return { kind: "accept", surplus: uRival, why };
    if (lastChance && uRival >= 0.35 * nextWorth) return { kind: "accept", surplus: uRival, why: `out of rounds: ${why}` };
  }

  // 4. Silent rival: one opening, then wait for them (no deal still scores 0, talking into the void costs nothing
  //    but repeated offers pin us to our own price).
  if (!theirs.length && !d.rival_offer && k >= 1 && ticksLeft > 3) {
    return { kind: "wait", why: "the rival has not spoken yet; our opening offer stands" };
  }
  const lastMine = mine[mine.length - 1];
  if (lastMine && lastMine.price === price && (lastMine.days ?? null) === days && (!rival || (rival.tick ?? 0) < lastMine.tick)) {
    return { kind: "wait", why: "nothing new from the rival since our last offer" };
  }

  const stage = lastChance ? "last offer, just inside our limit" : k === 0 ? "opening far from our limit" : `round ${k + 1} of ${R}`;
  return {
    kind: "offer",
    price,
    days,
    surplus: uOurs,
    why: `${stage}: ${price} P${days != null ? ` at day ${days}` : ""} keeps +${uOurs.toFixed(1)} for us${dayNote ? `; ${dayNote}` : ""}`,
  };
}

export function duelMessage(a: Extract<DuelAction, { kind: "offer" }>, k: number): string {
  const day = a.days != null ? ` with delivery on day ${a.days}` : "";
  if (k === 0) return `Hello, thanks for meeting us. We can do ${a.price} P${day}.`;
  return `Thanks for moving. We can do ${a.price} P${day}; that is a fair close for both of us.`;
}
