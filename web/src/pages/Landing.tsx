import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError, SessionInfo } from "../api";
import { Logo } from "../components/Shell";
import { Trk } from "../components/Trk";
import { Banner, Icon } from "../components/ui";
import { S, fmt } from "../strings";

/** One-time keys live only in memory (module variable): never in the URL, storage or history state. */
export let pendingKeys: { name: string; ingest: string; read: string } | null = null;
export const clearPendingKeys = () => { pendingKeys = null; };

export default function Landing() {
  const nav = useNavigate();
  const [params] = useSearchParams();
  const reason = params.get("reason");
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [nameErr, setNameErr] = useState("");
  const [keyErr, setKeyErr] = useState("");
  const [banner, setBanner] = useState("");
  const [busy, setBusy] = useState<"" | "create" | "sign">("");

  useEffect(() => { api<SessionInfo>("/session").then(() => nav("/runs", { replace: true })).catch(() => {}); }, [nav]);

  const rateText = (e: ApiError) => fmt(S.signin.errRate, { s: e.retryAfter ?? 60 });

  async function create(ev: FormEvent) {
    ev.preventDefault(); setNameErr(""); setBanner("");
    if (!/^[A-Za-z0-9-]{3,40}$/.test(name)) { setNameErr(S.signin.errName); return; }
    setBusy("create");
    try {
      const r = await api<{ project: { name: string }; keys: { ingest: { key: string }; read: { key: string } } }>("/projects", { method: "POST", body: { name } });
      pendingKeys = { name: r.project.name, ingest: r.keys.ingest.key, read: r.keys.read.key };
      nav("/keys-created");
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 429) setBanner(rateText(err)); else if (err.code === "network") setBanner(S.signin.errNetwork); else if (err.status === 422) setNameErr(S.signin.errName); else setBanner(S.sys.error.replace("{id}", err.requestId ?? "—"));
    } finally { setBusy(""); }
  }

  async function signIn(ev: FormEvent) {
    ev.preventDefault(); setKeyErr(""); setBanner("");
    const k = key.trim();
    if (k.startsWith("trk_ing_")) { setKeyErr(S.signin.errIngest); return; }
    if (!/^trk_[A-Za-z0-9_]+$/.test(k) || k.length !== 40) { setKeyErr(S.signin.errInvalid); return; }
    setBusy("sign");
    try {
      await api<SessionInfo>("/session", { method: "POST", body: { key: k } });
      setKey(""); nav("/runs");
    } catch (e) {
      const err = e as ApiError;
      if (err.code === "key_revoked") setKeyErr(S.signin.errRevoked);
      else if (err.code === "invalid_key") setKeyErr(S.signin.errInvalid);
      else if (err.status === 429) setBanner(rateText(err));
      else if (err.code === "network") setBanner(S.signin.errNetwork);
      else setBanner(S.sys.error.replace("{id}", err.requestId ?? "—"));
    } finally { setBusy(""); }
  }

  return (
    <Trk style={{ minHeight: "100%" }}>
      <header className="topbar" style={{ background: "transparent", borderBottom: 0 }}><Logo /></header>
      <div style={{ maxWidth: 1000, margin: "0 auto", padding: "48px 16px 64px", display: "flex", flexDirection: "column", gap: 24, alignItems: "stretch" }}>
        <div style={{ textAlign: "center", display: "flex", flexDirection: "column", gap: 12 }}>
          <h1 className="t-32">{S.signin.title}</h1>
          <p className="muted t-16" style={{ maxWidth: 640, margin: "0 auto" }}>{S.signin.sub}</p>
        </div>
        {reason === "expired" && <Banner tone="warn" icon="clock">{S.signin.reasonExpired}</Banner>}
        {reason === "revoked" && <Banner tone="warn" icon="warn" role="alert">{S.signin.reasonRevoked}</Banner>}
        {banner && <Banner tone="danger" icon="warn" role="alert">{banner}</Banner>}
        <div className="grid-2" style={{ alignItems: "start" }}>
          <form className="card" style={{ padding: 24, display: "flex", flexDirection: "column", gap: 16 }} onSubmit={create} noValidate>
            <div><h2 className="t-20">{S.signin.createTitle}</h2><p className="muted" style={{ marginTop: 4 }}>{S.signin.createSub}</p></div>
            <div className="field">
              <label className="label" htmlFor="pname">{S.signin.nameLabel}</label>
              <input id="pname" className={`input ${nameErr ? "is-error" : ""}`} value={name} onChange={(e) => setName(e.target.value)} placeholder="vision-experiments" autoComplete="off" aria-invalid={!!nameErr} aria-describedby="pname-h" />
              {nameErr ? <span id="pname-h" className="help-error" role="alert"><Icon n="warn" />{nameErr}</span> : <span id="pname-h" className="help">{S.signin.nameHelp}</span>}
            </div>
            <button className="btn btn-primary btn-lg" disabled={busy !== ""}>{busy === "create" ? "Creating…" : S.signin.createCta}</button>
          </form>
          <form className="card" style={{ padding: 24, display: "flex", flexDirection: "column", gap: 16 }} onSubmit={signIn} noValidate>
            <div><h2 className="t-20">{S.signin.keyTitle}</h2><p className="muted" style={{ marginTop: 4 }}>Paste a read key. It starts with <span className="mono">trk_</span>.</p></div>
            <div className="field">
              <label className="label" htmlFor="pkey">{S.signin.keyLabel}</label>
              <input id="pkey" className={`input mono ${keyErr ? "is-error" : ""}`} value={key} onChange={(e) => setKey(e.target.value)} placeholder="trk_…" autoComplete="off" spellCheck={false} aria-invalid={!!keyErr} aria-describedby="pkey-h" type="password" />
              {keyErr ? <span id="pkey-h" className="help-error" role="alert"><Icon n="warn" />{keyErr}</span> : <span id="pkey-h" className="help">{S.signin.keyHelp}</span>}
            </div>
            <button className="btn btn-lg" style={{ color: "var(--accent-text)" }} disabled={busy !== ""}>{busy === "sign" ? "Signing in…" : S.signin.cta}</button>
          </form>
        </div>
        <p className="help" style={{ textAlign: "center" }}>{S.signin.footer}</p>
      </div>
    </Trk>
  );
}
