import { appendFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { Bazaar, BazaarError } from "../sdk/bazaar.js";
import type { Catalog, Clock, Dealer, Duel, FeedEvent, Leaderboard, Me, Offer, ScheduleItem, Thread, Venue } from "../sdk/types.js";
import { Values } from "../game/values.js";

export interface LbPoint { ts: number; tick: number; teams: Record<string, { s: number; n: number; m: number }> }

/** Everything the agents and the UI read, refreshed on a budget the server's 5 requests/s allows. */
export class World {
  clock: Clock | null = null;
  me: Me | null = null;
  catalog: Catalog | null = null;
  values: Values | null = null;
  dealers: Dealer[] = [];
  schedule: ScheduleItem[] = [];
  scheduleNow = 0;
  leaderboard: Leaderboard | null = null;
  lbHistory: LbPoint[] = [];
  venues: Venue[] = [];
  boards = new Map<string, Offer[]>();
  duels: Duel[] = [];
  duelsDone: Duel[] = [];
  dealThreads: Thread[] = [];
  myOffers: Offer[] = [];
  news: any[] = [];
  feed: FeedEvent[] = [];
  private feedIds = new Set<number>();
  roundStartTick: Record<string, number> = {};
  reserved = new Set<string | number>();
  errors: { ts: number; what: string; error: string }[] = [];
  lastRefresh: Record<string, number> = {};
  private persistFile: string;

  constructor(public api: Bazaar, private dataDir: string, private reservedFile: string | null) {
    mkdirSync(dataDir, { recursive: true });
    this.persistFile = join(dataDir, "world.json");
    this.load();
  }

  get tick() {
    return this.clock?.tick ?? 0;
  }
  get open() {
    return !!this.clock && this.clock.doors === "open" && !this.clock.paused;
  }

  private load() {
    try {
      const s = JSON.parse(readFileSync(this.persistFile, "utf8"));
      this.lbHistory = s.lbHistory ?? [];
      this.roundStartTick = s.roundStartTick ?? {};
    } catch {
      /* first run */
    }
    const feedFile = join(this.dataDir, "feed.jsonl");
    if (existsSync(feedFile)) {
      for (const line of readFileSync(feedFile, "utf8").split("\n")) {
        if (!line) continue;
        try {
          const e = JSON.parse(line) as FeedEvent;
          if (!this.feedIds.has(e.id)) {
            this.feedIds.add(e.id);
            this.feed.push(e);
          }
        } catch {
          /* torn line */
        }
      }
      this.feed.sort((a, b) => a.id - b.id);
      if (this.feed.length > 20000) this.feed = this.feed.slice(-20000);
    }
  }

  private save() {
    try {
      writeFileSync(this.persistFile, JSON.stringify({ lbHistory: this.lbHistory.slice(-3000), roundStartTick: this.roundStartTick }));
    } catch {
      /* best effort */
    }
  }

  private due(what: string, everyMs: number) {
    const now = Date.now();
    if ((this.lastRefresh[what] ?? 0) + everyMs > now) return false;
    this.lastRefresh[what] = now;
    return true;
  }

  private async guard<T>(what: string, f: () => Promise<T>): Promise<T | null> {
    try {
      return await f();
    } catch (e) {
      const msg = e instanceof BazaarError ? `${e.code} ${e.message}` : String(e);
      this.errors.push({ ts: Date.now(), what, error: msg.slice(0, 200) });
      if (this.errors.length > 50) this.errors.splice(0, this.errors.length - 50);
      return null;
    }
  }

  loadReserved() {
    if (!this.reservedFile || !existsSync(this.reservedFile)) return;
    try {
      const r = JSON.parse(readFileSync(this.reservedFile, "utf8"));
      const s = new Set<string | number>();
      for (const k of ["refs", "assets", "asset_ids", "cards"]) for (const x of r[k] ?? []) s.add(x);
      this.reserved = s;
    } catch {
      /* keep the last good list */
    }
  }

  /** The cheap, frequent reads: once per tick while the doors are open, slower when closed. */
  async refresh(hasKey: boolean) {
    const open = this.open;
    const tickMs = (this.clock?.tick_seconds ?? 15) * 1000;
    const c = await this.guard("clock", () => this.api.clock());
    if (c) {
      this.clock = c;
      const r = String(c.round);
      if (this.roundStartTick[r] == null) this.roundStartTick[r] = c.tick;
    }
    if (!this.catalog || this.due("catalog", 10 * 60_000)) {
      const cat = await this.guard("catalog", () => this.api.catalog());
      if (cat) this.catalog = cat;
    }
    if (this.due("schedule", 60_000)) {
      const s = await this.guard("schedule", () => this.api.schedule());
      if (s) {
        this.schedule = s.upcoming;
        this.scheduleNow = s.now_hours;
      }
      const d = await this.guard("dealers", () => this.api.dealers());
      if (d) this.dealers = d.personas;
      this.loadReserved();
    }
    if (this.due("leaderboard", open ? 60_000 : 5 * 60_000)) {
      const lb = await this.guard("leaderboard", () => this.api.leaderboard());
      if (lb) {
        this.leaderboard = lb;
        const last = this.lbHistory[this.lbHistory.length - 1];
        const teams: LbPoint["teams"] = {};
        for (const t of lb.teams) teams[t.team] = { s: t.score, n: t.negotiating, m: t.market };
        if (!last || last.tick !== lb.tick || JSON.stringify(last.teams) !== JSON.stringify(teams)) {
          this.lbHistory.push({ ts: Date.now(), tick: lb.tick, teams });
          this.save();
        }
      }
    }
    if (this.due("feed", open ? tickMs : 60_000)) {
      const f = await this.guard("feed", () => this.api.feed(500));
      if (f) this.addFeed(f.events);
    }
    if (this.due("venues", open ? 2 * tickMs : 120_000)) {
      const v = await this.guard("venues", () => this.api.venues());
      if (v) this.venues = v.venues;
      const n = await this.guard("news", () => this.api.news());
      if (n) this.news = n.news ?? n.items ?? (Array.isArray(n) ? n : []);
    }
    if (this.due("boards", open ? 2 * tickMs : 120_000)) await this.refreshBoards();
    if (!hasKey) return;
    if (this.due("me", open ? tickMs : 60_000)) {
      const me = await this.guard("me", () => this.api.me());
      if (me) {
        this.me = me;
        if (this.catalog) {
          if (!this.values) this.values = new Values(this.catalog, me.affinity, me.assets);
          else this.values.update(me.affinity, me.assets);
        }
      }
      const offers = await this.guard("my_offers", () => this.api.myOffers());
      if (offers) this.myOffers = offers.offers ?? offers.mine ?? [];
    }
    if (this.due("duels", open ? tickMs : 60_000)) {
      const d = await this.guard("duels", () => this.api.duels());
      if (d) this.duels = d.duels;
    }
    if (this.due("duels_done", open ? 120_000 : 600_000)) {
      const d = await this.guard("duels_done", () => this.api.duels(true));
      if (d) this.duelsDone = d.duels;
    }
    if (this.due("deal_threads", open ? 60_000 : 600_000)) {
      const t = await this.guard("deal_threads", () => this.api.myThreads("deal"));
      if (t) this.dealThreads = t.threads;
    }
  }

  async refreshBoards() {
    const live = this.venues.filter((v) => v.status === "open").map((v) => v.venue);
    const ids = ["rastro", ...live.filter((v) => v !== "rastro")].slice(0, 12);
    await Promise.all(ids.map(async (id) => {
      const b = await this.guard(`board ${id}`, () => this.api.board(id));
      if (b) this.boards.set(id, b.offers ?? []);
    }));
  }

  addFeed(events: FeedEvent[]) {
    const fresh = events.filter((e) => !this.feedIds.has(e.id)).sort((a, b) => a.id - b.id);
    if (!fresh.length) return;
    for (const e of fresh) {
      this.feedIds.add(e.id);
      this.feed.push(e);
    }
    if (this.feed.length > 20000) this.feed = this.feed.slice(-20000);
    try {
      appendFileSync(join(this.dataDir, "feed.jsonl"), fresh.map((e) => JSON.stringify(e)).join("\n") + "\n");
    } catch {
      /* best effort */
    }
  }

  venueFee(venue: string | null): { bps: number; perCard: number } {
    if (!venue || venue === "rastro") return { bps: 500, perCard: 1 };
    const v = this.venues.find((x) => x.venue === venue);
    return { bps: v?.fee_bps ?? 0, perCard: v?.fee_per_card ?? 0 };
  }

  /** The first tick we know of the current round (Sunday is round 3). */
  currentRoundStart(): number {
    if (process.env.ROUND_START_TICK) return Number(process.env.ROUND_START_TICK);
    const r = this.clock?.round ?? 0;
    // Saturday's round ended at tick 1445: a server started late on Sunday must not count Sunday from its start
    if (r === 3) return Math.min(this.roundStartTick["3"] ?? Infinity, 1445);
    return this.roundStartTick[String(r)] ?? this.tick;
  }
}
