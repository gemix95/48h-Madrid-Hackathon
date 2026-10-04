import type { Agent, Ctx } from "./base.js";
import { decide } from "./base.js";
import { evaluateOffer, haggleBuyStep, type OfferEval, DEFAULT_HAGGLE } from "./dealBrain.js";
import type { Dealer, DealerMenuItem, Thread } from "../sdk/types.js";
import type { Values } from "../game/values.js";

export interface LadderSlot {
  dealer: string;
  dealerName: string;
  level: number;
  dealsToday: number;
  bestPrices: number[];
  target: Target | null;
  /** The best no-loss item we cannot afford yet. */
  stretch: Target | null;
  note: string;
}

interface Target { label: string; topic: Record<string, unknown>; value: number; opening: number; list: number; cap: number; expected: number; gain: number }

const EXPECTED_SHARE = 0.77; // across every team's haggles, the lowest prices landed near 77% of the dealer's opening ask

export class DealAgent implements Agent {
  id = "deal" as const;
  name = "Deal agent";
  role = "Team trades and dealers: takes only trades that gain at our private values after fees, plans the 3 best ladder deals per dealer, haggles with the dealers you hand it.";
  minGain = Number(process.env.DEAL_MIN_GAIN ?? 3);
  /** Dealers this agent may haggle with live; the Python agents keep the others (one conversation per dealer). */
  dealers = new Set((process.env.DEAL_DEALERS ?? "").split(",").map((s) => s.trim()).filter(Boolean));
  opportunities: OfferEval[] = [];
  ladder: LadderSlot[] = [];
  private lastTick = -1;
  private haggles = new Map<string, { thread: number; cap: number; opening: number; label: string }>();

  async step(ctx: Ctx) {
    const w = ctx.world;
    const v = w.values;
    if (!v || !w.me) return;
    this.ladder = this.planLadder(ctx, v);
    if (!w.open || w.tick === this.lastTick) return;
    this.lastTick = w.tick;
    await this.trades(ctx, v);
    await this.haggle(ctx, v);
  }

  private async trades(ctx: Ctx, v: Values) {
    const w = ctx.world;
    const me = w.me!;
    const ourVenue = me.venue?.venue;
    const evals: OfferEval[] = [];
    for (const [venue, offers] of w.boards) {
      if (venue === ourVenue) continue; // our key cannot trade on our own market
      for (const o of offers) {
        if (o.maker === me.id || o.status !== "open" || o.expires_tick <= w.tick) continue;
        if (o.to && o.to !== me.id) continue;
        const e = evaluateOffer({ ...o, venue: o.venue ?? venue }, v, me.assets.filter((a) => a.kind === "card"), (x) => w.venueFee(x), w.reserved);
        if (!e) continue;
        if (e.cashOut > me.cash) continue;
        evals.push(e);
      }
    }
    for (const o of w.myOffers.filter((x) => x.to === me.id && x.maker !== me.id)) {
      const e = evaluateOffer(o, v, me.assets.filter((a) => a.kind === "card"), (x) => w.venueFee(x), w.reserved);
      if (e && e.cashOut <= me.cash) evals.push(e);
    }
    evals.sort((a, b) => b.score - a.score);
    this.opportunities = evals.slice(0, 30);
    const best = evals.find((e) => e.score >= this.minGain);
    if (!best) return;
    const title = `Take offer #${best.offer.id} from ${best.offer.maker} on ${best.offer.venue}: +${best.score.toFixed(1)}`;
    if (ctx.mode("deal") === "live" && !ctx.takeAccept()) {
      await decide(ctx, "deal", { kind: "trade_wait", title, why: "the team's one accept this tick is taken; trying next tick", worth: best.score });
      return;
    }
    await decide(ctx, "deal", {
      kind: "trade", title, why: `${best.why}. Scores +${best.score.toFixed(1)} at our values (minimum ${this.minGain}).`, worth: best.score,
      numbers: { value_in: +best.valueIn.toFixed(1), value_out: +best.valueOut.toFixed(1), cash_in: best.cashIn, cash_out: best.cashOut, fee: best.fee },
    }, () => ctx.api.accept(best.offer.id, best.giveAssets), 8);
  }

