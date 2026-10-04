import { Card, f1, type Snap } from "../ui";

export function GuidePage({ s }: { s: Snap }) {
  const aff: Record<string, number> = s.me?.affinity ?? {};
  const pages = s.album?.pages ?? [];
  const st = s.story;
  const raw = st.raw ?? {};
  const duel = s.duels?.stats?.sessions ?? [];
  const two = duel.find((x: Snap) => x.issues?.includes("days"));
  const sorted = Object.entries(aff).sort((a, b) => b[1] - a[1]);
  const setName = (id: string) => pages.find((p: Snap) => p.set === id)?.name ?? id;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="kicker">How to win</div>
          <h1>The Bazaar, explained in ten minutes</h1>
          <p>What the game is, where every point comes from, what we did right and wrong on Saturday, and the plan for Sunday. Every number here comes from the live game or from our own history.</p>
        </div>
      </div>

      <Card title="1 · The game in 30 seconds">
        <div className="prose">
          <p>18 teams. Each team's agent collects <b>Madrid trading cards</b> (6 neighbourhoods × 12 cards), <b>haggles with 5 card dealers</b>, <b>trades with other teams</b>, <b>negotiates 1-vs-1 duels</b> and <b>runs its own market</b>. Everything happens through an HTTP API, one <b>tick</b> at a time (every 15 s on Sunday). Per tick a team may accept <b>one</b> offer, send one message per conversation, post 12 listings.</p>
          <div className="rule"><b>The one rule:</b> words persuade, structure binds. Anyone can say anything; only a structured offer that the other side accepts moves cards or cash. Read the offer, never the words (Los Pícaros' offers sometimes give a different card than the one they talk about).</div>
          <div className="rule good"><b>Only value created scores.</b> Never the number of trades, fees earned, pack luck or gifts. We have the most deals of any team (88) and sit at rank {st.rank}: volume is not the game.</div>
        </div>
      </Card>

      <Card title="2 · Where the 100 points come from">
        <div className="stack">
          <div style={{ flex: 30, background: "#3554b8" }}><b>30 Negotiating</b>duels · dealer ladder · team trades</div>
          <div style={{ flex: 30, background: "#1f8a62" }}><b>30 Market-making</b>Market Test · our market</div>
          <div style={{ flex: 40, background: "#6b4bb8" }}><b>40 Judges</b>ideas and craft</div>
        </div>
        <div className="prose" style={{ marginTop: 16 }}>
          <p>Each day is a <b>round</b>; rounds are averaged (Friday ×0.5, Saturday ×1, <b>Sunday ×1</b>). Sunday therefore weighs as much as Saturday, and it starts from zero: every leak below is fixable tomorrow.</p>
        </div>
        <div className="table-wrap" style={{ marginTop: 12 }}>
          <table>
            <thead><tr><th>Lever</th><th>How it scores</th><th className="r">Ours now</th><th>The one rule</th></tr></thead>
            <tbody>
              <tr><td><b>Duels</b></td><td>Our surplus inside our private limit × (1 − decay) for every round of talk. Outside the limit is negative, no deal is 0.</td><td className="r num">{f1(raw.duel_points)}</td><td>Close in 2–4 rounds. As a buyer, delivery days cost us: day 0.</td></tr>
              <tr><td><b>Dealer ladder</b></td><td>Best 3 deals per dealer (5 dealers), each by the share of the dealer's price range we captured. An empty slot counts 0, higher levels weigh more.</td><td className="r num">{raw.ladder_points != null ? raw.ladder_points.toFixed(3) : "–"}</td><td>3 cheap, well-haggled deals at every dealer, starting with the highest level.</td></tr>
              <tr><td><b>Team trades</b></td><td>Value in − value out at <b>our</b> private values, cash included. A gain counts up to +50 per trade; a loss counts in full.</td><td className={`r num ${raw.neg_points < 0 ? "bad" : ""}`}>{f1(raw.neg_points)}</td><td>Never sell below our value, never buy above it. One +40 trade beats twenty +2 trades.</td></tr>
              <tr><td><b>Market Test</b></td><td>Every 2 h every market gets the same fake book; our broker scores realised gains / possible gains. The free stall earns 0.5, the top three brokers 1.</td><td className="r num">{raw.bench_points != null ? raw.bench_points.toFixed(3) : "–"}</td><td>Market open, 0% fee, board, broker matching every tick of every session.</td></tr>
              <tr><td><b>Our market</b></td><td>Value other teams create trading with each other on our venue.</td><td className="r num">{f1(raw.mm_points)}</td><td>Teams must want to trade there: 0% fee and liquidity.</td></tr>
              <tr><td><b>Judges</b></td><td>Ideas and craft, judged at the end.</td><td className="r faint">later</td><td>Show clear thinking: this war room, the data, the lessons.</td></tr>
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="3 · Our private values: why the same card is worth different amounts to each team" sub="Every team gets the same six multipliers, shuffled. Nobody sees anyone else's.">
        <div className="sets">
          {sorted.map(([set, m]) => {
            const p = pages.find((x: Snap) => x.set === set);
            return (
              <div className="set" key={set} style={{ borderTop: `3px solid ${p?.color ?? "var(--line)"}` }}>
                <div className="n">{setName(set)}</div>
                <div className="m" style={{ color: m >= 1.2 ? "var(--good)" : m < 0.8 ? "var(--bad)" : "var(--text)" }}>×{m}</div>
                <div className="n">{m >= 1.2 ? "collect" : m < 0.8 ? "sell to collectors" : "neutral"}</div>
                {p && <div className="page-dots">{Array.from({ length: p.of }, (_, i) => <i key={i} className={i < p.have ? "have" : ""} />)}</div>}
              </div>
            );
          })}
        </div>
        <div className="prose" style={{ marginTop: 16 }}>
          <p><b>value of a card = book × our multiplier × copy factor</b> (1st copy ×1, 2nd ×0.25, 3rd ×0.1). Book: common 10, uncommon 25, rare 70, epic 180, legendary 450.</p>
          <p><b>Page bonus:</b> holding a set's 5 commons + 3 uncommons + 2 rares adds 25% of the page's value (epic + legendary on top: +10% more). That is why one missing card can be worth 3× its book to us, and why selling one card off a complete page costs far more than the card.</p>
          <div className="rule">Example from our album: <b>SAL-09 El Marqués</b> is a rare (book 70). Salamanca is ×1.6 for us → 112, plus losing it would break the Salamanca page bonus (25% of 424 = 106). So it is worth <b>218 P</b> to us. Doña Pilar paid another team 75 P for one. Selling ours at 75 would score −143.</div>
        </div>
      </Card>

      <Card title="4 · What happened to us on Saturday" sub="Read this before changing anything.">
        <div className="list">
          <div className="item"><span className="chip critical">−{Math.abs(raw.neg_points ?? 0).toFixed(0)} P</span><div><div className="t">Team trades lost value</div><div className="w">Many modules (trader, swaps, auctions, arbitrage, loans, cashback) traded 88 times. Resting asks filled below what cards were worth to us; cards sold to dealers were bought back from teams for more. A gain counts at most +50, a loss in full: churn can only hurt.</div></div><span /></div>
          {two && <div className="item"><span className="chip critical">{two.buyer.mean.toFixed(1)} / duel</span><div><div className="t">Duels II as a buyer</div><div className="w">In two-issue duels each delivery day COSTS the buyer its weight. Our bot proposed and accepted day 10 (e.g. limit 148, 7.99 P/day: paid 140 at day 10 → −60.8). As a seller we averaged +{two.seller.mean.toFixed(1)}. The Python bot is patched; the duel agent here picks the day that grows the pie.</div></div><span /></div>}
          <div className="item"><span className="chip critical">{raw.bench_points != null ? raw.bench_points.toFixed(3) : "–"}</span><div><div className="t">Market Test below the free stall (0.5)</div><div className="w">Our market was closed and reopened three times (v03 → v22 → v24) and the broker restarted with the agent: those sessions counted 0. Our simulator shows the matching rule changes efficiency by ±1%; being open every session is what matters.</div></div><span /></div>
          <div className="item"><span className="chip good">+{f1(raw.duel_points)}</span><div><div className="t">Duels I went well</div><div className="w">Price-only duels averaged +16 (session 1). Rivals settle around 0.88 × our limit when we buy and 1.21 × our cost when we sell, in 2–3 rounds.</div></div><span /></div>
          <div className="item"><span className="chip good">3 pages</span><div><div className="t">Album</div><div className="w">Lavapiés, Malasaña and Salamanca are complete: the page bonuses are banked. Protect them (they are in the reserved list).</div></div><span /></div>
        </div>
      </Card>

      <Card title="5 · Sunday's plan, in order" sub="Madrid time, from the organisers' schedule (see Now → The day ahead for live countdowns).">
        <div className="prose">
          <ul>
            <li><b>Before 09:00</b>: one process per job. Exactly one duel player, one broker, one trader. Our market open (0%, board). Decide which agent here goes live (Agents page).</li>
            <li><b>09:00 open</b>: +150 P, Chamberí released (×{aff.CHA ?? 0.9} for us). Ticks every 15 s: 240 ticks per hour.</li>
            <li><b>Ladder, all morning</b>: 15 slots (5 dealers × 3). Fill the highest levels first (Don Ernesto, Los Pícaros, Doña Pilar) with no-loss buys, haggled from ~40% of the opening ask. See Dealers.</li>
            <li><b>Market Tests at ~09:21, 11:21, 13:21</b>: market open, broker matching every tick.</li>
            <li><b>Duels III ~11:00</b>: price + delivery days, 10% decay per round, 12-tick clock. Close fast, pick the right day.</li>
            <li><b>Team trades</b>: only sure gains at our values (min +3 after fees). Sell what is worth little to us (La Latina ×{aff.LAT}, El Retiro ×{aff.RET}, duplicates) to teams that collect it.</li>
            <li><b>~14:00 finale</b>: all dealers close (ladder done by then) and the Grand Final duels run. <b>15:00</b> scores freeze.</li>
          </ul>
        </div>
      </Card>

      <Card title="6 · Words you will see">
        <div className="table-wrap">
          <table>
            <tbody>
              <tr><td><b>Tick</b></td><td>One heartbeat of the game. Everything accepted settles at the next tick.</td></tr>
              <tr><td><b>Limit</b> (duels)</td><td>Our private walk-away price: a seller's cost, a buyer's value. Never cross it.</td></tr>
              <tr><td><b>Decay</b></td><td>Share of a duel's pie lost with every round of talk (10% on Sunday).</td></tr>
              <tr><td><b>Days weight</b></td><td>In two-issue duels: P per delivery day. A seller gains it, a buyer pays it.</td></tr>
              <tr><td><b>Ladder slot</b></td><td>One of our best 3 deals with a dealer. 5 dealers × 3 slots.</td></tr>
              <tr><td><b>Opening ask</b></td><td>The dealer's first price. Every team's lowest prices landed near 77% of it.</td></tr>
              <tr><td><b>Final offer</b></td><td>A dealer's last price (<span className="mono">"final": true</span>): take it or it walks.</td></tr>
              <tr><td><b>Venue / market</b></td><td>A place where teams trade. El Rastro (house) charges 5% + 1 P/card; ours (v24) charges 0%.</td></tr>
              <tr><td><b>Broker</b></td><td>Our market's matching engine. It pairs crossing bids and asks, including the Market Test's fake traders.</td></tr>
              <tr><td><b>Stall</b></td><td>The free auto market every team gets. Matching like it earns half the Market Test points.</td></tr>
              <tr><td><b>Shadow mode</b></td><td>An agent here decides and explains but does not act: a safe way to compare it with the Python agents.</td></tr>
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
