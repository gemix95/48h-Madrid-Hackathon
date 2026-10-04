import { useState } from "react";
import { agentIcon, Card, DecisionFeed, type Snap } from "../ui";

const CONFLICT: Record<string, string> = {
  duel: "The Python agents on the server also play duels (enable_duels). Two duel players on one key send two messages per duel and fight over accepts. Before LIVE here, set enable_duels = 0 on the server's Strategy tab.",
  market: "Only one broker may match per venue. Before LIVE here, stop the bazaar-broker service on the server, and start this server with BROKER_KEY.",
  deal: "The Python trader also accepts board offers, and the team has ONE accept per tick. Set enable_trader = 0 on the server first, or start this server with ACCEPT_PARITY=odd/even so the two take turns. It haggles only with the dealers listed in DEAL_DEALERS.",
};

export function AgentsPage({ s, reload }: { s: Snap; reload: () => void }) {
  const [filter, setFilter] = useState<string>("all");
  const [msg, setMsg] = useState<string | null>(null);

  const setMode = async (id: string, mode: string) => {
    if (mode === "live" && !confirm(`Switch the ${id} agent to LIVE?\n\nIt will act with the team key.\n\n${CONFLICT[id]}`)) return;
    const r = await fetch(`/api/agents/${id}/mode`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode }) });
    const j = await r.json();
    setMsg(r.ok ? `${id} agent → ${mode}` : j.message ?? j.error);
    reload();
  };

  const feed = filter === "all" ? s.decisions : s.decisions.filter((d: Snap) => d.agent === filter);

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="kicker">Agents</div>
          <h1>Three agents, one job each</h1>
          <p>Each agent explains every decision in plain words: what it would do, why, and what it is worth. Start in <b>shadow</b> (decides, does not act) next to the Python agents; switch one to <b>live</b> only after switching the matching Python module off. One team key means one accept per tick for everyone.</p>
        </div>
      </div>
      {msg && <div className="banner">{msg}</div>}
      {!s.hasKey && <div className="banner bad">No team key: the agents can only read public data. Start the server with BAZAAR_KEY (it reads ../bazaar.env).</div>}

      <div className="grid g3">
        {s.agents.map((a: Snap) => (
          <Card key={a.id}>
            <div className="agent-card">
              <div className="agent-top">
                <div className="agent-ico">{agentIcon(a.id)}</div>
                <div style={{ flex: 1 }}>
                  <h2>{a.name}</h2>
                  <span className={`chip ${a.mode}`}>{a.mode}</span>
                </div>
              </div>
              <div className="seg">
                {["off", "shadow", "live"].map((m) => (
                  <button key={m} className={`${m} ${a.mode === m ? "on" : ""}`} onClick={() => setMode(a.id, m)}>{m === "shadow" ? "Shadow" : m === "live" ? "Live" : "Off"}</button>
                ))}
              </div>
              <p className="muted">{a.role}</p>
              <div className="agent-stats">
                {Object.entries(a.status).map(([k, v]) => (
                  <span className="pill" key={k}>{k.replace(/_/g, " ")} <b>{Array.isArray(v) ? (v.length ? v.join(", ") : "–") : v === true ? "yes" : v === false ? "no" : String(v ?? "–")}</b></span>
                ))}
              </div>
              <div className="faint" style={{ fontSize: 12.5 }}>⚠ {CONFLICT[a.id]}</div>
            </div>
          </Card>
        ))}
      </div>

      <Card title="Every decision, newest first" sub="“would do” = shadow mode: the agent decided this but did not act." right={
        <div className="filters">
          {["all", "deal", "duel", "market"].map((f) => <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>{f === "all" ? "All" : `${agentIcon(f)} ${f}`}</button>)}
        </div>
      }>
        <DecisionFeed items={feed} empty="No decisions yet. Agents decide while the doors are open; the market agent also reports our venue's health." />
      </Card>
    </div>
  );
}
