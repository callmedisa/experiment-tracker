import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import { COLORS, DASHES, MetricChart } from "../components/MetricChart";
import { ago, Banner, CopyButton, Icon, RunStatusChip, useNow } from "../components/ui";
import { RunStore, DEFICIT_WARN_MS } from "../lib/runStore";
import { S, fmt } from "../strings";

const hms = (ms: number) => new Date(ms).toLocaleTimeString([], { hour12: false });
const f5 = (v: number) => v.toFixed(5);

function Dash({ slot }: { slot: number }) {
  return <svg width="16" height="8" viewBox="0 0 16 8" aria-hidden="true"><line x1="1" y1="4" x2="15" y2="4" stroke={`var(${COLORS[slot % 4]})`} strokeWidth="2" strokeDasharray={DASHES[slot % 4].join(" ")} /></svg>;
}

export default function RunPage() {
  const { id = "" } = useParams();
  const [store, setStore] = useState<RunStore | null>(null);
  useEffect(() => {
    const s = new RunStore(id, { onAuthCheck: () => { api("/session").catch(() => {}); } });
    setStore(s);
    if (import.meta.env.VITE_TEST_HOOKS === "1") (window as any).__tracker = s.testHook();
    void s.init();
    return () => { s.dispose(); if ((window as any).__tracker) delete (window as any).__tracker; };
  }, [id]);
  if (!store) return <p className="muted">{S.run.loading}</p>;
  return <RunView store={store} />;
}

