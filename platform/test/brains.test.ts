import { describe, expect, it } from "vitest";
import { Values } from "../src/game/values.js";
import { duelSurplus, teamTradeScore } from "../src/game/scoring.js";
import { decideDuel } from "../src/agents/duelBrain.js";
import { maxCrossPlan, stallPlan, hybridPlan, type Quote } from "../src/agents/marketBrain.js";
import { evaluateOffer, haggleBuyStep } from "../src/agents/dealBrain.js";

// Salamanca's real book prices: page = 5 commons + 3 uncommons + 2 rares, book sum 265 (× 1.6 = 424).
const SAL_BOOKS = [10, 10, 10, 12, 12, 25, 25, 28, 70, 63];
const catalog: any = {
  values: { copy_marginals: [1, 0.25, 0.1], page_bonus: 0.25, master_bonus: 0.1 },
  sets: [
    {
      id: "SAL", name: "Salamanca", color: "#2E86AB", released: true,
      cards: SAL_BOOKS.map((book, i) => ({ id: `SAL-${String(i + 1).padStart(2, "0")}`, name: `S${i + 1}`, book, page: true, rarity: "common" })),
    },
    { id: "LAT", name: "La Latina", color: "#F4A259", released: true, cards: [{ id: "LAT-01", name: "L1", book: 10, page: true, rarity: "common" }] },
  ],
};
const salAssets = SAL_BOOKS.map((_, i) => ({ id: 100 + i, kind: "card", ref: `SAL-${String(i + 1).padStart(2, "0")}` })) as any[];
const values = () => new Values(catalog, { SAL: 1.6, LAT: 0.5 }, salAssets);

describe("private values", () => {
  it("reproduces your_value for SAL-09 on a complete page: 70 × 1.6 + 25% × 424 = 218", () => {
    const v = values();
    expect(v.pageValue("SAL")).toBeCloseTo(424);
    expect(v.lossOfRemoving(["SAL-09"])).toBeCloseTo(218);
  });
  it("a second copy is worth a quarter", () => {
    expect(values().gainOfAdding(["SAL-01"])).toBeCloseTo(10 * 1.6 * 0.25);
  });
  it("losing a card we do not hold is impossible", () => {
    expect(values().lossOfRemoving(["LAT-01"])).toBe(Infinity);
  });
});

describe("scoring", () => {
  it("caps a team-trade gain at +50 and counts a loss in full", () => {
    expect(teamTradeScore(200, 0, 0, 10)).toBe(50);
    expect(teamTradeScore(0, 10, 218, 0)).toBe(-208);
  });
  it("buyer surplus pays its weight per delivery day", () => {
    expect(duelSurplus("buyer", 200, 140, 10, 7.99)).toBeCloseTo(-19.9);
  });
});

const duel = (over: any = {}): any => ({
  id: 1, status: "open", role: "buyer", your_limit: 200, issues: ["price", "days"], your_days_weight: 7.99,
  decay_per_round: 0.08, deadline_tick: 1000, messages: [], rival_offer: null, ...over,
});

describe("duel brain", () => {
  it("does not accept 140 P at day 10 when each day costs us 7.99 (duel 5629), and offers day 0", () => {
    const d = duel({
      messages: [{ from: "you", price: 90, days: 0, tick: 980 }, { from: "rival", price: 140, days: 10, tick: 981 }],
      rival_offer: { id: 9, price: 140, days: 10, tick: 981 },
    });
    const a = decideDuel(d, 982);
    expect(a.kind).toBe("offer");
    if (a.kind === "offer") {
      expect(a.days).toBe(0);
      expect(a.surplus).toBeGreaterThan(0);
    }
  });
  it("accepts any positive offer in the last ticks", () => {
    const d = duel({ issues: ["price"], messages: [{ from: "you", price: 150, tick: 990 }], rival_offer: { id: 9, price: 195, days: 0, tick: 997 } });
    expect(decideDuel(d, 999).kind).toBe("accept");
  });
  it("waits for a silent rival after opening once", () => {
    const d = duel({ messages: [{ from: "you", price: 90, days: 0, tick: 980 }] });
    expect(decideDuel(d, 984).kind).toBe("wait");
  });
  it("never offers past our limit", () => {
    for (let k = 0; k < 8; k++) {
      const msgs = Array.from({ length: k }, (_, i) => ({ from: "you", price: 100 + i, days: 0, tick: 900 + i }));
      const a = decideDuel(duel({ messages: msgs, rival_offer: { id: 1, price: 260, days: 0, tick: 950 } }), 960);
      if (a.kind === "offer") expect(duelSurplus("buyer", 200, a.price, a.days ?? 0, 7.99)).toBeGreaterThan(0);
    }
  });
});

