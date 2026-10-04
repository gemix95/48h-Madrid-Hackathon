/**
 * Our private value of cards, the number the scorer uses for every trade.
 *
 * value of a copy = book × set multiplier × copy factor (1st ×1, 2nd ×0.25, 3rd+ ×0.1)
 * page bonus      = 25% of the page's value once its 5 commons + 3 uncommons + 2 rares are all held
 * master bonus    = +10% of the page's value once the epic and legendary are held as well
 *
 * `your_value` from /api/me is what losing that card would cost us (copy + any bonus it breaks); this model
 * reproduces it (SAL-09 on a complete Salamanca page: 70 × 1.6 + 25% × 424 = 218).
 */
import type { Asset, Catalog, CatalogCard } from "../sdk/types.js";

export interface CardInfo extends CatalogCard {
  set: string;
  released: boolean;
}

export class Values {
  readonly cards = new Map<string, CardInfo>();
  readonly sets = new Map<string, Catalog["sets"][number]>();
  private marg: number[];
  private pageRate: number;
  private masterRate: number;
  held = new Map<string, number>();
  aff: Record<string, number> = {};

  constructor(catalog: Catalog, affinity: Record<string, number>, assets: Asset[]) {
    this.marg = catalog.values?.copy_marginals ?? [1, 0.25, 0.1];
    this.pageRate = catalog.values?.page_bonus ?? 0.25;
    this.masterRate = catalog.values?.master_bonus ?? 0.1;
    for (const s of catalog.sets) {
      this.sets.set(s.id, s);
      for (const c of s.cards) this.cards.set(c.id, { ...c, set: s.id, released: s.released });
    }
    this.update(affinity, assets);
  }

  update(affinity: Record<string, number>, assets: Asset[]) {
    this.aff = affinity ?? {};
    this.held = new Map();
    for (const a of assets) if (a.kind === "card" && a.ref) this.held.set(a.ref, (this.held.get(a.ref) ?? 0) + 1);
  }

  m(ref: string): number {
    const c = this.cards.get(ref);
    return c ? this.aff[c.set] ?? 1 : 1;
  }

  copyFactor(nAlreadyHeld: number): number {
    return this.marg[Math.min(nAlreadyHeld, this.marg.length - 1)];
  }

  pageCards(setId: string): string[] {
    return (this.sets.get(setId)?.cards ?? []).filter((c) => c.page).map((c) => c.id);
  }

  pageValue(setId: string): number {
    return this.pageCards(setId).reduce((s, r) => s + (this.cards.get(r)!.book * this.m(r)), 0);
  }

  private bonus(setId: string, held: Map<string, number>): number {
    const page = this.pageCards(setId);
    if (!page.length || !page.every((r) => (held.get(r) ?? 0) > 0)) return 0;
    let b = this.pageRate * this.pageValue(setId);
    const extras = (this.sets.get(setId)?.cards ?? []).filter((c) => !c.page && !c.hidden).map((c) => c.id);
    if (extras.length && extras.every((r) => (held.get(r) ?? 0) > 0)) b += this.masterRate * this.pageValue(setId);
    return b;
  }

  /** What receiving these cards adds to our collection (duplicates and page completion included). */
  gainOfAdding(refs: string[]): number {
    const held = new Map(this.held);
    let total = 0;
    for (const ref of refs) {
      const c = this.cards.get(ref);
      if (!c) continue;
      const before = this.bonus(c.set, held);
      total += c.book * this.m(ref) * this.copyFactor(held.get(ref) ?? 0);
      held.set(ref, (held.get(ref) ?? 0) + 1);
      total += this.bonus(c.set, held) - before;
    }
    return total;
  }

  /** What handing over these cards takes from our collection. Infinity if we do not hold them. */
  lossOfRemoving(refs: string[]): number {
    const held = new Map(this.held);
    let total = 0;
    for (const ref of refs) {
      const c = this.cards.get(ref);
      const n = held.get(ref) ?? 0;
      if (!c || n <= 0) return Infinity;
      const before = this.bonus(c.set, held);
      held.set(ref, n - 1);
      total += c.book * this.m(ref) * this.copyFactor(n - 1);
      total += before - this.bonus(c.set, held);
    }
    return total;
  }

  /** Cards we lack, ranked by what one copy would add (page completion counted). */
  wishlist(limit = 12): { ref: string; gain: number }[] {
    const out: { ref: string; gain: number }[] = [];
    for (const [ref, c] of this.cards) {
      if (!c.released || c.hidden || (this.held.get(ref) ?? 0) > 0) continue;
      out.push({ ref, gain: this.gainOfAdding([ref]) });
    }
    return out.sort((a, b) => b.gain - a.gain).slice(0, limit);
  }

  /** Copies we can let go cheaply: duplicates, and every card of a set we value under ×1 that is not on a full page. */
  spares(assets: Asset[], reserved: Set<string | number> = new Set()): { asset: Asset; loss: number }[] {
    const out: { asset: Asset; loss: number }[] = [];
    for (const a of assets) {
      if (a.kind !== "card" || !a.ref || reserved.has(a.ref) || reserved.has(a.id)) continue;
      const loss = this.lossOfRemoving([a.ref]);
      const n = this.held.get(a.ref) ?? 0;
      const weakSet = this.m(a.ref) < 1;
      if (n >= 2 || weakSet) out.push({ asset: a, loss });
    }
    return out.sort((x, y) => x.loss - y.loss);
  }

  pages() {
    return [...this.sets.values()].map((s) => {
      const page = this.pageCards(s.id);
      const have = page.filter((r) => (this.held.get(r) ?? 0) > 0).length;
      const missing = page.filter((r) => (this.held.get(r) ?? 0) === 0);
      return {
        set: s.id, name: s.name, color: s.color, released: s.released, multiplier: this.aff[s.id] ?? 1,
        have, of: page.length, missing, pageValue: this.pageValue(s.id), bonus: this.pageRate * this.pageValue(s.id),
      };
    });
  }
}
