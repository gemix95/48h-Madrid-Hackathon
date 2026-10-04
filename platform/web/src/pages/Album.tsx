import { useState } from "react";
import { Card, f1, sign, type Snap } from "../ui";

export function AlbumPage({ s }: { s: Snap }) {
  const a = s.album;
  const [set, setSet] = useState<string>("all");
  if (!a) return <div className="page"><div className="empty">No album yet: the team key is needed to read our cards.</div></div>;
  const cards = (a.cards as Snap[]).filter((c) => set === "all" || c.set === set).sort((x, y) => (y.value ?? 0) - (x.value ?? 0));
  const complete = (a.pages as Snap[]).filter((p) => p.have === p.of).length;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="kicker">Album · our cards</div>
          <h1>{a.cards.length} cards, {complete} complete pages, {a.cash} P cash</h1>
          <p>
            A card is worth book × our multiplier for its set (shown under each set). A complete page adds a 25% bonus, so breaking one costs far more than the card.
            Selling to a team below that value counts as a full loss. Reserved cards are traded only by hand.
          </p>
        </div>
      </div>

      <Card title="Pages" sub="Green squares: cards we hold. Bonus = 25% of the page value, earned only when the page is complete.">
        <div className="sets">
          {(a.pages as Snap[]).map((p) => (
            <div className="set" key={p.set} style={{ borderTop: `3px solid ${p.color}` }}>
              <div className="m">{p.multiplier}×</div>
              <div><b>{p.name}</b> <span className="faint mono">{p.set}</span></div>
              <div className="n">{p.have}/{p.of} · page {f1(p.pageValue)} · bonus {f1(p.bonus)}{!p.released && " · not released"}</div>
              <div className="page-dots">{Array.from({ length: p.of }, (_, i) => <i key={i} className={i < p.have ? "have" : ""} />)}</div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid g2">
        <Card title="Wishlist" sub="Cards that add the most value to us, page bonus included. Buy below this.">
          {a.wishlist.length ? (
            <div className="table-wrap"><table>
              <thead><tr><th>Card</th><th className="r">Worth to us</th></tr></thead>
              <tbody>{a.wishlist.map((w: Snap) => <tr key={w.ref}><td className="mono">{w.ref}</td><td className="r num good">{sign(w.gain)}</td></tr>)}</tbody>
            </table></div>
          ) : <div className="empty">Nothing worth buying.</div>}
        </Card>
        <Card title="Spares" sub="Cards we can give away cheaply: duplicates or low-multiplier sets. Never sell below the loss shown.">
          {a.spares.length ? (
            <div className="table-wrap"><table>
              <thead><tr><th>Card</th><th className="r">Loss if we give it</th></tr></thead>
              <tbody>{a.spares.map((x: Snap) => <tr key={x.id}><td className="mono">{x.ref} <span className="faint">#{x.id}</span></td><td className="r num">{f1(x.loss)}</td></tr>)}</tbody>
            </table></div>
          ) : <div className="empty">No spares: every card we hold is on a page we value.</div>}
        </Card>
      </div>

      <Card title="Best offers on the boards right now" sub={`Scored at our private values, fees included. The deal agent only takes offers worth at least +${s.deal.minGain}.`}>
        {s.deal.opportunities.length ? (
          <div className="table-wrap"><table>
            <thead><tr><th>Offer</th><th>From</th><th>Venue</th><th className="r">Gain to us</th><th>Why</th></tr></thead>
            <tbody>{s.deal.opportunities.map((o: Snap) => (
              <tr key={o.id}>
                <td className="mono">#{o.id}</td><td className="mono">{o.maker}</td><td className="mono">{o.venue}</td>
                <td className={`r num ${o.score >= 0 ? "good" : "bad"}`}>{sign(o.score)}</td>
                <td className="muted" style={{ fontSize: 12.5 }}>{o.why}</td>
              </tr>
            ))}</tbody>
          </table></div>
        ) : <div className="empty">No open offer is worth anything to us.</div>}
      </Card>

      <Card title="Today's trades" sub="Value in minus value out plus cash, at our private values. Negative rows are leaks.">
        {s.deal.trades.length ? (
          <div className="table-wrap"><table>
            <thead><tr><th>Tick</th><th>With</th><th>Gave</th><th>Got</th><th className="r">Cash</th><th className="r">Value to us</th></tr></thead>
            <tbody>{s.deal.trades.map((t: Snap, i: number) => (
              <tr key={i}>
                <td className="mono">{t.tick}</td><td className="mono">{t.with}</td>
                <td className="mono">{(t.gave ?? []).join(", ") || "–"}</td><td className="mono">{(t.got ?? []).join(", ") || "–"}</td>
                <td className="r num">{sign(t.cash, 0)}</td>
                <td className={`r num ${t.estValue >= 0 ? "good" : "bad"}`}>{sign(t.estValue)}</td>
              </tr>
            ))}</tbody>
          </table></div>
        ) : <div className="empty">No trades yet this round.</div>}
      </Card>

      <Card title="Our cards" right={
        <div className="filters">
          {["all", ...(a.pages as Snap[]).map((p) => p.set)].map((k) => <button key={k} className={set === k ? "on" : ""} onClick={() => setSet(k)}>{k}</button>)}
        </div>
      }>
        <div className="cards">
          {cards.map((c) => (
            <div className={`cardlet rar-${c.rarity}`} key={c.id} title={`${c.rarity} · asset #${c.id}`}>
              <div className="ref">{c.ref}{c.reserved && " · reserved"}</div>
              <div className="nm">{c.name}</div>
              <div className="val">{f1(c.value)}</div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
