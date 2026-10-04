import type { Agent, Ctx } from "./base.js";
import { decide } from "./base.js";
import { benchQuotes, hybridPlan, publicPlan, stallPlan, type Match } from "./marketBrain.js";
import type { BrokerBook } from "../sdk/types.js";

export class MarketAgent implements Agent {
  id = "market" as const;
  name = "Market agent";
  role = "Runs our market's broker: matches every Market Test pair the stall would (the half-points floor), more pairs late in hard sessions, and crosses real bids and asks on our book.";
  private lastSig = "";
  private runStart = new Map<string, number>();
  book: BrokerBook | null = null;
  lastPlan: Match[] = [];
  sessionTicks = 16;

  async step(ctx: Ctx) {
    const w = ctx.world;
    const venue = w.me?.venue;
    if (w.me && (!venue || venue.status !== "open")) {
      await decide(ctx, "market", {
        kind: "alert", title: "Our market is not open",
        why: "Each Market Test counts our best venue open during it; with none open the session scores 0. Reopen it (level 2+, bond 250 P + 20 P) as a board venue at 0% fee and keep it open all day.",
      }, undefined, 40);
    } else if (venue && (venue.fee_bps > 0 || venue.fee_per_card > 0)) {
      await decide(ctx, "market", {
        kind: "alert", title: `Our market charges a fee (${venue.fee_bps} bps + ${venue.fee_per_card} P/card)`,
        why: "Fees never score, and a fee blocks thin Market Test pairs the 0% stall would take.",
      }, undefined, 40);
    }
    if (!ctx.broker) {
      await decide(ctx, "market", {
        kind: "info", title: "No broker key: the market agent can only watch",
        why: "Set BROKER_KEY (from team13/state.json on the server) to let it read the Market Test book. Only one broker may run per venue: stop bazaar-broker before going live.",
      }, undefined, 240);
      return;
    }
    if (!w.open) return;
    const next = w.schedule.find((s) => s.action === "bench");
    if (next?.params?.ticks) this.sessionTicks = Number(next.params.ticks);
    let book: BrokerBook;
    try {
      book = await ctx.broker.book();
    } catch (e) {
      await decide(ctx, "market", { kind: "error", title: "Could not read our book", why: String(e).slice(0, 200) }, undefined, 10);
      return;
    }
    this.book = book;
    const quotes = benchQuotes(book.bench_offers ?? []);
    const sig = `${w.tick}|${quotes.map((q) => `${q.id}:${q.price}`).join(",")}|${(book.offers ?? []).length}`;
    if (sig === this.lastSig) return;
    this.lastSig = sig;
    const fee = (p: number) => Math.ceil(((book.fee_bps ?? 0) * p) / 10000) + (book.fee_per_card ?? 0);
    for (const q of quotes) if (!this.runStart.has(q.run)) this.runStart.set(q.run, w.tick);
    const run = quotes[0]?.run;
    const progress = run ? (w.tick - (this.runStart.get(run) ?? w.tick)) / this.sessionTicks : 0;
    const hard = /hard/i.test(String(next?.note ?? "")) || /hard/i.test(String(next?.params?.name ?? ""));
    const leaving = new Set((book.bench_offers ?? []).filter((o) => o.expires_tick != null && o.expires_tick - w.tick <= 1).map((o) => o.id));
    const plan = quotes.length ? (hard ? hybridPlan(quotes, progress, leaving, fee) : stallPlan(quotes, fee)) : [];
    const pub = publicPlan(book.offers ?? [], book.fee_bps ?? 0, book.fee_per_card ?? 0);
    this.lastPlan = [...plan, ...pub];
    if (quotes.length) {
      await decide(ctx, "market", {
        kind: "bench", title: `Market Test: ${quotes.length} quotes, ${plan.length} pairs to match`,
        why: `${Math.round(progress * 100)}% of the session gone; ${hard ? "hard session: more pairs once late or a trader is leaving" : "stall order (the half-points floor)"}`,
        numbers: { asks: quotes.filter((q) => q.side === "ask").length, bids: quotes.filter((q) => q.side === "bid").length, pairs: plan.length },
      }, undefined, 1);
    }
    for (const m of this.lastPlan) {
      await decide(ctx, "market", { kind: "match", title: `Match ${m.sell} → ${m.buy} at ${m.price} P`, why: "both quotes cross at this price" }, () => ctx.broker!.match(m.sell, m.buy, m.price), 1);
    }
  }

  status(ctx: Ctx) {
    const v = ctx.world.me?.venue;
    return { venue: v?.venue ?? null, venue_status: v?.status ?? "none", fee_bps: v?.fee_bps ?? null, broker: !!ctx.broker, last_plan: this.lastPlan.length };
  }
}
