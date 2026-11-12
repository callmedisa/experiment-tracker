import { ReactNode, useEffect, useState } from "react";
import { S } from "../strings";

const P = {
  check: <><circle cx="8" cy="8" r="6" /><path d="M5.5 8l1.8 1.8 3.2-3.3" /></>,
  tick: <path d="M4 8l2.5 2.5L12 5" />,
  x: <><circle cx="8" cy="8" r="6" /><path d="M6 6l4 4M10 6l-4 4" /></>,
  warn: <><path d="M8 2.5l6 11H2z" /><path d="M8 7v3" /><path d="M8 12h.01" /></>,
  clock: <><circle cx="8" cy="8" r="6" /><path d="M8 5v3l2 1.5" /></>,
  info: <><circle cx="8" cy="8" r="6" /><path d="M8 7.5V11" /><path d="M8 5h.01" /></>,
  copy: <><rect x="5" y="5" width="8" height="8" rx="1.5" /><path d="M3 10.5V3.5A.5.5 0 0 1 3.5 3h7" /></>,
  spin: <><path d="M13 8a5 5 0 1 1-1.5-3.5" /><path d="M13 3v3h-3" /></>,
  down: <><path d="M8 2v8" /><path d="M5 7l3 3 3-3" /><path d="M3 13h10" /></>,
  off: <><path d="M2 2l12 12" /><path d="M5.5 8.6a4 4 0 0 1 2.2-1.1" /><path d="M2.5 6a8 8 0 0 1 3-1.8" /><path d="M10.5 4.6A8 8 0 0 1 13.5 6" /><path d="M8 12h.01" /></>,
  stop: <rect x="4" y="4" width="8" height="8" rx="1.5" />,
  shield: <><path d="M8 1.5l5 2v4c0 3-2.2 5.3-5 6.5-2.8-1.2-5-3.5-5-6.5v-4z" /><path d="M5.8 8l1.5 1.5 3-3" /></>,
  logo: <path d="M2 12l4-5 3 3 5-7" />,
  out: <><path d="M6 2.5H3.5v11H6" /><path d="M10 5l3 3-3 3" /><path d="M13 8H6" /></>,
  runs: <path d="M2 12l4-5 3 3 5-7" />,
  search: <><circle cx="7" cy="7" r="4.5" /><path d="M10.5 10.5L14 14" /></>,
  eye: <><path d="M1.5 8s2.3-4.5 6.5-4.5S14.5 8 14.5 8 12.2 12.5 8 12.5 1.5 8 1.5 8z" /><circle cx="8" cy="8" r="2" /></>,
  pause: <path d="M5.5 3v10M10.5 3v10" />,
  arrow: <><path d="M8 3v10" /><path d="M4 9l4 4 4-4" /></>,
  chev: <path d="M4 6l4 4 4-4" />,
  key: <><circle cx="5" cy="10" r="3" /><path d="M7.2 7.8L13 2" /><path d="M11 4l2 2" /></>,
  menu: <path d="M2.5 4h11M2.5 8h11M2.5 12h11" />,
  moon: <path d="M13 9.5A5.5 5.5 0 0 1 6.5 3a5.5 5.5 0 1 0 6.5 6.5z" />,
  sun: <><circle cx="8" cy="8" r="3" /><path d="M8 1.5v1.5M8 13v1.5M1.5 8H3M13 8h1.5M3.4 3.4l1 1M11.6 11.6l1 1M3.4 12.6l1-1M11.6 4.4l1-1" /></>,
  plus: <path d="M8 3v10M3 8h10" />,
  revoked: <><circle cx="8" cy="8" r="6" /><path d="M4 12l8-8" /></>,
} satisfies Record<string, ReactNode>;
export type IconName = keyof typeof P;
export function Icon({ n, className = "ic", style }: { n: IconName; className?: string; style?: React.CSSProperties }) {
  return <svg className={className} style={style} viewBox="0 0 16 16" aria-hidden="true">{P[n]}</svg>;
}

export function CopyButton({ text, label = S.copy.copy, className = "btn", icon = true }: { text: string; label?: string; className?: string; icon?: boolean }) {
  const [done, setDone] = useState(false);
  useEffect(() => { if (!done) return; const t = setTimeout(() => setDone(false), 2000); return () => clearTimeout(t); }, [done]);
  return (
    <button type="button" className={className} onClick={async () => { try { await navigator.clipboard.writeText(text); } catch { /* clipboard blocked */ } setDone(true); }}>
      {icon && <Icon n={done ? "tick" : "copy"} />}<span aria-live="polite">{done ? S.copy.done : label}</span>
    </button>
  );
}

export function Banner({ tone = "info", icon, children, action, role = "status" }: { tone?: "info" | "warn" | "danger" | "success"; icon: IconName; children: ReactNode; action?: ReactNode; role?: string }) {
  return (
    <div className={`banner ${tone === "info" ? "" : "banner-" + tone}`} role={role}>
      <Icon n={icon} className={icon === "spin" ? "ic spin" : "ic"} /><div style={{ flex: 1 }}>{children}</div>{action}
    </div>
  );
}

export function useNow(ms = 1000): number {
  const [n, setN] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setN(Date.now()), ms); return () => clearInterval(t); }, [ms]);
  return n;
}

export function ago(msAgo: number): string {
  const s = Math.max(0, Math.floor(msAgo / 1000));
  if (s < 2) return "just now";
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function fmtStarted(iso: string, now = new Date()): string {
  const d = new Date(iso);
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  const same = (a: Date, b: Date) => a.toDateString() === b.toDateString();
  const y = new Date(now); y.setDate(y.getDate() - 1);
  if (same(d, now)) return `Today ${hm}`;
  if (same(d, y)) return `Yesterday ${hm}`;
  return `${d.toLocaleDateString([], { month: "short", day: "numeric" })}, ${hm}`;
}

export function RunStatusChip({ status, stale }: { status: "running" | "finished" | "failed"; stale: boolean }) {
  if (status === "running" && stale) return <span className="chip chip-warn"><Icon n="clock" />stale</span>;
  if (status === "running") return <span className="chip chip-accent"><span className="live-dot" aria-hidden="true" />running</span>;
  if (status === "finished") return <span className="chip chip-success"><Icon n="check" />finished</span>;
  return <span className="chip chip-danger"><Icon n="x" />failed</span>;
}

export function Sparkline({ points, label }: { points: number[]; label: string }) {
  const w = 88, h = 22;
  if (points.length < 2) return <span className="faint" aria-label={label}>—</span>;
  const min = Math.min(...points), max = Math.max(...points), r = max - min || 1;
  const d = points.map((v, i) => `${((i / (points.length - 1)) * (w - 2) + 1).toFixed(1)},${(h - 2 - ((v - min) / r) * (h - 4)).toFixed(1)}`).join(" ");
  const trend = points[points.length - 1] < points[0] ? "falling" : points[points.length - 1] > points[0] ? "rising" : "flat";
  return <svg width={w} height={h} role="img" aria-label={`${label}, ${trend}`}><polyline points={d} fill="none" stroke="var(--chart-1)" strokeWidth="1.5" strokeLinejoin="round" /></svg>;
}
