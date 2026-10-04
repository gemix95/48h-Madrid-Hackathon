/**
 * The war room server: runs the three agents and serves the UI.
 *
 *   cd platform && npm install && npm run build && npm start      # http://localhost:8787
 *
 * Env: BAZAAR_URL, BAZAAR_KEY (read from ../bazaar.env when unset), BROKER_KEY, PORT (8787), WAR_ROOM_PASSWORD,
 * LIVE_AGENTS (e.g. "duel,market"), DEAL_DEALERS (e.g. "banco"), ACCEPT_PARITY (any|odd|even), DATA_DIR.
 */
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, extname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { Runner } from "../engine/runner.js";
import { album, duelStats, moves, rivals, scoreStory, timeline, todayTrades } from "../engine/insights.js";
import { decideDuel } from "../agents/duelBrain.js";
import { compare } from "../sim/marketSim.js";
import type { DealAgent } from "../agents/dealAgent.js";
import type { MarketAgent } from "../agents/marketAgent.js";
import type { AgentId, Mode } from "../engine/log.js";
import { LEVERS } from "../game/scoring.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "../..");
const REPO = resolve(ROOT, "..");

function loadEnvFile(path: string) {
  if (!existsSync(path)) return;
  for (const line of readFileSync(path, "utf8").split("\n")) {
    const m = line.match(/^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)=(.*)$/);
    if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2].trim().replace(/^["']|["']$/g, "");
  }
}
loadEnvFile(join(REPO, "bazaar.env"));
loadEnvFile(join(ROOT, ".env"));

const PORT = Number(process.env.PORT ?? 8787);
const PASSWORD = process.env.WAR_ROOM_PASSWORD ?? "";
const live = new Set((process.env.LIVE_AGENTS ?? "").split(",").map((s) => s.trim()).filter(Boolean));

const runner = new Runner({
  url: process.env.BAZAAR_URL ?? "https://bazaar.causaprima.ai",
  key: process.env.BAZAAR_KEY ?? "",
  brokerKey: process.env.BROKER_KEY || null,
  dataDir: process.env.DATA_DIR ?? join(ROOT, "data"),
  reservedFile: process.env.RESERVED_FILE ?? join(REPO, "team13", "reserved.json"),
  acceptParity: (process.env.ACCEPT_PARITY as "any" | "odd" | "even") ?? "any",
  initialModes: Object.fromEntries([...live].map((a) => [a, "live"])) as Partial<Record<AgentId, Mode>>,
});

let marketSim: ReturnType<typeof compare> & { hard?: ReturnType<typeof compare> } | null = null;
setTimeout(() => {
  marketSim = { ...compare(1500, { arrivals: true }), hard: compare(1500, { arrivals: true, hard: true }) };
}, 2000);

function snapshot() {
  const w = runner.world;
  const deal = runner.agents.find((a) => a.id === "deal") as DealAgent;
  const market = runner.agents.find((a) => a.id === "market") as MarketAgent;
  const ctx = runner.ctx();
  return {
    now: Date.now(),
    hasKey: !!runner.cfg.key,
    passwordSet: !!PASSWORD,
    clock: w.clock,
    me: w.me ? { id: w.me.id, name: w.me.name, cash: w.me.cash, level: w.me.level, unlocked: w.me.unlocked, venue: w.me.venue, affinity: w.me.affinity } : null,
    story: scoreStory(w),
    levers: LEVERS,
    timeline: timeline(w),
    moves: moves(w, runner),
    agents: runner.agents.map((a) => ({ id: a.id, name: a.name, role: a.role, mode: runner.modes[a.id], status: a.status(ctx), recent: runner.log.recent(6, a.id) })),
    decisions: runner.log.recent(150),
    duels: {
      live: w.duels.filter((d) => d.status === "open").map((d) => ({ ...d, suggestion: decideDuel(d, w.tick) })),
      stats: duelStats(w),
    },
    deal: { ladder: deal.ladder, opportunities: deal.opportunities.slice(0, 15).map((o) => ({ id: o.offer.id, maker: o.offer.maker, venue: o.offer.venue, score: +o.score.toFixed(1), why: o.why, give: o.offer.give, want: o.offer.want })), minGain: deal.minGain, trades: todayTrades(w).slice(0, 40) },
    market: {
      venues: w.venues.map((v) => ({ id: v.venue, name: v.name, owner: v.owner, status: v.status, fee_bps: v.fee_bps, fee_per_card: v.fee_per_card, mechanism: (v.rules as any)?.mechanism, trades: v.trades ?? 0, volume: v.volume ?? 0, value_created: v.value_created ?? 0, starter: v.starter, house: v.house })),
      sim: marketSim, broker: !!runner.broker, lastPlan: market.lastPlan, bench: (market.book?.bench_offers ?? []).length,
    },
    album: album(w),
    rivals: rivals(w),
    news: (w.news ?? []).slice(0, 12),
    dealers: w.dealers.map((d) => ({ id: d.id, name: d.name, level: d.level, status: d.status, kind: d.kind, title: d.title, traits: d.traits, menu: d.menu })),
    health: { loops: runner.loops, errors: w.errors.slice(-8), feedEvents: w.feed.length, lbPoints: w.lbHistory.length, roundStart: w.currentRoundStart(), refreshed: w.lastRefresh },
  };
}

