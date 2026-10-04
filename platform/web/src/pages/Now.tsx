import { agentIcon, Bar, Card, f1, f2, fmtEta, type Snap } from "../ui";

export function NowPage({ s, go }: { s: Snap; go: (p: any) => void }) {
  const st = s.story;
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="kicker">Right now</div>
          <h1>{headline(s)}</h1>
          <p>Everything on this page updates every 3 seconds from the live game. Start with “What to do now”; each line says why and who owns it.</p>
        </div>
      </div>

      <div className="grid g-hero">
        <Card title="Our score" sub="Visible part: 60 of 100 points. Judges add up to 40 at the end.">
          <div className="score-row">
            <div className="score-big num">{f2(st.score)}</div>
            <div className="rank">rank <b>#{st.rank}</b> of {st.of}</div>
          </div>
          <div className="gaps">
            <div className="gap"><div className="l">To pass {st.above?.name ?? "–"}</div><div className="v warn num">{st.above ? `+${f2(st.above.gap)}` : "we lead"}</div></div>
            <div className="gap"><div className="l">Leader {st.leader?.name ?? ""}</div><div className="v num">{st.leader ? (st.leader.team === s.me?.id ? "us" : `+${f2(st.leader.gap)}`) : "–"}</div></div>
            <div className="gap"><div className="l">Ahead of {st.below?.name ?? "–"}</div><div className="v good num">{st.below ? `${f2(st.below.gap)}` : "–"}</div></div>
          </div>
          <div style={{ marginTop: 18 }}>
            {st.components.map((c: Snap) => (
              <div className="bar-row" key={c.id}>
                <div><b>{c.title}</b><div className="faint" style={{ fontSize: 12 }}>of {c.max}</div></div>
                <Bar value={c.value} max={c.max} color={c.id === "market" ? "var(--good)" : "var(--accent)"}
                  marks={[{ at: c.best, title: `best team ${f1(c.best)}` }, { at: c.avg, cls: "avg", title: `average ${f1(c.avg)}` }]} />
                <div className="r num"><b>{f2(c.value)}</b> <span className="faint">/ best {f1(c.best)}</span></div>
              </div>
            ))}
            <div className="bar-legend"><span><i />best team</span><span><i style={{ background: "var(--muted)" }} />average team</span></div>
          </div>
        </Card>

        <Card title="What to do now" sub="Ordered by urgency. Red means points are leaking right now." right={<button className="btn" onClick={() => go("guide")}>Why? Read “How to win”</button>}>
          <div className="list">
            {s.moves.map((m: Snap, i: number) => (
              <div className="item" key={i}>
                <span className={`chip ${m.severity}`}>{m.severity === "good" ? "chance" : m.severity}</span>
                <div><div className="t">{m.title}</div><div className="w">{m.why}</div></div>
                <div className="who">{m.who}</div>
              </div>
            ))}
            {!s.moves.length && <div className="empty">Nothing urgent.</div>}
          </div>
        </Card>
      </div>

      {st.leaks.length > 0 && (
        <Card title="Where we leak points this round" sub="Each is fixable on Sunday: it is a new round that starts from zero.">
          <div className="list">
            {st.leaks.map((l: string, i: number) => (
              <div className="item" key={i}><span className="chip critical">leak</span><div className="t" style={{ fontWeight: 500 }}>{l}</div><span /></div>
            ))}
          </div>
        </Card>
      )}

      <div className="grid g2">
        <Card title="The day ahead" sub="Madrid time. The organisers' schedule: what happens, what it means for us.">
          <Timeline items={s.timeline} />
        </Card>
        <Card title="Our three agents" sub="Shadow = decides and explains, does not act. Live = acts with the team key." right={<button className="btn" onClick={() => go("agents")}>Open agents</button>}>
          <div className="list">
            {s.agents.map((a: Snap) => (
              <div className="item" key={a.id}>
                <div className="agent-ico">{agentIcon(a.id)}</div>
                <div>
                  <div className="t">{a.name} <span className={`chip ${a.mode}`}>{a.mode}</span></div>
                  <div className="w">{a.recent?.[0] ? <><b style={{ color: "var(--text)" }}>{a.recent[0].title}</b> — {a.recent[0].why}</> : a.role}</div>
                </div>
                <div className="who">{Object.entries(a.status).slice(0, 2).map(([k, v]) => `${k.replace(/_/g, " ")}: ${Array.isArray(v) ? v.join(",") || "–" : v ?? "–"}`).join(" · ")}</div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}

function headline(s: Snap): string {
  const c = s.clock;
  if (!c) return "Connecting to the Bazaar…";
  const open = c.doors === "open" && !c.paused;
  const next = s.timeline.find((t: Snap) => t.eta != null && t.eta > 0 && ["bench", "duels", "persona", "end_round"].includes(t.action));
  if (!open) return `Doors closed. ${c.next_name} opens at ${new Date(c.next_opens).toLocaleTimeString("en-GB", { timeZone: "Europe/Madrid", hour: "2-digit", minute: "2-digit" })} Madrid.`;
  return next ? `${c.round_name}: ${next.title} ${fmtEta(next.eta)}.` : `${c.round_name} is live.`;
}

export function Timeline({ items }: { items: Snap[] }) {
  if (!items?.length) return <div className="empty">No schedule published.</div>;
  return (
    <div className="tl">
      {items.map((t: Snap, i: number) => (
        <div key={i} className={`tl-row ${t.action} ${t.leftover ? "leftover" : ""}`}>
          <div className="tl-time">{t.madrid ?? "—"}<small>h {t.at_hours.toFixed(2)}</small></div>
          <div className="tl-node"><i /></div>
          <div>
            <b>{t.title}</b>
            <div className="muted" style={{ fontSize: 13 }}>{t.leftover ? "Left over from Saturday's paused clock: may fire at the opening, or never. " : ""}{t.meaning}</div>
          </div>
          <div className="tl-eta">{fmtEta(t.eta)}</div>
        </div>
      ))}
    </div>
  );
}
