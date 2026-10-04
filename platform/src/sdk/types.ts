// Shapes as the live server returns them (checked against Saturday's payloads, not only the SDK docstrings).

export type Rarity = "common" | "uncommon" | "rare" | "epic" | "legendary";

export interface Asset {
  id: number;
  kind: "card" | "pack";
  ref?: string;
  serial?: number;
  rarity?: Rarity;
  set?: string;
  print_run?: number;
  name?: string;
  your_value?: number;
}

export interface OfferSide {
  cash: number;
  assets: Asset[];
  types: string[]; // "card:LAV-03": any copy of that card
}

export interface Offer {
  id: number;
  maker: string;
  to: string | null;
  venue: string | null;
  thread: number | null;
  status: string;
  give: OfferSide;
  want: OfferSide;
  expires_tick: number;
  created_tick: number;
  final: boolean;
}

export interface Clock {
  tick: number;
  t_hours: number;
  tick_seconds: number;
  paused: boolean;
  next_tick_in: number;
  round: number;
  round_name: string;
  limits: Record<string, number>;
  doors: "open" | "closed";
  today: string;
  today_name: string;
  closes: string;
  next_opens: string;
  next_name: string;
  days: { day: string; name: string; opens: string; closes: string; tick_seconds: number }[];
}

export interface ScheduleItem {
  at_hours: number;
  action: string;
  note: string;
  params: Record<string, unknown>;
  wall?: string;
}

export interface Score {
  team: string;
  name: string;
  score: number;
  negotiating: number;
  market: number;
  neg_points?: number;
  mm_points?: number;
  duel_points?: number;
  ladder_points?: number;
  bench_efficiency?: number | null;
  bench_points?: number;
  bench_venue?: string | null;
  level: number;
  album_filled: number;
  album_slots: number;
  pages_complete: number;
  luck: number;
  deals: number;
  badges: string[];
  venue?: string | null;
  rank: number;
}

export interface Me {
  id: string;
  name: string;
  cash: number;
  level: number;
  unlocked: string[];
  affinity: Record<string, number>;
  assets: Asset[];
  collection_value: number;
  album: { pages: { set: string; name: string; have: number; of: number; complete: boolean; master: boolean }[]; filled: number; slots: number };
  tick: number;
  score: Score;
  venue: Venue | null;
  open_threads: number[];
}

export interface Venue {
  venue: string;
  name: string;
  owner: string | null;
  owner_name?: string;
  status: string;
  fee_bps: number;
  fee_per_card: number;
  rules: Record<string, unknown>;
  starter?: boolean;
  house?: boolean;
  trades?: number;
  volume?: number;
  value_created?: number;
  description?: string;
}

export interface Leaderboard {
  tick: number;
  t: number;
  round: number;
  rounds: { round: number; name: string; weight: number; status: string; phase: number }[];
  teams: Score[];
}

export interface DuelMessage {
  tick: number;
  from: string; // "you" or the rival's alias
  text: string;
  price: number | null;
  days: number | null;
}

export interface DuelOffer {
  id: number;
  price: number;
  tick: number;
  days: number;
}

export interface Duel {
  duel: number;
  session: number;
  status: string; // open | deal | no_deal
  role: "buyer" | "seller";
  item: string;
  issues: string[];
  your_days_weight: number | null;
  days_meaning: string | null;
  your_limit: number;
  limit_meaning: string;
  rival: string;
  deadline_tick: number;
  decay_per_round: number;
  rounds: number;
  your_offer: DuelOffer | null;
  rival_offer: DuelOffer | null;
  messages: DuelMessage[];
  result: number | null;
  price: number | null;
  days: number | null;
}

export interface ThreadMessage {
  id: number;
  tick: number;
  sender: string;
  text: string;
  offer?: Offer | null;
}

export interface Thread {
  id: number;
  kind: "persona" | "team";
  team: string;
  with: string;
  venue: string | null;
  topic: Record<string, unknown>;
  status: string; // open | deal | walked | closed | cooloff
  created_tick: number;
  standing_offers: Offer[];
  item: string | null;
  closed_reason: string | null;
  messages?: ThreadMessage[];
}

export interface DealerMenuItem {
  pack?: string;
  name?: string;
  rarity?: Rarity;
  sets?: string | string[];
  list_price?: number;
  opening_ask?: number;
  per_team_per_hour?: number;
}

export interface Dealer {
  id: string;
  name: string;
  status: string;
  level: number | null;
  title: string;
  kind: string;
  enabled: boolean;
  traits: Record<string, number>;
  menu: { sells: DealerMenuItem[]; buys: DealerMenuItem[]; deals_per_team_per_hour: number };
}

export interface CatalogCard {
  id: string;
  name: string;
  rarity: Rarity;
  book: number;
  print_run: number;
  minted: number;
  hidden: boolean;
  page: boolean;
}

export interface Catalog {
  rarities: Record<Rarity, { label: string; book: number; print_run: number; color: string }>;
  sets: { id: string; name: string; theme: string; color: string; released: boolean; release: string; cards: CatalogCard[] }[];
  packs: { id: string; name: string; slots: Record<string, number>[]; expected_book: number }[];
  values: { copy_marginals: number[]; page_bonus: number; master_bonus: number };
}

export interface FeedEvent {
  id: number;
  tick: number;
  t: number;
  type: string;
  actor: string;
  payload: Record<string, any>;
}

export interface BenchOffer {
  id: string;
  give: { cash?: number };
  want: { cash?: number };
  expires_tick?: number;
}

export interface BrokerBook {
  offers?: Offer[];
  bench_offers?: BenchOffer[];
  fee_bps?: number;
  fee_per_card?: number;
}