function authed(req: IncomingMessage): boolean {
  if (!PASSWORD) return true;
  const h = req.headers.authorization ?? "";
  if (!h.startsWith("Basic ")) return false;
  const [, pass] = Buffer.from(h.slice(6), "base64").toString().split(":");
  return pass === PASSWORD;
}

function send(res: ServerResponse, code: number, body: unknown, type = "application/json") {
  res.writeHead(code, { "Content-Type": type, "Cache-Control": "no-store" });
  res.end(type === "application/json" ? JSON.stringify(body) : (body as string | Buffer));
}

async function readBody(req: IncomingMessage): Promise<any> {
  const chunks: Buffer[] = [];
  for await (const c of req) chunks.push(c as Buffer);
  try {
    return JSON.parse(Buffer.concat(chunks).toString() || "{}");
  } catch {
    return {};
  }
}

const MIME: Record<string, string> = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json" };
const DIST = join(ROOT, "web", "dist");

createServer(async (req, res) => {
  if (!authed(req)) {
    res.writeHead(401, { "WWW-Authenticate": 'Basic realm="Team 13 war room"' });
    return res.end("Login: any user, the war room password.");
  }
  const url = new URL(req.url ?? "/", "http://x");
  try {
    if (url.pathname === "/api/snapshot") return send(res, 200, snapshot());
    if (url.pathname === "/api/decisions") return send(res, 200, runner.log.recent(Number(url.searchParams.get("n") ?? 300), (url.searchParams.get("agent") as AgentId) || undefined));
    const m = url.pathname.match(/^\/api\/agents\/(deal|duel|market)\/mode$/);
    if (m && req.method === "POST") {
      const body = await readBody(req);
      const mode = body.mode as Mode;
      if (!["off", "shadow", "live"].includes(mode)) return send(res, 400, { error: "bad_mode" });
      if (mode === "live" && !runner.cfg.key) return send(res, 400, { error: "no_key", message: "Set BAZAAR_KEY first." });
      if (mode === "live" && !PASSWORD && process.env.ALLOW_LIVE_WITHOUT_PASSWORD !== "1")
        return send(res, 403, { error: "password_required", message: "Start the server with WAR_ROOM_PASSWORD to allow LIVE mode from the browser (or LIVE_AGENTS=... at start)." });
      runner.setMode(m[1] as AgentId, mode);
      return send(res, 200, { ok: true, modes: runner.modes });
    }
    if (url.pathname.startsWith("/api/")) return send(res, 404, { error: "not_found" });
    let file = join(DIST, url.pathname === "/" ? "index.html" : url.pathname);
    if (!file.startsWith(DIST) || !existsSync(file) || statSync(file).isDirectory()) file = join(DIST, "index.html");
    if (!existsSync(file)) return send(res, 200, "Run `npm run build` first (the UI is not built yet).", "text/plain");
    return send(res, 200, readFileSync(file), MIME[extname(file)] ?? "application/octet-stream");
  } catch (e) {
    return send(res, 500, { error: "server", message: String(e) });
  }
}).listen(PORT, () => {
  console.log(`Team 13 war room on http://localhost:${PORT}  (key: ${runner.cfg.key ? "yes" : "no"}, broker: ${runner.broker ? "yes" : "no"}, modes: ${JSON.stringify(runner.modes)})`);
});

runner.loop();
