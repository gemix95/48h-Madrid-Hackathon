/**
 * How a deal scores, as the organisers' scoring slide (Sat 20:25) and RULES.md describe it. One place, so the
 * agents' decisions and the explanations in the UI never disagree.
 *
 *   deal value = value the cards add to our collection − cash paid + cash received   (at OUR private values)
 *
 *   team trade    gain counts up to +50 per trade, a loss counts in full
 *   dealer buy    a gain counts through the ladder (share of the dealer's price range), a loss counts in full
 *   dealer sale   below our value: the loss counts in full
 *   ladder        best 3 deals per dealer level count, a missing one counts 0, higher levels weigh more
 *   duel          our surplus (inside our limit) × (1 − decay)^rounds; outside our limit is negative; no deal is 0
 *
 * Score = 30 negotiating + 30 market-making + 40 judges. Rounds: Friday ×0.5, Saturday ×1, Sunday ×1.
 */
export const TEAM_GAIN_CAP = 50;

export function teamTradeScore(valueIn: number, cashIn: number, valueOut: number, cashOut: number): number {
  const gain = valueIn + cashIn - valueOut - cashOut;
  return gain > 0 ? Math.min(TEAM_GAIN_CAP, gain) : gain;
}

/** Venue fee on a cash amount: basis points of the price plus a per-card charge. */
export function venueFee(price: number, feeBps: number, feePerCard: number, cards: number): number {
  return Math.ceil((feeBps * price) / 10000) + feePerCard * cards;
}

/** Duel points for a deal: surplus inside our limit (days included) shrunk by the decay of each round of talk. */
export function duelSurplus(role: "buyer" | "seller", limit: number, price: number, days: number | null, daysWeight: number | null): number {
  const w = daysWeight ?? 0;
  const d = days ?? 0;
  return role === "seller" ? price - limit + w * d : limit - price - w * d;
}

export interface Lever {
  id: string;
  title: string;
  max: number;
  plain: string;
}

/** The point sources, in the words we show to humans. */
export const LEVERS: Lever[] = [
  { id: "duels", title: "Duels", max: 10, plain: "1-vs-1 negotiations with a rival team. Close inside your limit, fast: every round of talk shrinks the pie." },
  { id: "ladder", title: "Dealer ladder", max: 10, plain: "Your best 3 deals with each of the 5 dealers. Score = how much of the dealer's price range you captured. Empty slots count 0." },
  { id: "trades", title: "Team trades", max: 10, plain: "Value gained trading cards with other teams, at OUR values. A gain counts up to +50 per trade, a loss counts in full." },
  { id: "bench", title: "Market Test", max: 15, plain: "Every 2 hours every market gets the same fake book of buyers and sellers. Our broker scores the share of possible gains it realises. Free stall level = half points." },
  { id: "venue", title: "Our market", max: 15, plain: "Value other teams create by trading with each other on our market (0% fee)." },
  { id: "judges", title: "Judges", max: 40, plain: "Ideas and craft, judged by people at the end. The biggest single block." },
];
