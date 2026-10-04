/**
 * The Bazaar SDK for TypeScript: the same routes as the official `bazaar_sdk.py`, typed.
 *
 *   const b = new Bazaar(process.env.BAZAAR_URL!, process.env.BAZAAR_KEY!);
 *   const me = await b.me();
 *
 * A refused request throws BazaarError with the server's code ("wait_for_tick", "insufficient_cash", ...).
 * Every instance shares one client-side limiter: the server allows 5 requests/s per key (bursts of 20), and the
 * deal, duel and market agents plus the web server all speak with the same key.
 */
import type {
  BrokerBook, Catalog, Clock, Dealer, Duel, FeedEvent, Leaderboard, Me, Offer, ScheduleItem, Thread, Venue,
} from "./types.js";

export class BazaarError extends Error {
  constructor(public code: string, message = "", public status = 0, public extra: Record<string, unknown> = {}) {
    super(message ? `${code}: ${message}` : code);
  }
}

class Limiter {
  private tokens: number;
  private last = Date.now();
  constructor(private ratePerSec: number, private burst: number) {
    this.tokens = burst;
  }
  async take(): Promise<void> {
    for (;;) {
      const now = Date.now();
      this.tokens = Math.min(this.burst, this.tokens + ((now - this.last) / 1000) * this.ratePerSec);
      this.last = now;
      if (this.tokens >= 1) {
        this.tokens -= 1;
        return;
      }
      await sleep(((1 - this.tokens) / this.ratePerSec) * 1000);
    }
  }
}

// the server agents trade on the same key and its 5 requests/s: this process keeps well under a third of that
const sharedLimiter = new Limiter(Number(process.env.BAZAAR_RPS ?? 1.5), 6);

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

type Query = Record<string, string | number | boolean | undefined | null>;

class Http {
  constructor(
    protected url: string,
    private headers: Record<string, string>,
    protected opts: { timeoutMs: number; waitOnTick: boolean; retries: number },
  ) {
    this.url = url.replace(/\/$/, "");
  }

  async call<T = any>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
    let url = this.url + path;
    if (query) {
      const q = Object.entries(query).filter(([, v]) => v !== undefined && v !== null);
      if (q.length) url += "?" + new URLSearchParams(q.map(([k, v]) => [k, String(v)])).toString();
    }
    for (let attempt = 0; ; attempt++) {
      await sharedLimiter.take();
      let err: BazaarError;
      try {
        const res = await fetch(url, {
          method,
          headers: { ...this.headers, Accept: "application/json", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
          body: body !== undefined ? JSON.stringify(body) : undefined,
          signal: AbortSignal.timeout(this.opts.timeoutMs),
        });
        const text = await res.text();
        let data: any = {};
        try {
          data = text ? JSON.parse(text) : {};
        } catch {
          throw new BazaarError("bad_response", `${method} ${path}: not JSON`, res.status);
        }
        if (res.ok) return data as T;
        err = new BazaarError(String(data?.error ?? `http_${res.status}`), String(data?.message ?? res.statusText), res.status, data);
      } catch (e) {
        if (e instanceof BazaarError) err = e;
        else {
          err = new BazaarError("network", `${method} ${path}: ${(e as Error).message}`);
          if (method !== "GET") throw err; // a write may have landed: never repeat it blindly
        }
      }
      if (attempt >= this.opts.retries) throw err;
      if (err.code === "rate_limited") await sleep(250 * (attempt + 1));
      else if (err.code === "network") await sleep(500 * (attempt + 1));
      else if (err.code === "wait_for_tick" && this.opts.waitOnTick) await this.sleepUntilNextTick();
      else throw err;
    }
  }

  async sleepUntilNextTick(): Promise<void> {
    try {
      const c = await this.call<Clock>("GET", "/api/clock");
      await sleep(Math.min(65, Math.max(0.05, c.next_tick_in ?? 1)) * 1000 + 200);
    } catch {
      await sleep(1000);
    }
  }
}

export class Bazaar extends Http {
  constructor(url: string, public key: string, opts: Partial<{ timeoutMs: number; waitOnTick: boolean; retries: number }> = {}) {
    super(url, key ? { "X-Team-Key": key } : {}, { timeoutMs: 15000, waitOnTick: false, retries: 2, ...opts });
  }

