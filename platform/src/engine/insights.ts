/**
 * Turns raw game state into things a human can act on: where our points come from and leak, what happens next
 * and what it means for us, what to do now, how each rival plays.
 */
import type { Runner } from "./runner.js";
import type { World } from "./world.js";
import type { Duel, FeedEvent, ScheduleItem, Score } from "../sdk/types.js";
import type { DealAgent } from "../agents/dealAgent.js";

export type Severity = "critical" | "high" | "normal" | "good";

export interface Move {
  severity: Severity;
  title: string;
  why: string;
  who: "you" | "deal agent" | "duel agent" | "market agent" | "team";
}

const ME = "t13";

// ---------------------------------------------------------------------------------------------------- the score
export function scoreStory(w: World) {
  const lb = w.leaderboard;
  const s: Partial<Score> = w.me?.score ?? lb?.teams.find((t) => t.team === ME) ?? {};
  const teams = lb?.teams ?? [];
  const rank = s.rank ?? teams.findIndex((t) => t.team === ME) + 1;
  const leader = teams[0];
  const above = rank > 1 ? teams[rank - 2] : null;
  const below = teams[rank] ?? null;
  const best = (k: "negotiating" | "market") => Math.max(0, ...teams.map((t) => t[k] ?? 0));
  const avg = (k: "negotiating" | "market") => (teams.length ? teams.reduce((a, t) => a + (t[k] ?? 0), 0) / teams.length : 0);
  const components = [
    {
      id: "negotiating", title: "Negotiating", max: 30, value: s.negotiating ?? 0, best: best("negotiating"), avg: avg("negotiating"),
      parts: [
        { id: "duels", title: "Duels", raw: s.duel_points ?? null, plain: "points from 1-vs-1 duels (surplus inside our limit, shrunk by each round of talk)" },
        { id: "ladder", title: "Dealer ladder", raw: s.ladder_points ?? null, plain: "share of each dealer's price range captured, best 3 deals per dealer" },
        { id: "trades", title: "Team trades", raw: s.neg_points ?? null, plain: "value gained trading with teams at OUR values; gains capped at +50 each, losses in full" },
      ],
    },
    {
      id: "market", title: "Market-making", max: 30, value: s.market ?? 0, best: best("market"), avg: avg("market"),
      parts: [
        { id: "bench", title: "Market Test", raw: s.bench_points ?? null, plain: `efficiency ${s.bench_efficiency != null ? Math.round(s.bench_efficiency * 100) + "%" : "–"}; 0.5 = the free stall, 1 = the best three brokers` },
        { id: "venue", title: "Our market", raw: s.mm_points ?? null, plain: "value other teams create trading on our market" },
      ],
    },
  ];
  const leaks: string[] = [];
  if ((s.neg_points ?? 0) < 0) leaks.push(`Team trades are at ${s.neg_points!.toFixed(1)} P: we gave away more value than we received. Every loss counts in full, every gain only up to +50.`);
  if ((s.bench_points ?? 1) < 0.5) leaks.push(`Market Test at ${s.bench_points!.toFixed(3)}, below the free stall's 0.5: some sessions ran with our market closed or the broker down, and those count 0.`);
  if ((s.mm_points ?? 0) === 0) leaks.push("No other team has traded on our market yet (venue value 0).");
  return {
    score: s.score ?? 0, rank, of: teams.length, level: s.level, deals: s.deals,
    leader: leader ? { team: leader.team, name: leader.name, score: leader.score, gap: +(leader.score - (s.score ?? 0)).toFixed(2) } : null,
    above: above ? { team: above.team, name: above.name, score: above.score, gap: +(above.score - (s.score ?? 0)).toFixed(2) } : null,
    below: below ? { team: below.team, name: below.name, score: below.score, gap: +((s.score ?? 0) - below.score).toFixed(2) } : null,
    components, leaks,
    rounds: lb?.rounds ?? [],
    raw: s,
  };
}

// ---------------------------------------------------------------------------------------------------- timeline
/**
 * Seconds until a schedule item. The schedule is in game hours; a day's opening is pinned to a wall time
 * (Sunday 09:00 Madrid = game hour 16.65), so anything after an upcoming opening counts from that wall time.
 * Saturday's clock was paused for ~3 h, so items scheduled before Sunday's opening hour are left-overs that may
 * fire at the opening or never: they get no ETA.
 */
