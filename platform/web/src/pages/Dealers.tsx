import { Bar, Card, Dots, type Snap } from "../ui";

const HOW: Record<string, string> = {
  abuela: "Honest, kind, patient. Likes kindness. Every team's first deal is a fixed welcome price that is not negotiable. Haggled: pack 19–24 P (opens 30), common 9–12 P.",
  chato: "Shrewd, long memory, strict. Mirrors your step size and lands near the midpoint of the two openings. Do not try tricks.",
  pilar: "A collector: pays over book for the sets she loves (Salamanca, El Retiro) and sells gold packs.",
  picaros: "Bargains and bad faith. Their structured offer sometimes gives a different card than the one they name: read it before accepting, flag proven tricks.",
  banco: "The vault: epics and legendaries, very patient, very strict, never in a hurry. Formal Spanish.",
};

export function DealersPage({ s }: { s: Snap }) {
  const ladder = s.deal.ladder as Snap[];
  const filled = ladder.reduce((a, l) => a + Math.min(3, l.dealsToday), 0);
  const close = s.timeline.find((t: Snap) => t.action === "persona");
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="kicker">Dealers · the ladder</div>
          <h1>{filled} of {ladder.length * 3} ladder slots filled today</h1>
          <p>Our best 3 deals with each dealer count, scored by how much of the dealer's price range we captured. An empty slot counts 0, and higher levels weigh more. A buy above what the card is worth to us counts as a full loss, so the plan only lists buys worth more to us than their likely price.{close?.madrid ? ` All dealers close at ${close.madrid} Madrid.` : ""}</p>
        </div>
      </div>

      <Card title="Ladder plan" sub="Highest level first. “Likely price” = 77% of the opening ask, where every team's best haggles landed.">
        <div className="table-wrap"><table>
          <thead><tr><th>Dealer</th><th>Today</th><th>Our prices</th><th>Best no-loss buy</th><th className="r">Worth to us</th><th className="r">Likely price</th><th className="r">Our cap</th><th>Note</th></tr></thead>
          <tbody>{ladder.map((l) => {
            const t = l.target ?? l.stretch;
            return (
              <tr key={l.dealer}>
                <td><b>{l.dealerName}</b><div className="faint">level {l.level}</div></td>
                <td><Dots n={Math.min(3, l.dealsToday)} of={3} /></td>
                <td className="num">{l.bestPrices.length ? l.bestPrices.join(", ") : "–"}</td>
                <td>{t ? <>{t.label}{!l.target && <span className="chip high" style={{ marginLeft: 6 }}>needs cash</span>}</> : <span className="faint">none</span>}</td>
                <td className="r num">{t ? t.value.toFixed(0) : "–"}</td>
                <td className="r num">{t ? t.expected : "–"}</td>
                <td className="r num">{t ? t.cap : "–"}</td>
                <td className="muted" style={{ fontSize: 12.5, maxWidth: 340 }}>{l.note}</td>
              </tr>
            );
          })}</tbody>
        </table></div>
      </Card>

      <div className="grid g3">
        <Card title="How to haggle (every dealer)">
          <div className="prose">
            <ul>
              <li><b>Open low</b>: 30–45% of the opening ask got the lowest prices across every team's conversations.</li>
              <li><b>A new price every message.</b> A dealer only moves when we move; the same price twice earns nothing (and some call it spam).</li>
              <li><b>Small steps earn small steps.</b> Concede slowly at first, faster near the end.</li>
              <li><b>Final offer</b> (<span className="mono">final: true</span>): take it only inside our cap, or it walks.</li>
              <li>One open conversation per dealer per team: the Python agents and this one must not talk to the same dealer.</li>
            </ul>
          </div>
        </Card>
        {s.dealers.filter((d: Snap) => d.status === "active").slice(0, 5).map((d: Snap) => (
          <Card key={d.id} title={d.name} sub={`level ${d.level} · ${d.title}`}>
            <p className="muted" style={{ fontSize: 13, marginBottom: 12 }}>{HOW[d.id] ?? ""}</p>
            {Object.entries(d.traits ?? {}).map(([k, v]) => (
              <div className="trait" key={k}><span>{k}</span><Bar value={Number(v)} max={1} color="var(--violet)" /></div>
            ))}
            <div className="faint" style={{ fontSize: 12, marginTop: 10 }}>
              Sells: {(d.menu?.sells ?? []).map((m: Snap) => m.name ?? `${m.rarity} ~${m.list_price}`).join(", ")}<br />
              Buys: {[...new Set((d.menu?.buys ?? []).map((m: Snap) => m.rarity))].join(", ")} · {d.menu?.deals_per_team_per_hour} deals/hour
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
