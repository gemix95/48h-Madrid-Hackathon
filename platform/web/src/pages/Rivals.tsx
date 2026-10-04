import { Card, Spark, f1, f2, sign, type Snap } from "../ui";

export function RivalsPage({ s }: { s: Snap }) {
  const rows = s.rivals as Snap[];
  const us = rows.find((r) => r.us);
  const lead = rows[0];
  const gap = us && lead ? lead.score - us.score : null;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="kicker">Rivals · leaderboard</div>
          <h1>{us ? `We are #${us.rank} with ${f2(us.score)}` : "Leaderboard"}{gap ? `, ${f2(gap)} behind ${lead.name}` : ""}</h1>
          <p>
            Score = negotiating (out of 30) + market (out of 30) + judges (out of 40, not shown live). "Today" counts settlements since the round started:
            deals with dealers, trades with other teams, and the sets each team collects or dumps.
            Teams that collect our high-multiplier sets are our buyers; teams dumping them are our sellers.
          </p>
        </div>
      </div>

      <Card>
        <div className="table-wrap"><table>
          <thead><tr>
            <th>#</th><th>Team</th><th className="r">Score</th><th>Trend</th><th className="r">Negotiating</th><th className="r">Market</th><th className="r">Level</th>
            <th className="r">Pages</th><th>Venue</th><th className="r">Dealer / team deals today</th><th>Collects</th><th>Dumps</th>
          </tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.team} style={r.us ? { background: "rgba(76,141,255,0.12)" } : undefined}>
              <td className="mono">{r.rank}</td>
              <td><b>{r.name}</b>{r.us && <span className="chip good" style={{ marginLeft: 6 }}>us</span>}<div className="faint" style={{ fontSize: 12 }}>{(r.badges ?? []).join(" · ")}</div></td>
              <td className="r num"><b>{f2(r.score)}</b></td>
              <td><Spark data={r.series} color={r.trend >= 0 ? "var(--good)" : "var(--bad)"} /><span className={`faint ${r.trend >= 0 ? "good" : "bad"}`} style={{ fontSize: 12 }}>{sign(r.trend, 2)}</span></td>
              <td className="r num">{f1(r.negotiating)}</td>
              <td className="r num">{f1(r.market)}</td>
              <td className="r num">{r.level}</td>
              <td className="r num">{r.pages} <span className="faint">({r.album})</span></td>
              <td>{r.venue ? <><span>{r.venue.name}</span><div className="faint" style={{ fontSize: 12 }}>{r.venue.mechanism} · {r.venue.fee_bps ? `${r.venue.fee_bps / 100}%` : "0%"} · {r.venue.trades} trades</div></> : <span className="faint">none</span>}</td>
              <td className="r num">{r.today ? `${r.today.dealer} / ${r.today.team}` : "–"}</td>
              <td className="mono">{r.today?.collects?.join(", ") || "–"}</td>
              <td className="mono">{r.today?.dumps?.join(", ") || "–"}</td>
            </tr>
          ))}</tbody>
        </table></div>
      </Card>

      <div className="grid g2">
        <Card title="What the leaders do differently" sub="Read from the leaderboard split, not guessed.">
          <div className="prose"><ul>
            {rows.slice(0, 3).map((r) => (
              <li key={r.team}><b>{r.name}</b>: negotiating {f1(r.negotiating)}, market {f1(r.market)}, {r.deals} deals, level {r.level}{r.venue ? `, runs ${r.venue.name}` : ", no venue of its own"}.</li>
            ))}
            {us && <li><b>Us</b>: negotiating {f1(us.negotiating)}, market {f1(us.market)}, {us.deals} deals, level {us.level}.</li>}
          </ul></div>
        </Card>
        <Card title="Where the gap is" sub="Points we need to catch the leader, by part of the score.">
          {us && lead && us !== lead ? (
            <div className="prose"><ul>
              <li>Negotiating: {sign(lead.negotiating - us.negotiating)} for {lead.name}.</li>
              <li>Market: {sign(lead.market - us.market)} for {lead.name}.</li>
              <li>The judges (40 points) are not on the live board: the demo and the write-up decide them.</li>
            </ul></div>
          ) : <div className="empty">{us && us === lead ? "We lead. Protect it: avoid losing trades." : "No leaderboard yet."}</div>}
        </Card>
      </div>
    </div>
  );
}