export function etaSeconds(w: World, item: ScheduleItem): number | null {
  const c = w.clock;
  if (!c) return null;
  if (item.wall) return (Date.parse(item.wall) - Date.now()) / 1000;
  const now = w.scheduleNow || c.t_hours;
  const opening = w.schedule.find((s) => s.action === "day_opens" && s.wall && Date.parse(s.wall) > Date.now());
  if (opening) {
    if (item.at_hours < opening.at_hours) return null;
    return (Date.parse(opening.wall!) - Date.now()) / 1000 + (item.at_hours - opening.at_hours) * 3600;
  }
  if (c.doors !== "open" || c.paused) return null;
  return (item.at_hours - now) * 3600;
}

export function explainEvent(item: ScheduleItem, w: World): { title: string; meaning: string; owner: Move["who"] } {
  const p = item.params ?? {};
  const aff = w.me?.affinity ?? {};
  switch (item.action) {
    case "bench":
      return {
        title: String(p.name ?? "Market Test"),
        meaning: `${p.traders ?? "?"} synthetic traders for ${p.ticks ?? "?"} ticks. Our market must be OPEN and the broker matching every tick: a session with no open venue scores 0.${/hard/i.test(item.note) ? " Hard: firmer, more impatient traders; match crossing pairs at once." : ""}`,
        owner: "market agent",
      };
    case "duels": {
      const issues = (p.issues as string[] | undefined) ?? ["price"];
      return {
        title: String(p.name ?? "Duels"),
        meaning: `${p.rounds ?? "?"} round-robin${Number(p.rounds) > 1 ? "s" : ""}, ${p.duel_ticks ?? "?"} ticks each, ${Math.round(Number(p.decay ?? 0) * 100)}% of the pie lost per round of talk, up to ${p.max_concurrent ?? "?"} at once. ${issues.includes("days") ? "Price AND delivery day: as buyer each day costs us, as seller each day pays us. Close in 2–4 rounds." : "Price only."}`,
        owner: "duel agent",
      };
    }
    case "set_release": {
      const set = String(p.set ?? "");
      const m = aff[set];
      return { title: item.note, meaning: m != null ? `Our multiplier for ${set} is ×${m}. ${m < 1 ? "Worth less to us than to most teams: sell what we pull to collectors, unless we can complete its page (page bonus +25%)." : "A set we value: collect it."}` : "A new set enters the packs.", owner: "deal agent" };
    }
    case "round":
      return { title: item.note, meaning: `A new scoring round (weight ${p.weight ?? 1}). Everything restarts from zero for this round: duels, ladder slots, team-trade balance, Market Test sessions.`, owner: "team" };
    case "grant_all":
      return { title: item.note, meaning: "Cash for the day. Cash never scores by itself: spend it only on deals worth more to us than their price.", owner: "team" };
    case "persona":
      return { title: `${String(p.id ?? "")} ${p.enabled === false ? "closes" : "opens"}`, meaning: p.enabled === false ? "After this, no more ladder deals with this dealer: fill its best-3 slots before." : "A dealer opens.", owner: "deal agent" };
    case "end_round":
      return { title: "Scores freeze", meaning: "Nothing after this counts.", owner: "team" };
    case "day_closes":
      return { title: item.note, meaning: "The clock stops. Offers stay open, nothing settles.", owner: "team" };
    case "day_opens":
      return { title: item.note, meaning: `Ticks every ${p.tick_seconds ?? "?"} s.`, owner: "team" };
    case "announce":
      return { title: item.note.replace(/^./, (x) => x.toUpperCase()), meaning: "An announcement on the big screen.", owner: "team" };
    default:
      return { title: item.note || item.action, meaning: "", owner: "team" };
  }
}

export function timeline(w: World) {
  // the five stall closings arrive as five rows: show them as one
  const items: (ScheduleItem & { group?: string[] })[] = [];
  for (const it of w.schedule) {
    const prev = items[items.length - 1];
    if (prev && it.action === "persona" && prev.action === "persona" && prev.at_hours === it.at_hours) {
      prev.group = [...(prev.group ?? [String(prev.params?.id)]), String(it.params?.id)];
      continue;
    }
    items.push({ ...it });
  }
  return items
    .filter((it) => !(it.wall && Date.parse(it.wall) < Date.now()))
    .map((it) => {
      const e = explainEvent(it, w);
      if (it.group) {
        e.title = `Dealers close: ${it.group.join(", ")}`;
        e.meaning = "Finale. No more ladder deals after this: fill every dealer's best-3 slots before.";
      }
      const eta = etaSeconds(w, it);
      return {
        at_hours: it.at_hours, action: it.action, eta, leftover: eta == null && !!w.clock && w.clock.doors !== "open",
        madrid: eta != null ? new Date(Date.now() + eta * 1000).toLocaleTimeString("en-GB", { timeZone: "Europe/Madrid", hour: "2-digit", minute: "2-digit" }) : null,
        ...e,
      };
    });
}