  // public
  clock = () => this.call<Clock>("GET", "/api/clock");
  catalog = () => this.call<Catalog>("GET", "/api/catalog");
  leaderboard = () => this.call<Leaderboard>("GET", "/api/leaderboard");
  feed = (limit = 500) => this.call<{ events: FeedEvent[] }>("GET", "/api/feed", undefined, { limit });
  schedule = () => this.call<{ now_hours: number; upcoming: ScheduleItem[] }>("GET", "/api/schedule");
  dealers = () => this.call<{ personas: Dealer[] }>("GET", "/api/dealers");
  levels = () => this.call<{ levels: any[] }>("GET", "/api/levels");
  news = () => this.call<any>("GET", "/api/news");
  venues = () => this.call<{ venues: Venue[] }>("GET", "/api/venues");
  board = (venue = "rastro") => this.call<{ offers: Offer[] }>("GET", `/api/venues/${venue}/offers`);
  card = (assetId: number) => this.call<any>("GET", `/api/cards/${assetId}`);

  // your team
  me = () => this.call<Me>("GET", "/api/me");
  value = (card: string) => this.call<{ card: string; your_value: number }>("GET", "/api/me/value", undefined, { card });
  myThreads = (status?: string) => this.call<{ threads: Thread[] }>("GET", "/api/me/threads", undefined, { status });
  myOffers = () => this.call<any>("GET", "/api/me/offers");

  // negotiation threads
  openThread = (withId: string, topic?: Record<string, unknown>, venue?: string) =>
    this.call<Thread>("POST", "/api/threads", { with: withId, ...(topic ? { topic } : {}), ...(venue ? { venue } : {}) });
  thread = (id: number) => this.call<Thread>("GET", `/api/threads/${id}`);
  say = (id: number, text: string, price?: number, offer?: { give: unknown; want: unknown }) =>
    this.call("POST", `/api/threads/${id}/messages`, {
      text,
      ...(price !== undefined ? { price: Math.round(price) } : {}),
      ...(offer ? { offer } : {}),
    });
  closeThread = (id: number) => this.call("POST", `/api/threads/${id}/close`);

  // offers
  listOffer = (give: unknown, want: unknown, venue?: string, to?: string, expiresInTicks = 40) =>
    this.call<Offer>("POST", "/api/offers", { give, want, expires_in_ticks: expiresInTicks, ...(venue ? { venue } : {}), ...(to ? { to } : {}) });
  cancel = (id: number) => this.call("DELETE", `/api/offers/${id}`);
  accept = (id: number, assets?: number[]) => this.call("POST", `/api/offers/${id}/accept`, assets?.length ? { assets } : {});
  openPack = (assetId: number) => this.call("POST", `/api/packs/${assetId}/open`);
  flag = (messageId: number, reason = "") => this.call("POST", "/api/flags", { message_id: messageId, reason });

  // duels
  duels = (done = false) => this.call<{ duels: Duel[] }>("GET", "/api/duels", undefined, done ? { done: true } : undefined);
  duelSay = (id: number, text: string, price?: number, days?: number | null) => {
    const body: Record<string, unknown> = { text };
    if (price !== undefined) {
      body.price = Math.round(price);
      if (days !== undefined && days !== null) body.offer = { price: Math.round(price), days: Math.round(days) };
    }
    return this.call("POST", `/api/duels/${id}/messages`, body);
  };
  duelAccept = (id: number) => this.call("POST", `/api/duels/${id}/accept`);
}

export class Broker extends Http {
  constructor(url: string, brokerKey: string) {
    super(url, { "X-Broker-Key": brokerKey }, { timeoutMs: 15000, waitOnTick: false, retries: 2 });
  }
  book = () => this.call<BrokerBook>("GET", "/api/broker/book");
  clock = () => this.call<Clock>("GET", "/api/clock");
  match = (sell: string | number, buy: string | number, price: number) =>
    this.call("POST", "/api/broker/matches", { sell, buy, price: Math.round(price) });
  announce = (text: string) => this.call("POST", "/api/broker/announce", { text });
}
