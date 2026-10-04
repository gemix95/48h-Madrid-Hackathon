import { Bar, Card, f1, f2, type Snap } from "../ui";

const STRATS: { id: string; label: string; how: string }[] = [
  { id: "stall", label: "Stall rule", how: "Pair the highest buyer with the lowest seller, in order, while they cross." },
  { id: "maxcross", label: "Max crossings", how: "Find the most pairs that can cross, then pair them in reverse." },
  { id: "hybrid", label: "Hybrid", how: "Stall rule early, max crossings near the end of the session." },
  { id: "oracle", label: "Oracle", how: "Knows every future arrival. An upper bound that no real broker can reach." },
];

export function MarketPage({ s }: { s: Snap }) {
  const m = s.market;
  const ours = m.venues.find((v: Snap) => v.id === s.me?.venue) ?? null;
  const tests = s.timeline.filter((t: Snap) => t.action === "bench" && !t.leftover);
  const checks = [
    { ok: !!ours && ours.status === "open", label: "Our venue is open", why: ours ? `${ours.name} (${ours.id}) is ${ours.status}.` : "We have no venue. The Market Test scores 0 without one." },
    { ok: !!ours && !ours.fee_bps && !ours.fee_per_card, label: "Fee is 0%", why: "The Market Test measures surplus left to traders. Every fee point is surplus we take away from them." },
    { ok: !!ours && ours.mechanism === "board", label: "Board mechanism", why: "With a board, our broker picks the matches. With an auto venue, the server's stall rule matches for us." },
    { ok: !!m.broker, label: "Broker key loaded", why: m.broker ? "The market agent can match bench offers itself." : "Without BROKER_KEY the market agent only watches. The Python bazaar-broker service matches instead." },
    { ok: !!ours && ours.status === "open", label: "Never close it during a test", why: "Saturday's bench score fell to 0.451 (stall gives 0.5) because the venue was closed and reopened mid-session (v03 → v22 → v24)." },
  ];
  const sim = m.sim;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="kicker">Market · 30 points</div>
          <h1>Stay open, charge 0%, match every session</h1>
          <p>
            Market points come from the Market Test: the organisers put bench traders on our venue and measure how much of their possible surplus we let them realise.
            The simulation shows that the matching rule barely matters (about 1%). Being open with no fee for the whole session matters far more.
          </p>
        </div>
      </div>

      <div className="grid g2">
        <Card title="Checklist" sub="Each line costs points if it is red during a Market Test.">
          <div className="list">
            {checks.map((c) => (
              <div className="item" key={c.label}>
                <span className={`chip ${c.ok ? "good" : "critical"}`}>{c.ok ? "ok" : "fix"}</span>
                <div>
                  <b>{c.label}</b>
                  <div className="why">{c.why}</div>
                </div>
              </div>
            ))}
          </div>
        </Card>

        <Card title="Upcoming Market Tests" sub="Madrid time. Each one is a separate session on our venue.">
          {tests.length ? (
            <div className="list">
              {tests.map((t: Snap, i: number) => (
                <div className="item" key={i}>
                  <span className="mono">{t.madrid ?? "–"}</span>
                  <div><b>{t.title}</b><div className="why">{t.meaning ?? ""}</div></div>
                </div>
              ))}
            </div>
          ) : <div className="empty">No Market Test left in the schedule.</div>}
          <div className="faint" style={{ marginTop: 12, fontSize: 12.5 }}>
            Bench offers in the book right now: <b>{m.bench}</b>. Last plan: {m.lastPlan?.length ? `${m.lastPlan.length} matches` : "none"}.
          </div>
        </Card>
      </div>

      <Card title="Which matching rule? (simulated)" sub="Share of the best possible trader surplus, averaged over 1,500 random sessions with arrivals. Higher is better.">
        {!sim ? <div className="empty">The simulation is still running (a few seconds after start).</div> : (
          <div className="grid g2">
            {[{ k: "normal", r: sim }, { k: "hard (few crossing pairs)", r: sim.hard }].map(({ k, r }) => r && (
              <div key={k}>
                <div className="kicker" style={{ marginBottom: 8 }}>{k}</div>
                {STRATS.map((st) => (
                  <div className="bar-row" key={st.id} title={st.how}>
                    <span>{st.label}</span>
                    <Bar value={r[st.id]} max={1} color={st.id === "oracle" ? "var(--line-2)" : "var(--accent)"} />
                    <span className="num">{f2(r[st.id])}</span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
        <div className="prose" style={{ marginTop: 12 }}>
          <ul>
            {STRATS.map((st) => <li key={st.id}><b>{st.label}</b>: {st.how}</li>)}
            <li>All the real rules sit within about 1% of each other and close to the oracle. A closed venue scores 0 for the rest of the session.</li>
          </ul>
        </div>
      </Card>

      <Card title="All venues" sub="Who runs a market and how busy it is. Teams trade where the fee is 0 and the board is full.">
        <div className="table-wrap"><table>
          <thead><tr><th>Venue</th><th>Owner</th><th>Status</th><th>Mechanism</th><th className="r">Fee</th><th className="r">Trades</th><th className="r">Volume</th><th className="r">Value created</th></tr></thead>
          <tbody>
            {[...m.venues].sort((a: Snap, b: Snap) => b.trades - a.trades).map((v: Snap) => (
              <tr key={v.id} style={v.id === s.me?.venue ? { background: "rgba(76,141,255,0.10)" } : undefined}>
                <td><b>{v.name}</b> <span className="faint mono">{v.id}</span>{v.starter && <span className="faint"> · starter</span>}{v.house && <span className="faint"> · house</span>}</td>
                <td className="mono">{v.owner ?? "–"}</td>
                <td><span className={`chip ${v.status === "open" ? "good" : "normal"}`}>{v.status}</span></td>
                <td>{v.mechanism ?? "–"}</td>
                <td className="r num">{v.fee_bps ? `${(v.fee_bps / 100).toFixed(1)}%` : "0%"}{v.fee_per_card ? ` + ${v.fee_per_card}/card` : ""}</td>
                <td className="r num">{v.trades}</td>
                <td className="r num">{v.volume}</td>
                <td className="r num">{f1(v.value_created)}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </Card>
    </div>
  );
}