// ---------------------------------------------------------------------------------------------------- our trades
export interface TradeRow { tick: number; with: string; venue: string | null; gave: string[]; got: string[]; cash: number; dir: "paid" | "received" | "swap"; estValue: number | null }

/** Our settled team trades since the round started, valued (roughly) at today's private values. */
export function todayTrades(w: World): TradeRow[] {
  const start = w.currentRoundStart();
  const v = w.values;
  const out: TradeRow[] = [];
  for (const e of w.feed) {
    if (e.type !== "settlement" || e.tick < start) continue;
    const p = e.payload;
    if (!(p.parties ?? []).includes(ME) || p.persona) continue;
    const items = p.items ?? [];
    const got = items.filter((i: any) => i.to === ME && i.ref).map((i: any) => i.ref);
    const gave = items.filter((i: any) => i.frm === ME && i.ref).map((i: any) => i.ref);
    // a settlement's price is the total for all its items; the side that gets the cards pays it
    const price = p.price ?? 0;
    const dir: TradeRow["dir"] = got.length && !gave.length ? "paid" : gave.length && !got.length ? "received" : "swap";
    let est: number | null = null;
    if (v) {
      const kept = got.filter((r: string) => (v.held.get(r) ?? 0) > 0);
      const valIn = kept.length ? v.lossOfRemoving(kept) : 0; // still ours: what losing them would cost now
      const valOut = gave.length ? v.gainOfAdding(gave) : 0;   // gone: what having them back would add now
      const cash = dir === "paid" ? -price : dir === "received" ? price : 0;
      est = Number.isFinite(valIn) ? +(valIn - valOut + cash).toFixed(1) : null;
    }
    out.push({ tick: e.tick, with: (p.parties as string[]).find((x) => x !== ME) ?? "?", venue: p.venue ?? null, gave, got, cash: price, dir, estValue: est });
  }
  return out.reverse();
}

// ---------------------------------------------------------------------------------------------------- duels
export function duelStats(w: World) {
  const by = new Map<number, Duel[]>();
  for (const d of w.duelsDone) by.set(d.session, [...(by.get(d.session) ?? []), d]);
  const sessions = [...by.entries()].sort((a, b) => a[0] - b[0]).map(([session, xs]) => {
    const r = (role: string) => {
      const ys = xs.filter((x) => x.role === role);
      const deals = ys.filter((x) => x.status === "deal");
      return {
        n: ys.length, deals: deals.length,
        mean: ys.length ? ys.reduce((s, x) => s + (x.result ?? 0), 0) / ys.length : 0,
        negative: ys.filter((x) => (x.result ?? 0) < 0).length,
        silent: ys.filter((x) => x.status !== "deal" && !x.messages.some((m) => m.from !== "you")).length,
      };
    };
    return {
      session, issues: xs[0]?.issues ?? [], decay: xs[0]?.decay_per_round ?? null, n: xs.length,
      total: xs.reduce((s, x) => s + (x.result ?? 0), 0),
      deals: xs.filter((x) => x.status === "deal").length,
      buyer: r("buyer"), seller: r("seller"),
    };
  });
  const worst = [...w.duelsDone].filter((d) => d.result != null).sort((a, b) => (a.result ?? 0) - (b.result ?? 0)).slice(0, 6)
    .map((d) => ({ duel: d.duel, session: d.session, role: d.role, item: d.item, limit: d.your_limit, price: d.price, days: d.days, w: d.your_days_weight, result: d.result, rival: d.rival }));
  const best = [...w.duelsDone].filter((d) => d.result != null).sort((a, b) => (b.result ?? 0) - (a.result ?? 0)).slice(0, 6)
    .map((d) => ({ duel: d.duel, session: d.session, role: d.role, item: d.item, limit: d.your_limit, price: d.price, days: d.days, w: d.your_days_weight, result: d.result, rival: d.rival }));
  return { sessions, worst, best };
}