const q = (id: string, side: "ask" | "bid", price: number): Quote => ({ id, side, price, run: "r1" });

describe("market brain", () => {
  const quotes = [q("b10", "bid", 10), q("b3", "bid", 3), q("a2", "ask", 2), q("a9", "ask", 9)];
  it("stall rule strands 3 and 9", () => {
    expect(stallPlan(quotes)).toHaveLength(1);
  });
  it("max crossings clears both pairs (10↔9, 3↔2)", () => {
    const plan = maxCrossPlan(quotes);
    expect(plan.map((m) => `${m.buy}-${m.sell}`).sort()).toEqual(["b10-a9", "b3-a2"]);
  });
  it("hybrid never matches fewer pairs than the stall", () => {
    expect(hybridPlan(quotes, 0.1, new Set()).length).toBeGreaterThanOrEqual(1);
    expect(hybridPlan(quotes, 0.9, new Set())).toHaveLength(2);
  });
});

describe("deal brain", () => {
  const v = values();
  const ours = salAssets.map((a) => ({ id: a.id, ref: a.ref }));
  const noFee = () => ({ bps: 0, perCard: 0 });
  it("rejects selling a page card below its value", () => {
    const e = evaluateOffer({ id: 1, maker: "t05", venue: "v01", give: { cash: 100 }, want: { types: ["card:SAL-09"] } } as any, v, ours, noFee, new Set());
    expect(e!.score).toBeLessThan(0);
  });
  it("counts the venue fee as cash out", () => {
    const e = evaluateOffer({ id: 2, maker: "t05", venue: "vX", give: { assets: [{ id: 5, ref: "LAT-01" }] }, want: { cash: 2 } } as any, v, ours, () => ({ bps: 500, perCard: 1 }), new Set());
    expect(e!.fee).toBe(2);
    // LAT-01 is 10 × 0.5 = 5, plus the 25% bonus of this one-card test page; minus 2 P price and 2 P fee
    expect(e!.score).toBeCloseTo(5 + 1.25 - 4);
  });
  it("never touches reserved cards", () => {
    expect(evaluateOffer({ id: 3, maker: "t05", venue: "v01", give: { cash: 999 }, want: { types: ["card:SAL-01"] } } as any, v, ours, noFee, new Set(["SAL-01"]))).toBeNull();
  });
  it("haggle: a new, rising price every message and a walk past the cap", () => {
    const prices: number[] = [];
    for (let i = 0; i < 20; i++) {
      const a = haggleBuyStep(100, 60, prices, { id: 1, price: 95, final: false });
      if (a.kind === "walk") break;
      expect(a.kind).toBe("offer");
      if (a.kind === "offer") {
        if (prices.length) expect(a.price).toBeGreaterThan(prices[prices.length - 1]);
        expect(a.price).toBeLessThanOrEqual(60);
        prices.push(a.price);
      }
    }
    expect(prices[0]).toBe(40);
    expect(haggleBuyStep(100, 60, prices, { id: 1, price: 95, final: true }).kind).toBe("walk");
    expect(haggleBuyStep(100, 60, [40], { id: 1, price: 55, final: true }).kind).toBe("accept");
  });
});