  planLadder(ctx: Ctx, v: Values): LadderSlot[] {
    const w = ctx.world;
    const start = w.currentRoundStart();
    const out: LadderSlot[] = [];
    for (const d of w.dealers.filter((x) => x.status === "active" && x.enabled)) {
      const today = w.dealThreads.filter((t) => t.with === d.id && t.created_tick >= start);
      const prices = today.map((t) => dealPrice(t)).filter((p): p is number => p != null).sort((a, b) => a - b);
      const target = bestBuy(d, v, w.me!.cash);
      const stretch = target ? null : bestBuy(d, v, Infinity);
      out.push({
        dealer: d.id, dealerName: d.name, level: d.level ?? 0, dealsToday: today.length, bestPrices: prices.slice(0, 3), target, stretch,
        note: !target
          ? stretch
            ? `Best no-loss buy is ${stretch.label} (worth ${stretch.value.toFixed(0)} to us, likely ~${stretch.expected} P): we need ${stretch.expected - w.me!.cash} P more cash.`
            : "Nothing on this dealer's menu is worth more to us than its likely price: a buy here would count as a loss. Selling it a spare worth less to us than its price also fills a slot."
          : today.length >= 3
            ? "Three deals in: only a cheaper deal than the third best adds points."
            : `${3 - today.length} empty slot${today.length === 2 ? "" : "s"} (an empty slot counts 0; level ${d.level} weighs more than lower levels).`,
      });
    }
    return out.sort((a, b) => b.level - a.level);
  }

  private async haggle(ctx: Ctx, v: Values) {
    const w = ctx.world;
    for (const slot of this.ladder) {
      if (!slot.target || slot.dealsToday >= 3) continue;
      if (!this.dealers.has(slot.dealer)) {
        await decide(ctx, "deal", {
          kind: "haggle_plan", title: `Would haggle with ${slot.dealerName} for ${slot.target.label}`,
          why: `worth ${slot.target.value.toFixed(0)} P to us, likely ~${slot.target.expected} P, cap ${Math.min(slot.target.cap, w.me!.cash)} P. Not handed to this agent (DEAL_DEALERS), so the Python agents keep this dealer.`,
          worth: slot.target.gain,
        }, undefined, 120);
        continue;
      }
      const h = this.haggles.get(slot.dealer);
      if (!h) {
        const cap = Math.min(slot.target.cap, w.me!.cash);
        await decide(ctx, "deal", {
          kind: "haggle_open", title: `Open a haggle with ${slot.dealerName} for ${slot.target.label}`,
          why: `worth ${slot.target.value.toFixed(0)} P to us, likely price ~${slot.target.expected} P: a ladder slot with no risk of a loss. Cap ${cap} P.`,
          worth: slot.target.gain,
        }, async () => {
          const th = await ctx.api.openThread(slot.dealer, slot.target!.topic);
          this.haggles.set(slot.dealer, { thread: th.id, cap, opening: slot.target!.opening, label: slot.target!.label });
        }, 20);
        continue;
      }
      let th: Thread;
      try {
        th = await ctx.api.thread(h.thread);
      } catch {
        this.haggles.delete(slot.dealer);
        continue;
      }
      if (th.status !== "open") {
        await decide(ctx, "deal", { kind: "haggle_end", title: `${slot.dealerName}: conversation ${th.status}`, why: th.closed_reason ?? "the dealer ended it" }, undefined, 1);
        this.haggles.delete(slot.dealer);
        continue;
      }
      const msgs = th.messages ?? [];
      const ours = msgs.filter((m) => m.sender === w.me!.id && m.offer).map((m) => m.offer!.give?.cash ?? 0);
      const standing = (th.standing_offers ?? []).filter((o) => o.maker === slot.dealer && o.status !== "cancelled").pop();
      // structure binds: the dealer's offer must give what we asked for (Los Pícaros switch cards)
      const wantRef = (slot.target.topic as any).buy?.card as string | undefined;
      const honest = !standing || !wantRef || (standing.give?.types ?? []).includes(`card:${wantRef}`) || (standing.give?.assets ?? []).some((a) => a.ref === wantRef);
      const ask = standing && honest ? { id: standing.id, price: standing.want?.cash ?? 0, final: standing.final } : null;
      const step = haggleBuyStep(h.opening, h.cap, ours, ask, DEFAULT_HAGGLE);
      if (!honest) {
        await decide(ctx, "deal", { kind: "haggle_trick", title: `${slot.dealerName} offered a different card`, why: `we asked for ${wantRef}; the structured offer gives something else. Never accept it.` }, undefined, 4);
      }
      if (step.kind === "accept") {
        if (!ctx.takeAccept()) continue;
        await decide(ctx, "deal", { kind: "haggle_accept", title: `${slot.dealerName}: take ${h.label} at ${step.price} P`, why: step.why }, () => ctx.api.accept(step.offerId), 1);
      } else if (step.kind === "offer") {
        await decide(ctx, "deal", { kind: "haggle_offer", title: `${slot.dealerName}: offer ${step.price} P for ${h.label}`, why: step.why },
          () => ctx.api.say(h.thread, kindWords(slot.dealer, step.price), step.price), 1);
      } else {
        await decide(ctx, "deal", { kind: "haggle_walk", title: `${slot.dealerName}: walk away`, why: step.why }, async () => {
          await ctx.api.closeThread(h.thread);
          this.haggles.delete(slot.dealer);
        }, 1);
      }
    }
  }