// ---------------------------------------------------------------------------------------------------- rivals
export function rivals(w: World) {
  const start = w.currentRoundStart();
  const lb = w.leaderboard?.teams ?? [];
  const hist = w.lbHistory;
  const act = new Map<string, { dealer: number; team: number; listed: number; bought: Record<string, number>; sold: Record<string, number>; last: number; volume: number }>();
  const get = (t: string) => {
    let a = act.get(t);
    if (!a) act.set(t, (a = { dealer: 0, team: 0, listed: 0, bought: {}, sold: {}, last: 0, volume: 0 }));
    return a;
  };
  for (const e of w.feed as FeedEvent[]) {
    if (e.tick < start) continue;
    const p = e.payload;
    if (e.type === "settlement") {
      for (const party of (p.parties ?? []) as string[]) {
        if (!party.startsWith("t")) continue;
        const a = get(party);
        if (p.persona) a.dealer++;
        else a.team++;
        a.volume += p.price ?? 0;
        a.last = Math.max(a.last, e.tick);
        for (const i of p.items ?? []) {
          if (!i.set) continue;
          if (i.to === party) a.bought[i.set] = (a.bought[i.set] ?? 0) + 1;
          if (i.frm === party) a.sold[i.set] = (a.sold[i.set] ?? 0) + 1;
        }
      }
    } else if (e.type === "offer.listed" && e.actor?.startsWith("t")) {
      const a = get(e.actor);
      a.listed++;
      a.last = Math.max(a.last, e.tick);
    }
  }
  return lb.map((t) => {
    const series = hist.map((h) => h.teams[t.team]?.s).filter((x): x is number => x != null).slice(-120);
    const a = act.get(t.team);
    const top = (o: Record<string, number> = {}) => Object.entries(o).sort((x, y) => y[1] - x[1]).slice(0, 2).map(([k]) => k);
    const venue = w.venues.find((v) => v.owner === t.team && v.status === "open" && !v.starter);
    return {
      team: t.team, name: t.name, rank: t.rank, score: t.score, negotiating: t.negotiating, market: t.market, level: t.level,
      pages: t.pages_complete, album: `${t.album_filled}/${t.album_slots}`, deals: t.deals, luck: t.luck, badges: t.badges,
      venue: venue ? { id: venue.venue, name: venue.name, fee_bps: venue.fee_bps, trades: venue.trades ?? 0, value_created: venue.value_created ?? 0, mechanism: (venue.rules as any)?.mechanism } : null,
      series, trend: series.length >= 2 ? +(series[series.length - 1] - series[0]).toFixed(2) : 0,
      today: a ? { dealer: a.dealer, team: a.team, listed: a.listed, volume: a.volume, collects: top(a.bought), dumps: top(a.sold), lastTick: a.last } : null,
      us: t.team === ME,
    };
  });
}

