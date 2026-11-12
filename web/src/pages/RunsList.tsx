import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, RunSummary } from "../api";
import { Banner, ago, fmtStarted, Icon, RunStatusChip, Sparkline, useNow } from "../components/ui";
import { S } from "../strings";

type Filter = "all" | "running" | "finished" | "failed" | "stale";

export default function RunsList() {
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [skew, setSkew] = useState(0);
  const [err, setErr] = useState<ApiError | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const now = useNow(1000);

  const load = useCallback(async () => {
    try {
      const r = await api<{ runs: RunSummary[]; server_time: string | null }>("/runs");
      setRuns(r.runs); setErr(null);
      if (r.server_time) setSkew(new Date(r.server_time).getTime() - Date.now());
    } catch (e) { setErr(e as ApiError); }
  }, []);
  useEffect(() => { void load(); const t = setInterval(load, 2000); return () => clearInterval(t); }, [load]);

  const kind = (r: RunSummary): Filter => (r.status === "running" && r.stale ? "stale" : r.status);
  const counts = useMemo(() => { const c: Record<Filter, number> = { all: 0, running: 0, finished: 0, failed: 0, stale: 0 }; (runs ?? []).forEach((r) => { c.all++; c[kind(r)]++; }); return c; }, [runs]);
  const shown = useMemo(() => (runs ?? [])
    .filter((r) => filter === "all" || (filter === "running" ? r.status === "running" && !r.stale : kind(r) === filter))
    .filter((r) => r.name.toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => new Date(b.last_event_at ?? b.created_at).getTime() - new Date(a.last_event_at ?? a.created_at).getTime()), [runs, filter, q]);
  const metric = runs?.find((r) => r.primary_metric)?.primary_metric ?? "value";
  const serverNow = now + skew;

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}><h1 className="t-32" style={{ fontSize: 28 }}>{S.runs.title}</h1></div>
      {err && <Banner tone="danger" icon="warn" role="alert" action={<button className="btn btn-sm" onClick={load}>{S.runs.retry}</button>}>{err.code === "network" ? S.runs.error : S.runs.error}</Banner>}
      {runs === null && !err && <div className="tbl-wrap" aria-busy="true" data-testid="runs-loading">{[0, 1, 2, 3].map((i) => <div key={i} className="sk" style={{ height: 24, margin: 12 }} />)}</div>}
      {runs !== null && runs.length === 0 && (
        <div className="card" style={{ padding: 48, textAlign: "center", display: "flex", flexDirection: "column", gap: 8 }} data-testid="runs-empty"><strong>No runs yet</strong><p className="muted">{S.runs.empty}</p></div>
      )}
      {runs !== null && runs.length > 0 && (
        <>
          <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
            <div className="seg" role="group" aria-label="Filter by status">
              {(["all", "running", "finished", "failed", "stale"] as Filter[]).map((f) => (
                <button key={f} aria-pressed={filter === f} onClick={() => setFilter(f)}>{f === "all" ? "All" : f} <span className="faint num">{counts[f]}</span></button>
              ))}
            </div>
            <div style={{ position: "relative", flex: "1 1 220px", maxWidth: 360 }}>
              <input className="input" style={{ paddingLeft: 34 }} placeholder={S.runs.search} aria-label={S.runs.search} value={q} onChange={(e) => setQ(e.target.value)} />
              <Icon n="search" className="ic" style={{ position: "absolute", left: 12, top: 11, color: "var(--text-3)" }} />
            </div>
            <span style={{ flex: 1 }} /><span className="t-12 faint">{shown.length} of {runs.length} runs</span>
          </div>
          <div className="tbl-wrap">
            <table className="tbl" style={{ tableLayout: "fixed", minWidth: 0 }}>
              <thead><tr>
                <th>Run</th><th style={{ width: 120 }}>Status</th><th className="hide-mobile" style={{ width: 150 }}>Started</th>
                <th style={{ width: 130 }}>Last event ↓</th><th className="r hide-mobile" style={{ width: 90 }}>Events</th><th className="r" style={{ width: 110 }}>{metric}</th><th className="hide-mobile" style={{ width: 110 }}>Trend</th>
              </tr></thead>
              <tbody>
                {shown.map((r) => {
                  const last = r.last_event_at ? new Date(r.last_event_at).getTime() : null;
                  return (
                    <tr key={r.id} data-testid="run-row">
                      <td style={{ overflow: "hidden", textOverflow: "ellipsis" }}><Link to={`/runs/${r.id}`} style={{ fontWeight: 500, color: "var(--text)" }}>{r.name}</Link></td>
                      <td><RunStatusChip status={r.status} stale={r.stale} /></td>
                      <td className="hide-mobile" title={new Date(r.created_at).toLocaleString()}>{fmtStarted(r.created_at)}</td>
                      <td className="mono" style={{ color: r.stale ? "var(--warn)" : undefined }} title={last ? new Date(last).toLocaleString() : ""}>{last ? ago(serverNow - last) : "—"}</td>
                      <td className="mono r hide-mobile num">{r.events_count.toLocaleString()}</td>
                      <td className="mono r">{r.latest_value == null ? "—" : r.latest_value.toFixed(5)}</td>
                      <td className="hide-mobile"><Sparkline points={r.spark} label={`${metric} trend`} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}
