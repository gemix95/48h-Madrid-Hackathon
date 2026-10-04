import { Card, f1, sign, type Snap } from "../ui";

export function DuelsPage({ s }: { s: Snap }) {
  const st = s.duels.stats;
  const live = s.duels.live;
  const next = s.timeline.filter((t: Snap) => t.action === "duels");
  const maxAbs = Math.max(1, ...st.sessions.flatMap((x: Snap) => [Math.abs(x.buyer.mean), Math.abs(x.seller.mean)]));
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="kicker">Duels</div>
          <h1>1-vs-1 against every other team</h1>
          <p>Each duel: one item, one rival (shown under an alias), our private limit. Points = our surplus inside the limit, shrunk by every round of talk. Every team meets every other twice, once as buyer and once as seller.</p>
        </div>
      </div>

      <div className="grid g3">
        <Card title="How to win a duel">
          <div className="prose">
            <ul>
              <li><b>Never cross the limit.</b> A deal outside it is negative.</li>
              <li><b>Close fast.</b> Each round of talk loses 10% of the pie on Sunday; 2–4 rounds is the sweet spot.</li>
              <li><b>Delivery days:</b> a seller gains its weight per day, a buyer pays it. Buyers' weights are usually bigger (median 3.6 vs 2.1), so day 0 usually grows the pie.</li>
              <li><b>Silent rivals</b> (18 of our 24 no-deals): one opening offer, then wait.</li>
            </ul>
          </div>
        </Card>
        <Card title="Coming up">
          {next.length ? next.map((t: Snap, i: number) => (
            <div key={i} style={{ marginBottom: 12 }}>
              <div><b>{t.title}</b> <span className="muted">{t.madrid ? `· ${t.madrid} Madrid` : ""}</span></div>
              <div className="muted" style={{ fontSize: 13 }}>{t.meaning}</div>
            </div>
          )) : <div className="empty">No more duel sessions scheduled.</div>}
        </Card>
        <Card title="Average points per duel" sub="by session and role">
          {st.sessions.map((x: Snap) => (
            <div key={x.session} style={{ marginBottom: 12 }}>
              <div className="muted" style={{ fontSize: 12.5 }}>Session {x.session} · {x.issues.join(" + ")} · decay {Math.round((x.decay ?? 0) * 100)}%</div>
              {(["buyer", "seller"] as const).map((r) => (
                <div key={r} style={{ display: "grid", gridTemplateColumns: "56px 1fr 52px", gap: 8, alignItems: "center", marginTop: 4 }}>
                  <span className="faint" style={{ fontSize: 12 }}>{r}</span>
                  <div style={{ position: "relative", height: 10, background: "#1d2433", borderRadius: 99 }}>
                    <div style={{ position: "absolute", left: "50%", top: -2, bottom: -2, width: 1, background: "var(--line-2)" }} />
                    <div style={{ position: "absolute", top: 0, bottom: 0, borderRadius: 99, background: x[r].mean >= 0 ? "var(--good)" : "var(--bad)", left: x[r].mean >= 0 ? "50%" : `${50 - (Math.abs(x[r].mean) / maxAbs) * 50}%`, width: `${(Math.abs(x[r].mean) / maxAbs) * 50}%` }} />
                  </div>
                  <span className={`r num ${x[r].mean < 0 ? "bad" : ""}`}>{sign(x[r].mean)}</span>
                </div>
              ))}
            </div>
          ))}
        </Card>
      </div>

      <Card title={`Live duels (${live.length})`} sub="What the duel agent would do this tick, and why.">
        {live.length ? (
          <div className="table-wrap"><table>
            <thead><tr><th>Item</th><th>Role</th><th className="r">Limit</th><th className="r">Days weight</th><th className="r">Rival offer</th><th className="r">Ticks left</th><th>Agent's move</th></tr></thead>
            <tbody>{live.map((d: Snap) => (
              <tr key={d.duel}>
                <td><b>{d.item}</b><div className="faint">vs {d.rival}</div></td>
                <td>{d.role}</td>
                <td className="r num">{d.your_limit}</td>
                <td className="r num">{d.your_days_weight ?? "–"}</td>
                <td className="r num">{d.rival_offer ? `${d.rival_offer.price}${d.issues.includes("days") ? ` · d${d.rival_offer.days}` : ""}` : "–"}</td>
                <td className="r num">{d.deadline_tick - (s.clock?.tick ?? 0)}</td>
                <td><span className={`chip ${d.suggestion.kind === "accept" ? "good" : d.suggestion.kind === "offer" ? "normal" : "info"}`}>{d.suggestion.kind}{d.suggestion.price != null ? ` ${d.suggestion.price}` : ""}{d.suggestion.days != null ? ` · d${d.suggestion.days}` : ""}</span><div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>{d.suggestion.why}</div></td>
              </tr>
            ))}</tbody>
          </table></div>
        ) : <div className="empty">No duel is running. Duels appear here as soon as a session starts.</div>}
      </Card>

      <div className="grid g2">
        <DuelList title="Our worst duels" sub="Learn from these first." rows={st.worst} />
        <DuelList title="Our best duels" sub="What good looks like." rows={st.best} />
      </div>

      <Card title="Sessions so far">
        <div className="table-wrap"><table>
          <thead><tr><th>Session</th><th>Issues</th><th className="r">Duels</th><th className="r">Deals</th><th className="r">Total points</th><th className="r">Buyer avg</th><th className="r">Seller avg</th><th className="r">Negative</th><th className="r">Silent rivals</th></tr></thead>
          <tbody>{st.sessions.map((x: Snap) => (
            <tr key={x.session}>
              <td>{x.session}</td><td>{x.issues.join(" + ")}</td><td className="r num">{x.n}</td><td className="r num">{x.deals}</td>
              <td className="r num"><b>{f1(x.total)}</b></td>
              <td className={`r num ${x.buyer.mean < 0 ? "bad" : ""}`}>{sign(x.buyer.mean)}</td>
              <td className={`r num ${x.seller.mean < 0 ? "bad" : ""}`}>{sign(x.seller.mean)}</td>
              <td className="r num">{x.buyer.negative + x.seller.negative}</td>
              <td className="r num">{x.buyer.silent + x.seller.silent}</td>
            </tr>
          ))}</tbody>
        </table></div>
      </Card>
    </div>
  );
}

function DuelList({ title, sub, rows }: { title: string; sub: string; rows: Snap[] }) {
  return (
    <Card title={title} sub={sub}>
      <div className="table-wrap"><table>
        <thead><tr><th>Duel</th><th>Role</th><th className="r">Limit</th><th className="r">Deal</th><th className="r">Days × weight</th><th className="r">Points</th></tr></thead>
        <tbody>{rows.map((d: Snap) => (
          <tr key={d.duel}>
            <td><b>{d.item}</b><div className="faint">#{d.duel} · session {d.session}</div></td>
            <td>{d.role}</td>
            <td className="r num">{d.limit}</td>
            <td className="r num">{d.price ?? "no deal"}</td>
            <td className="r num">{d.w != null ? `${d.days} × ${d.w}` : "–"}</td>
            <td className={`r num ${d.result < 0 ? "bad" : "good"}`}><b>{sign(d.result)}</b></td>
          </tr>
        ))}</tbody>
      </table></div>
    </Card>
  );
}