// ---------------------------------------------------------------------------------------------------- moves
export function moves(w: World, r: Runner): Move[] {
  const out: Move[] = [];
  const c = w.clock;
  const s = w.me?.score;
  const deal = r.agents.find((a) => a.id === "deal") as DealAgent | undefined;
  if (!r.cfg.key) out.push({ severity: "critical", title: "No team key: everything is read-only", why: "Set BAZAAR_KEY (source ../bazaar.env) to see our cards, duels and score.", who: "you" });
  if (c && (c.doors === "closed" || c.paused)) {
    const opens = Date.parse(c.next_opens);
    const h = Number.isFinite(opens) ? (opens - Date.now()) / 3600_000 : null;
    out.push({ severity: "normal", title: c.doors === "closed" ? `Doors closed: ${c.next_name} opens ${h != null ? `in ${fmtH(h)}` : "soon"}` : "The clock is paused", why: "Nothing ticks and nothing settles. Offers stay open. Use the time to check the plan below.", who: "team" });
  }
  const venue = w.me?.venue;
  if (w.me && (!venue || venue.status !== "open")) out.push({ severity: "critical", title: "Our market is closed", why: "Every Market Test with no open venue scores 0 for us (that is why we sit below the free stall). Reopen it as a 0% board venue and never close it again today.", who: "you" });
  const trades = todayTrades(w);
  const losing = trades.filter((t) => (t.estValue ?? 0) < -2);
  if (losing.length) out.push({ severity: "critical", title: `${losing.length} team trade${losing.length > 1 ? "s" : ""} today look like losses at our values`, why: "Losses count in full. Check which module made them (Python trader, auctions, swaps) and switch it off on the Strategy tab.", who: "you" });
  if ((s?.neg_points ?? 0) < -20) out.push({ severity: "high", title: `Team trades: ${s!.neg_points!.toFixed(0)} P this round`, why: "A new round starts from 0. Rule for the day: never sell a card below what it is worth to us, never pay more than it is worth. Small sure gains beat volume: the number of trades never scores.", who: "team" });
  const nextDuels = w.schedule.find((x) => x.action === "duels");
  if (nextDuels) {
    const eta = etaSeconds(w, nextDuels);
    if (eta != null && eta < 3600) out.push({ severity: "high", title: `${nextDuels.params?.name ?? "Duels"} in ${fmtH(eta / 3600)}`, why: `Make sure exactly ONE duel player runs: the duel agent here (${r.modes.duel}) or the Python duels module, not both.`, who: "duel agent" });
  }
  const nextBench = w.schedule.find((x) => x.action === "bench");
  if (nextBench) {
    const eta = etaSeconds(w, nextBench);
    if (eta != null && eta < 1800) out.push({ severity: "high", title: `Market Test in ${fmtH(eta / 3600)}`, why: "Venue open, fee 0, mechanism board, broker running: check all four now.", who: "market agent" });
  }
  if (deal) {
    const empty = deal.ladder.filter((l) => l.dealsToday < 3);
    const closing = w.schedule.find((x) => x.action === "persona" && x.params?.enabled === false);
    if (empty.length) {
      const top = empty.slice(0, 3).map((l) => `${l.dealerName} (level ${l.level}: ${l.dealsToday}/3${l.target ? `, try ${l.target.label} ≤ ${l.target.cap} P` : ""})`).join("; ");
      out.push({ severity: "normal", title: `${empty.reduce((a, l) => a + 3 - l.dealsToday, 0)} ladder slots still empty today`, why: `An empty slot counts 0 and higher levels weigh more. ${top}.${closing ? " All dealers close at the finale." : ""}`, who: "deal agent" });
    }
    const opp = deal.opportunities.filter((o) => o.score >= deal.minGain);
    if (opp.length) out.push({ severity: "good", title: `${opp.length} board offer${opp.length > 1 ? "s" : ""} would gain us value right now`, why: `Best: #${opp[0].offer.id} from ${opp[0].offer.maker} on ${opp[0].offer.venue}, +${opp[0].score.toFixed(1)} (${opp[0].why}).`, who: "deal agent" });
  }
  const v = w.values;
  if (v && w.me) {
    const spares = v.spares(w.me.assets, w.reserved).filter((x) => x.loss < 15);
    if (spares.length) out.push({ severity: "normal", title: `${spares.length} spare card${spares.length > 1 ? "s" : ""} worth little to us`, why: `${spares.slice(0, 5).map((x) => `${x.asset.ref} (worth ${x.loss.toFixed(0)})`).join(", ")}. Sell only to a team that pays more than that; a collector of the set usually does.`, who: "deal agent" });
  }
  const story = scoreStory(w);
  if (story.above) out.push({ severity: "normal", title: `${story.above.gap.toFixed(2)} points to pass ${story.above.name}`, why: `${story.leader ? `The leader ${story.leader.name} is ${story.leader.gap.toFixed(2)} ahead. ` : ""}Biggest gaps vs the best team: negotiating ${(story.components[0].best - story.components[0].value).toFixed(1)}, market ${(story.components[1].best - story.components[1].value).toFixed(1)}.`, who: "team" });
  const order: Severity[] = ["critical", "high", "good", "normal"];
  return out.sort((a, b) => order.indexOf(a.severity) - order.indexOf(b.severity));
}

export function fmtH(h: number): string {
  if (!Number.isFinite(h)) return "–";
  if (h < 0) return "now";
  const m = Math.round(h * 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

export function album(w: World) {
  const v = w.values;
  if (!v || !w.me) return null;
  const cards = w.me.assets.filter((a) => a.kind === "card").map((a) => ({ id: a.id, ref: a.ref, name: a.name, rarity: a.rarity, set: a.set, serial: a.serial, value: a.your_value ?? v.lossOfRemoving([a.ref!]), reserved: w.reserved.has(a.ref!) || w.reserved.has(a.id) }));
  return {
    cash: w.me.cash, collection_value: w.me.collection_value, affinity: w.me.affinity,
    pages: v.pages(), cards, wishlist: v.wishlist(10), spares: v.spares(w.me.assets, w.reserved).map((x) => ({ id: x.asset.id, ref: x.asset.ref, loss: +x.loss.toFixed(1) })),
  };
}