function RunView({ store }: { store: RunStore }) {
  useSyncExternalStore(store.subscribe, store.getVersion);
  const now = useNow(1000);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [gapsOpen, setGapsOpen] = useState(true);
  const logRef = useRef<HTMLDivElement>(null);
  const d = store.data, conn = store.conn, run = store.run;

  useEffect(() => { if (!store.paused && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; });

  if (store.error) {
    const e: ApiError = store.error;
    return e.status === 404
      ? <div className="card" style={{ padding: 32 }} data-testid="not-found"><strong>Not found</strong><p className="muted" style={{ marginTop: 8 }}>{S.run.notFound}</p><Link to="/runs">← Runs</Link></div>
      : <Banner tone="danger" icon="warn" role="alert" action={<button className="btn btn-sm" onClick={() => store.init()}>Retry</button>}>{e.code === "network" ? S.signin.errNetwork : fmt(S.sys.error, { id: e.requestId ?? "—" })}</Banner>;
  }
  if (store.loading || !run) return <div aria-busy="true" data-testid="run-loading"><div className="sk" style={{ height: 32, width: 280 }} /><div className="sk" style={{ height: 300, marginTop: 16 }} /></div>;

  const serverNow = now + store.skew;
  const lastEventMs = (store.stats?.last_event_at ?? run.last_event_at) ? new Date((store.stats?.last_event_at ?? run.last_event_at)!).getTime() : null;
  const baseline = lastEventMs ?? new Date(run.created_at).getTime();
  const staleMs = run.stale_after_s * 1000;
  const status = store.stats?.status ?? run.status;
  const isStale = status === "running" && serverNow - baseline >= staleMs;
  const closed = store.closed !== null || status !== "running" && conn.closedByServer;
  const state = conn.state;
  const metrics = d.metricOrder;

  // --- integrity ---
  const received = d.received, stored = store.stored;
  const behind = store.deficitSince > 0 && Date.now() - store.deficitSince > DEFICIT_WARN_MS && state === "live";
  const diff = received - stored;
  const lostFor = conn.lostAt ? ago(now - conn.lostAt) : "";
  const retryIn = Math.max(0, Math.ceil((conn.retryAt - now) / 1000));
  const v = store.verify;

  // --- event log ---
  const upTo = store.paused ? store.pausedAtSeq : d.maxSeq;
  const rows: typeof d.events extends Map<number, infer E> ? E[] : never = [];
  for (let s = upTo; s >= 1 && rows.length < 200; s--) { const e = d.events.get(s); if (e) rows.push(e); }
  rows.reverse();
  const newCount = d.maxSeq - store.pausedAtSeq;

  const chip = (() => {
    if (closed) return <span className="chip chip-lg" title={S.conn.tipClosed}><Icon n="stop" />{S.conn.closed}</span>;
    switch (state) {
      case "live": return <span className="chip chip-success chip-lg" title={S.conn.tipLive}><span className="live-dot" aria-hidden="true" />{S.conn.live}</span>;
      case "connecting": return <span className="chip chip-warn chip-lg" title={S.conn.tipReconnecting}><Icon n="spin" className="ic spin" />{S.conn.connecting}</span>;
      case "reconnecting": return <span className="chip chip-warn chip-lg" title={S.conn.tipReconnecting}><Icon n="spin" className="ic spin" />{S.conn.reconnecting} <span className="num">attempt {conn.attempt} · {retryIn > 0 ? `next try in ${retryIn} s` : "trying now, waiting for the server"}</span></span>;
      case "catching_up": return <span className="chip chip-accent chip-lg" title={S.conn.tipCatching}><Icon n="down" />{S.conn.catching} <span className="num">{conn.replayDone}/{conn.replayTotal}</span></span>;
      case "offline": return <span className="chip chip-lg" title={S.conn.tipOffline}><Icon n="off" />{S.conn.offline}</span>;
      case "resync": return <span className="chip chip-danger chip-lg" title={S.conn.tipResync}><Icon n="warn" />{S.conn.resync}</span>;
    }
  })();

  const late = d.lateRecent.filter((l) => Date.now() - l.at < 5000);
  const chartLabel = metrics.length ? `Chart of ${metrics.join(", ")} against step. ${d.received} points received.` : "Chart, no data yet";
  const points = [...d.series.values()].reduce((n, m) => n + m.size, 0);

  return (
    <>
      {/* RunHeader */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <Link to="/runs" className="t-12" style={{ color: "var(--text-3)" }}>← Runs</Link>
        <div className="stack-mobile" style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <h1 className="t-24" style={{ wordBreak: "break-all" }} data-testid="run-name">{run.name}</h1>
          <RunStatusChip status={status} stale={isStale} />
        </div>
        <div className="t-13 muted" style={{ display: "flex", gap: "8px 16px", flexWrap: "wrap", alignItems: "center" }}>
          <span>Started {new Date(run.created_at).toLocaleString([], { hour12: false })}</span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><span className="mono faint">{run.id}</span><CopyButton text={run.id} label="" className="btn btn-ghost btn-sm icon-btn" /></span>
        </div>
      </div>

      {/* IntegrityBar */}
      <div className="sticky-bar" style={{ paddingTop: 4, background: "var(--bg)" }}>
        <div className="integrity" role="region" aria-label="Connection and data integrity">
          <div aria-live="polite" style={{ display: "flex" }} data-testid="conn-chip" data-state={closed ? "closed" : state}>{chip}</div>
          <span className="vsep hide-mobile" aria-hidden="true" />
          <span className="t-13 muted">Last event <span className="mono" style={{ color: "var(--text)" }} data-testid="last-seq">#{d.cursor}</span> · <span className="mono" style={{ color: isStale ? "var(--warn)" : undefined }}>{lastEventMs ? ago(serverNow - lastEventMs) : "—"}</span></span>
          <span className="vsep hide-mobile" aria-hidden="true" />
          <span className="t-13" style={{ display: "inline-flex", alignItems: "center", gap: 6 }} data-testid="counters">
            {behind ? <Icon n="warn" style={{ color: "var(--warn)" }} /> : <Icon n="check" style={{ color: "var(--success)" }} />}
            <span className="muted">{S.integrity.received}</span> <span className="mono" style={{ color: "var(--text)" }} data-testid="received">{received.toLocaleString()}</span>
            <span className="muted">· {S.integrity.stored}</span> <span className="mono" style={{ color: "var(--text)" }} data-testid="stored">{stored.toLocaleString()}</span>
            {behind && <span className="mono t-12" style={{ color: "var(--warn)" }}>{diff}</span>}
          </span>
          {state === "catching_up" && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8, minWidth: 200 }} className="t-12 muted">Replaying {conn.replayTotal} events…
              <span className="progress" style={{ width: 96 }} role="progressbar" aria-valuemin={0} aria-valuemax={conn.replayTotal} aria-valuenow={conn.replayDone}><span style={{ width: `${conn.replayTotal ? (conn.replayDone / conn.replayTotal) * 100 : 0}%` }} /></span></span>
          )}
          <span style={{ flex: 1 }} />
          {v.status === "match" && <span className="chip chip-success" role="status" data-testid="verify-result"><Icon n="tick" />{S.integrity.match} <span className="mono">{v.checksum!.slice(0, 4)}…{v.checksum!.slice(-4)}</span></span>}
          {v.status === "mismatch" && <span className="chip chip-danger" role="status" data-testid="verify-result"><Icon n="x" />{v.missing && v.missing.length ? fmt(S.integrity.mismatch, { n: v.missing.length }) : S.integrity.mismatchNoList}</span>}
          {v.note && <span className="help-error">{v.note}</span>}
          <button className="btn btn-sm" onClick={() => store.doVerify()} disabled={v.status === "busy"} data-testid="verify"><Icon n="shield" className={v.status === "busy" ? "ic spin" : "ic"} />{v.status === "busy" ? S.integrity.verifying : v.status === "idle" ? S.integrity.verify : S.integrity.verifyAgain}</button>
        </div>
      </div>

      {/* banners */}
      {state === "offline" && <Banner icon="off" role="status">{fmt(S.run.offline, { seq: d.cursor, time: conn.lostAt ? hms(conn.lostAt) : "" })}</Banner>}
      {(state === "reconnecting") && <Banner tone="warn" icon="spin" action={<button className="btn btn-sm" onClick={() => conn.retryNow()}>Retry now</button>}>{fmt(S.run.recon, { t: lostFor })}</Banner>}
      {state === "resync" && <Banner tone="danger" icon="spin" role="alert">{S.run.resync}</Banner>}
      {v.status === "mismatch" && (
        <Banner tone="danger" icon="warn" role="alert" action={<button className="btn btn-sm btn-danger" onClick={() => store.reloadSnapshot(true)}>Reload from server</button>}>
          <strong>Integrity mismatch.</strong> This page has {v.pageCount} events; the server has {v.serverCount}.{v.missing && v.missing.length ? <> Missing: <span className="mono">{v.missing.slice(0, 8).map((s) => "#" + s).join(", ")}{v.missing.length > 8 ? "…" : ""}</span>.</> : null} Charts may be incomplete.
        </Banner>
      )}
      {isStale && !closed && <Banner tone="warn" icon="clock">{fmt(S.run.stale, { m: Math.floor((serverNow - baseline) / 60000) })}</Banner>}
      {closed && <Banner tone="success" icon="check">{fmt(status === "failed" ? S.run.failed : S.run.finished, { time: store.closed?.finished_at ? hms(new Date(store.closed.finished_at).getTime()) : "", n: d.received.toLocaleString() })}</Banner>}
      {store.toasts.map((t) => <div key={t.id} className="toast" role="status" style={{ alignSelf: "flex-start" }}><Icon n={t.kind === "caught" ? "down" : t.kind === "late" ? "clock" : "copy"} />{t.text}</div>)}
      {late.length > 0 && <div className="toast" role="status" style={{ alignSelf: "flex-start" }}><Icon n="clock" style={{ color: "var(--warn)" }} />{fmt(S.run.toastLate, { n: late.length, from: Math.min(...late.map((l) => l.step)), to: Math.max(...late.map((l) => l.step)) })}</div>}

      {/* MetricChart */}
      <section className="card" aria-labelledby="ch-h">
        <div className="card-h" style={{ flexWrap: "wrap" }}>
          <h2 id="ch-h" className="t-13" style={{ fontWeight: 600 }}>Metrics</h2>
          <div className="seg" role="group" aria-label="Show metrics" style={{ flexWrap: "wrap" }}>
            {metrics.map((m, i) => (
              <button key={m} aria-pressed={!hidden.has(m)} onClick={() => { const n = new Set(hidden); n.has(m) ? n.delete(m) : n.add(m); setHidden(n); }}><Dash slot={i} />{m}</button>
            ))}
          </div>
          <span style={{ flex: 1 }} /><span className="t-12 faint mono" data-testid="points-text">{points.toLocaleString()} points</span>
        </div>
        <div style={{ padding: "12px 16px 8px", position: "relative" }}>
          {metrics.length > 0 ? (
            <>
              <MetricChart data={d} metrics={metrics} hidden={hidden} version={store.v} label={chartLabel} />
              <p className="help" style={{ paddingTop: 4 }}>Late points are ringed for 5 s where they belong.</p>
            </>
          ) : (
            <div style={{ height: 300, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 10, textAlign: "center", border: "1px dashed var(--border-strong)", borderRadius: 8 }} data-testid="chart-empty">
              <span className="chip chip-accent"><span className="live-dot" aria-hidden="true" />Listening</span><strong>No metrics yet</strong>
              <p className="muted t-13" style={{ maxWidth: 420 }}>The run exists, but no events have arrived. Points appear here the moment your script calls <span className="mono">run.log()</span>.</p>
            </div>
          )}
          {state === "resync" && <div style={{ position: "absolute", inset: 0, background: "color-mix(in srgb, var(--surface) 82%, transparent)", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 10 }}><Icon n="spin" className="ic spin" /><strong>Refreshing data…</strong></div>}
        </div>
      </section>

      {/* LatestValues */}
      <div className="grid-4" aria-label="Latest values" role="list">
        {metrics.slice(0, 4).map((m, i) => { const s = d.stats(m)!; return (
          <div key={m} className="card" role="listitem" style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 6 }} data-testid="latest-card">
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}><Dash slot={i} /><span className="t-12 muted" style={{ fontWeight: 500 }}>{m}</span><span style={{ flex: 1 }} /><span className="mono t-11 faint">step {s.step}</span></div>
            <span className="stat-v">{f5(s.last)}</span>
            <div className="mono t-11 faint" style={{ display: "flex", gap: 10, flexWrap: "wrap" }}><span>min {s.min.toFixed(4)}</span><span>max {s.max.toFixed(4)}</span><span>n {s.n}</span></div>
          </div>
        ); })}
      </div>

      {/* EventTable + GapsPanel */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-start" }}>
        <section className="card" style={{ flex: "999 1 560px", minWidth: 0 }} aria-labelledby="ev-h">
          <div className="card-h" style={{ flexWrap: "wrap" }}>
            <h2 id="ev-h" className="t-13" style={{ fontWeight: 600 }}>Event log</h2><span className="mono t-12 faint">{d.received.toLocaleString()} events</span><span style={{ flex: 1 }} />
            {store.paused
              ? <button className="btn btn-sm btn-primary" onClick={() => store.resume()}><Icon n="arrow" />{fmt(S.run.resume, { n: newCount })}</button>
              : <button className="btn btn-sm" onClick={() => store.pause()}><Icon n="pause" />{S.run.pause}</button>}
          </div>
          <div ref={logRef} style={{ maxHeight: 420, overflow: "auto" }} tabIndex={0} aria-label="Event log, newest last">
            <table className="tbl dense" style={{ borderRadius: 0 }} data-testid="event-table">
              <thead><tr><th scope="col">#seq</th><th scope="col" className="r">step</th><th scope="col">metric</th><th scope="col" className="r">value</th><th scope="col">client time</th><th scope="col">received</th><th scope="col">flags</th></tr></thead>
              <tbody>
                {rows.map((e) => (
                  <tr key={e.seq} data-testid="event-row">
                    <td className="mono">#{e.seq}</td><td className="mono r">{e.step}</td><td>{e.metric}</td><td className="mono r">{e.value}</td>
                    <td className="mono faint">{e.ts ? hms(new Date(e.ts).getTime()) : "—"}</td><td className="mono faint">{hms(new Date(e.received_at).getTime())}</td>
                    <td>{e.is_late && <span className="badge badge-warn"><Icon n="clock" />late</span>} {e.replayed && <span className="badge badge-accent">replayed</span>}</td>
                  </tr>
                ))}
                {rows.length === 0 && <tr><td colSpan={7} style={{ height: 96, textAlign: "center" }} className="muted">No events yet.</td></tr>}
              </tbody>
            </table>
          </div>
        </section>
        <section className="card" style={{ flex: "1 1 300px", minWidth: 0 }} aria-labelledby="gp-h">
          <div className="card-h">
            <button className="btn btn-ghost btn-sm" onClick={() => setGapsOpen(!gapsOpen)} aria-expanded={gapsOpen} aria-controls="gp-body" style={{ marginLeft: -8, fontWeight: 600, color: "var(--text)" }}><Icon n="chev" /><span id="gp-h">Gaps</span></button>
            <span className={`chip ${d.gaps.some((g) => g.status === "open") ? "chip-warn" : ""}`} style={{ height: 22 }}>{d.gaps.length === 0 ? "none" : `${d.gaps.length} this session`}</span>
          </div>
          {gapsOpen && (
            <div id="gp-body" style={{ padding: "8px 16px 16px", display: "flex", flexDirection: "column", gap: 10 }}>
              {d.gaps.map((g, i) => (
                <div key={i} style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "8px 0", borderBottom: "1px solid var(--border)" }}>
                  <Icon n={g.status === "filled" ? "check" : g.status === "open" ? "warn" : "spin"} style={{ color: g.status === "filled" ? "var(--success)" : "var(--warn)", marginTop: 3 }} />
                  <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 2 }}><span className="mono">#{g.from}–#{g.to}</span><span className="t-12 muted">{g.reason === "disconnect" ? "Missed while disconnected" : "Missing seq numbers"}</span></div>
                  <span className="t-12" style={{ fontWeight: 500 }}>{g.status}</span>
                </div>
              ))}
              {d.gaps.length === 0 && <p className="muted t-13">No gaps detected. Every #seq from #1 to #{d.cursor} is present.</p>}
            </div>
          )}
        </section>
      </div>
    </>
  );
}