  status() {
    return {
      opportunities: this.opportunities.filter((o) => o.score >= this.minGain).length,
      best: this.opportunities[0]?.score ?? null,
      haggling_with: [...this.dealers],
      ladder_slots_open: this.ladder.reduce((s, l) => s + Math.max(0, 3 - l.dealsToday), 0),
    };
  }
}

function dealPrice(t: Thread): number | null {
  const msgs = t.messages ?? [];
  for (let i = msgs.length - 1; i >= 0; i--) {
    const o = msgs[i].offer;
    if (o && o.status === "settled") return (o.want?.cash || o.give?.cash) ?? null;
  }
  return null;
}

/** The best no-loss buy on a dealer's menu: the item whose value to us beats its likely price by the most. */
function bestBuy(d: Dealer, v: Values, cash: number): Target | null {
  let best: Target | null = null;
  for (const item of d.menu?.sells ?? []) {
    // singles have no published opening ask; Abuela opened commons at 12 for a list of 10 (×1.2)
    const opening = item.opening_ask ?? Math.round((item.list_price ?? 0) * 1.2);
    const list = item.list_price ?? opening;
    if (!opening) continue;
    const expected = Math.round(opening * EXPECTED_SHARE);
    let label = "";
    let value = 0;
    let topic: Record<string, unknown> = {};
    if (item.pack) {
      label = item.name ?? item.pack;
      value = packValue(v, item.pack);
      topic = { buy: { pack: item.pack } };
    } else if (item.rarity) {
      const pick = bestCardOf(v, item);
      if (!pick) continue;
      label = `${pick.ref} (${item.rarity})`;
      value = pick.gain;
      topic = { buy: { card: pick.ref } };
    }
    const cap = Math.min(Math.floor(value), opening);
    if (cap < expected || expected > cash) continue;
    const gain = value - expected;
    if (!best || gain > best.gain) best = { label, topic, value, opening, list, cap, expected, gain };
  }
  return best;
}

function bestCardOf(v: Values, item: DealerMenuItem): { ref: string; gain: number } | null {
  const sets = item.sets === "released" || !item.sets ? null : new Set(item.sets as string[]);
  let best: { ref: string; gain: number } | null = null;
  for (const [ref, c] of v.cards) {
    if (c.rarity !== item.rarity || !c.released || c.hidden || (sets && !sets.has(c.set))) continue;
    if (c.minted >= c.print_run) continue;
    const gain = v.gainOfAdding([ref]);
    if (!best || gain > best.gain) best = { ref, gain };
  }
  return best;
}

const PACK_SLOTS: Record<string, Record<string, number>[]> = {};

export function setPackSlots(packs: { id: string; slots: Record<string, number>[] }[]) {
  for (const p of packs) PACK_SLOTS[p.id] = p.slots;
}

function packValue(v: Values, pack: string): number {
  const slots = PACK_SLOTS[pack];
  if (!slots) return 0;
  let ev = 0;
  for (const slot of slots) {
    for (const [rarity, p] of Object.entries(slot)) {
      const pool = [...v.cards.entries()].filter(([, c]) => c.rarity === rarity && c.released && !c.hidden);
      if (!pool.length) continue;
      ev += (p * pool.reduce((s, [ref]) => s + v.gainOfAdding([ref]), 0)) / pool.length;
    }
  }
  return ev;
}

function kindWords(dealer: string, price: number): string {
  if (dealer === "abuela") return `¡Buenos días, Carmen! Qué alegría verla. ¿Le parece bien ${price} primas?`;
  if (dealer === "banco") return `Buenos días, Don Ernesto. Con todo respeto, le propongo ${price} primas.`;
  if (dealer === "picaros") return `¡Hola, Paco y Nando! ${price} primas, por la carta que pedí.`;
  if (dealer === "pilar") return `Buenos días, Doña Pilar. Le ofrezco ${price} primas, con mucho gusto.`;
  return `Buenas. ${price} primas, ¿hay trato?`;
}
