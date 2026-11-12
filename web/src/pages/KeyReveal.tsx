import { useEffect, useState } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api } from "../api";
import { Logo } from "../components/Shell";
import { Trk } from "../components/Trk";
import { Banner, CopyButton, Icon } from "../components/ui";
import { S } from "../strings";
import { clearPendingKeys, pendingKeys } from "./Landing";

function Secret({ title, scope, help, value, id }: { title: string; scope: string; help: string; value: string; id: string }) {
  const [shown, setShown] = useState(false);
  const masked = value.slice(0, value.indexOf("_", 4) + 1) + "•".repeat(22) + value.slice(-4);
  return (
    <div className="field">
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <label htmlFor={id} className="label" style={{ fontSize: 14, color: "var(--text)", fontWeight: 600 }}>{title}</label>
        <span className="chip">{scope}</span><span className="help">{help}</span>
      </div>
      <div style={{ display: "flex", gap: 8 }}>
        <input id={id} readOnly className="input mono" value={shown ? value : masked} aria-label={`${title} (${shown ? "visible" : "hidden"})`} data-testid={id} />
        <button type="button" className="btn icon-btn" aria-label={shown ? "Hide key" : "Show key"} aria-pressed={shown} onClick={() => setShown(!shown)}><Icon n="eye" /></button>
        <CopyButton text={value} />
      </div>
    </div>
  );
}

export const pythonSnippet = (key: string, origin = window.location.origin) => `pip install ./client   # from the Tracker repository (not on PyPI)

import tracker
run = tracker.start(key="${key}",
                    run="my-first-run",
                    url="${origin}/api")
for step in range(100):
    run.log(step, loss=1/(step+1))
run.finish()`;

export const curlSnippet = (key: string, origin: string) => `# 1. create a run (returns {"id": "run_…"})
curl -s -X POST ${origin}/api/runs \\
  -H "Authorization: Bearer ${key}" -H "Content-Type: application/json" \\
  -d '{"name": "my-first-run"}'

# 2. send points to that run id
curl -s -X POST ${origin}/api/runs/RUN_ID/metrics \\
  -H "Authorization: Bearer ${key}" -H "Content-Type: application/json" \\
  -d '{"batch_id": "b-1", "points": [{"metric": "loss", "step": 0, "value": 1.0}]}'`;

export default function KeyReveal() {
  const nav = useNavigate();
  const k = pendingKeys;
  const [tab, setTab] = useState<"python" | "curl">("python");
  const [ok, setOk] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => () => { /* leaving the page drops the keys from memory */ clearPendingKeys(); }, []);
  if (!k) return <Navigate to="/" replace />;
  const snippet = tab === "python" ? pythonSnippet(k.ingest) : curlSnippet(k.ingest, window.location.origin);

  async function cont() {
    setErr("");
    try { await api("/session", { method: "POST", body: { key: k!.admin } }); nav("/runs"); }
    catch { setErr(S.signin.errNetwork); }
  }

  return (
    <Trk style={{ minHeight: "100%" }}>
      <header className="topbar" style={{ background: "transparent", borderBottom: 0 }}><Logo /></header>
      <div style={{ maxWidth: 760, margin: "0 auto", padding: "24px 16px 64px" }}>
        <div className="card" style={{ padding: 28, display: "flex", flexDirection: "column", gap: 18 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}><span className="chip chip-success"><Icon n="tick" />{S.reveal.created}</span><span className="mono faint">{k.name}</span></div>
          <h1 className="t-24">{S.reveal.title}</h1>
          <Banner tone="warn" icon="warn"><strong>These keys are shown once.</strong> {S.reveal.warning.replace("These keys are shown once. ", "")}</Banner>
          <Secret id="ingest-key" title={S.reveal.ingestTitle} scope="ingest" help={S.reveal.ingestHelp} value={k.ingest} />
          <Secret id="read-key" title={S.reveal.readTitle} scope="read" help={S.reveal.readHelp} value={k.read} />
          <Secret id="admin-key" title={S.reveal.adminTitle} scope="admin" help={S.reveal.adminHelp} value={k.admin} />
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <h2 className="t-13" style={{ fontWeight: 600, fontSize: 14, flex: 1 }}>{S.reveal.snippetTitle}</h2>
            <div className="seg" role="group" aria-label="Language">
              <button aria-pressed={tab === "python"} onClick={() => setTab("python")}>Python</button>
              <button aria-pressed={tab === "curl"} onClick={() => setTab("curl")}>curl</button>
            </div>
            <CopyButton text={snippet} label={S.reveal.copySnippet} />
          </div>
          <pre className="code" tabIndex={0}>{snippet}</pre>
          <p className="help">{S.reveal.snippetNote}</p>
          <div style={{ borderTop: "1px solid var(--border)", margin: "0 -28px", padding: "16px 28px 0", display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
            <label style={{ display: "flex", gap: 10, alignItems: "center", flex: 1 }}><input type="checkbox" checked={ok} onChange={(e) => setOk(e.target.checked)} />{S.reveal.confirm}</label>
            {err && <span className="help-error">{err}</span>}
            <button className="btn btn-primary" disabled={!ok} onClick={cont}>{S.reveal.cont}</button>
          </div>
        </div>
      </div>
    </Trk>
  );
}
