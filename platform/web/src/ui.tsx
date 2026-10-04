import { useEffect, useState, type ReactNode } from "react";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Snap = any;

export const f1 = (x: number | null | undefined) => (x == null || !Number.isFinite(x) ? "–" : x.toFixed(1));
export const f2 = (x: number | null | undefined) => (x == null || !Number.isFinite(x) ? "–" : x.toFixed(2));
export const sign = (x: number | null | undefined, d = 1) => (x == null || !Number.isFinite(x) ? "–" : `${x > 0 ? "+" : ""}${x.toFixed(d)}`);

export function fmtEta(sec: number | null | undefined): string {
  if (sec == null || !Number.isFinite(sec)) return "";
  if (sec <= 0) return "now";
  const m = Math.round(sec / 60);
  if (m < 60) return `in ${m} min`;
  return `in ${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")}`;
}

export function Card({ title, sub, right, children, className = "" }: { title?: ReactNode; sub?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || right) && (
        <div className="card-h">
          <div>
            {title && <h2>{title}</h2>}
            {sub && <div className="sub">{sub}</div>}
          </div>
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function Bar({ value, max, color = "var(--accent)", marks = [] }: { value: number; max: number; color?: string; marks?: { at: number; cls?: string; title?: string }[] }) {
  const pct = (x: number) => `${Math.max(0, Math.min(100, (x / max) * 100))}%`;
  return (
    <div className="bar">
      <i style={{ width: pct(value), background: color }} />
      {marks.map((m, i) => (
        <span key={i} className={`mark ${m.cls ?? ""}`} style={{ left: pct(m.at) }} title={m.title} />
      ))}
    </div>
  );
}

export function Spark({ data, w = 110, h = 28, color = "var(--accent)" }: { data: number[]; w?: number; h?: number; color?: string }) {
  if (!data || data.length < 2) return <span className="faint">–</span>;
  const lo = Math.min(...data), hi = Math.max(...data);
  const span = hi - lo || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - 3 - ((v - lo) / span) * (h - 6)}`).join(" ");
  return (
    <svg width={w} height={h} style={{ display: "block" }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" />
    </svg>
  );
}

export function Dots({ n, of }: { n: number; of: number }) {
  return (
    <span className="progress-dots">
      {Array.from({ length: of }, (_, i) => <i key={i} className={i < n ? "on" : ""} />)}
    </span>
  );
}

export function useTicker(ms = 1000) {
  const [, set] = useState(0);
  useEffect(() => {
    const t = setInterval(() => set((x) => x + 1), ms);
    return () => clearInterval(t);
  }, [ms]);
}

const AGENT_ICON: Record<string, string> = { deal: "🤝", duel: "⚔️", market: "🏛️" };
export const agentIcon = (id: string) => AGENT_ICON[id] ?? "•";

export function DecisionFeed({ items, empty = "No decisions yet." }: { items: Snap[]; empty?: string }) {
  if (!items?.length) return <div className="empty">{empty}</div>;
  return (
    <div className="feed">
      {items.map((d: Snap) => (
        <div className="feed-row" key={d.id}>
          <div className="mono faint" title={new Date(d.ts).toLocaleString()}>
            {new Date(d.ts).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
            <div>t{d.tick}</div>
          </div>
          <div>
            <span className={`chip ${d.outcome}`}>{d.outcome === "shadow" ? "would do" : d.outcome}</span>
            <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>{agentIcon(d.agent)} {d.agent}</div>
          </div>
          <div>
            <div>
              <b>{d.title}</b>
              {d.worth != null && <span className={d.worth >= 0 ? "good" : "bad"}> · {sign(d.worth)}</span>}
            </div>
            <div className="why">{d.why}</div>
            {d.error && <div className="err">{d.error}</div>}
          </div>
        </div>
      ))}
    </div>
  );
}
