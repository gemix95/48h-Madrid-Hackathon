import { useCallback, useEffect, useState } from "react";
import { f2, useTicker, type Snap } from "./ui";
import { NowPage } from "./pages/Now";
import { GuidePage } from "./pages/Guide";
import { AgentsPage } from "./pages/Agents";
import { DuelsPage } from "./pages/Duels";
import { DealersPage } from "./pages/Dealers";
import { MarketPage } from "./pages/Market";
import { AlbumPage } from "./pages/Album";
import { RivalsPage } from "./pages/Rivals";

const PAGES = [
  { id: "now", label: "Now", ico: "◉" },
  { id: "guide", label: "How to win", ico: "📖" },
  { id: "agents", label: "Agents", ico: "🤖" },
  { id: "duels", label: "Duels", ico: "⚔️" },
  { id: "dealers", label: "Dealers", ico: "🧶" },
  { id: "market", label: "Market", ico: "🏛️" },
  { id: "album", label: "Album & trades", ico: "🃏" },
  { id: "rivals", label: "Rivals", ico: "🏁" },
] as const;

type PageId = (typeof PAGES)[number]["id"];

export function App() {
  const [snap, setSnap] = useState<Snap | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [page, setPage] = useState<PageId>(() => (location.hash.slice(1) as PageId) || "now");
  const [fetchedAt, setFetchedAt] = useState(0);
  useTicker(1000);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/snapshot");
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
      setSnap(await r.json());
      setFetchedAt(Date.now());
      setErr(null);
    } catch (e) {
      setErr(String(e));
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    const on = () => setPage((location.hash.slice(1) as PageId) || "now");
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const go = (p: PageId) => {
    location.hash = p;
    setPage(p);
  };

  const critical = snap?.moves?.filter((m: Snap) => m.severity === "critical").length ?? 0;

  return (
    <div className="shell">
      <aside className="side">
        <div className="brand">
          <div className="brand-logo">🃏</div>
          <div>
            <b>War Room</b>
            <span>Team 13 · The Bazaar</span>
          </div>
        </div>
        {PAGES.map((p) => (
          <button key={p.id} className={`nav ${page === p.id ? "on" : ""}`} onClick={() => go(p.id)}>
            <span className="ico">{p.ico}</span>
            {p.label}
            {p.id === "now" && critical > 0 && <span className="badge">{critical}</span>}
          </button>
        ))}
        <div className="side-foot">
          {snap ? <>Data {Math.round((Date.now() - fetchedAt) / 1000)} s old · {snap.health.feedEvents} feed events kept</> : "Connecting…"}
        </div>
      </aside>
      <main className="main">
        <TopBar snap={snap} err={err} />
        {!snap ? (
          <div className="page"><div className="empty">{err ? `Cannot reach the war room server: ${err}` : "Loading the game…"}</div></div>
        ) : (
          <>
            {page === "now" && <NowPage s={snap} go={go} />}
            {page === "guide" && <GuidePage s={snap} />}
            {page === "agents" && <AgentsPage s={snap} reload={load} />}
            {page === "duels" && <DuelsPage s={snap} />}
            {page === "dealers" && <DealersPage s={snap} />}
            {page === "market" && <MarketPage s={snap} />}
            {page === "album" && <AlbumPage s={snap} />}
            {page === "rivals" && <RivalsPage s={snap} />}
          </>
        )}
      </main>
    </div>
  );
}

function TopBar({ snap, err }: { snap: Snap | null; err: string | null }) {
  const c = snap?.clock;
  const open = c && c.doors === "open" && !c.paused;
  const story = snap?.story;
  const since = snap ? (Date.now() - snap.now) / 1000 : 0;
  const nextTick = c ? Math.max(0, c.next_tick_in - since) : 0;
  return (
    <div className="topbar">
      <span className="pill">
        <span className={`dot ${open ? "live" : "closed"}`} />
        <b>{c ? (open ? "Doors open" : c.paused && c.doors === "open" ? "Clock paused" : "Doors closed") : "…"}</b>
        {c && <span>{c.round_name}</span>}
      </span>
      {c && (
        <span className="pill">
          tick <b className="mono">{c.tick}</b> · every <b>{c.tick_seconds}s</b>
          {open && <> · next in <b className="mono">{nextTick.toFixed(0)}s</b></>}
        </span>
      )}
      {c && !open && c.next_opens && (
        <span className="pill">
          {c.next_name} opens <b>{new Date(c.next_opens).toLocaleTimeString("en-GB", { timeZone: "Europe/Madrid", hour: "2-digit", minute: "2-digit" })}</b> Madrid
        </span>
      )}
      <span style={{ flex: 1 }} />
      {story && (
        <>
          <span className="pill">score <b>{f2(story.score)}</b></span>
          <span className="pill">rank <b>#{story.rank}</b> / {story.of}</span>
          {snap?.me && <span className="pill">cash <b>{snap.me.cash} P</b></span>}
        </>
      )}
      {err && <span className="pill bad">offline</span>}
    </div>
  );
}
