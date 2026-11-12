import { FormEvent, useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError, holdAuthRedirects, KeyRow, SessionInfo } from "../api";
import { Dialog } from "../components/Dialog";
import { Banner, CopyButton, Icon, ago } from "../components/ui";
import { S, fmt } from "../strings";

const shown = (k: Pick<KeyRow, "scope" | "prefix">) => `${k.scope === "ingest" ? "trk_ing_" : "trk_rd_"}${k.prefix}…`;
const day = (iso: string) => new Date(iso).toLocaleDateString([], { month: "short", day: "numeric", year: "numeric" });
const when = (d = new Date()) => d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
/** "Last used" is written at most once a minute per key, so it is shown in whole minutes (owner-approved spec-gap). */
const used = (iso: string | null) => (iso ? (Date.now() - new Date(iso).getTime() < 60_000 ? "< 1 min ago" : ago(Date.now() - new Date(iso).getTime())) : S.keys.never);

type Modal =
  | { kind: "create" }
  | { kind: "rotate"; key: KeyRow }
  | { kind: "revoke"; key: KeyRow }
  | { kind: "reveal"; title: string; secret: string; oldNote?: string; thenSignOut: boolean };

export default function Keys() {
  const nav = useNavigate();
  const [keys, setKeys] = useState<KeyRow[] | null>(null);
  const [me, setMe] = useState<SessionInfo["key"] | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  const [modal, setModal] = useState<Modal | null>(null);
  const [toast, setToast] = useState("");

  const load = useCallback(async () => {
    try {
      const [k, s] = await Promise.all([api<{ keys: KeyRow[] }>("/keys"), api<SessionInfo>("/session")]);
      setKeys(k.keys); setMe(s.key); setErr(null);
    } catch (e) { setErr(e as ApiError); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const isMine = (k: KeyRow) => !!me && k.scope === me.scope && k.prefix === me.prefix && !k.revoked_at;
  const close = useCallback(() => setModal(null), []);

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4, minWidth: 220 }}><h1 className="t-24">{S.keys.title}</h1><p className="muted t-13">{S.keys.sub}</p></div>
        <button className="btn btn-primary" onClick={() => setModal({ kind: "create" })} data-testid="create-key"><Icon n="plus" />{S.keys.create}</button>
      </div>
      {toast && <Banner tone="success" icon="tick">{toast}</Banner>}
      {err && <Banner tone="danger" icon="warn" role="alert" action={<button className="btn btn-sm" onClick={load}>{S.runs.retry}</button>}>{err.code === "network" ? S.signin.errNetwork : fmt(S.sys.error, { id: err.requestId ?? "—" })}</Banner>}
      {keys === null && !err && <div className="tbl-wrap" aria-busy="true">{[0, 1, 2].map((i) => <div key={i} className="sk" style={{ height: 24, margin: 12 }} />)}</div>}
      {keys && (
        <div className="tbl-wrap">
          <table className="tbl" data-testid="keys-table">
            <thead><tr><th scope="col">Label</th><th scope="col">Scope</th><th scope="col">Key</th><th scope="col" className="hide-mobile">Created</th><th scope="col">Last used</th><th scope="col">Status</th><th scope="col"><span className="hide-mobile">Actions</span></th></tr></thead>
            <tbody>
              {keys.map((k) => {
                const revoked = !!k.revoked_at;
                return (
                  <tr key={k.id} data-testid="key-row" data-key-id={k.id}>
                    <td style={{ fontWeight: 500, color: revoked ? "var(--text-3)" : "var(--text)" }}>{k.label}{isMine(k) && <span className="chip chip-accent" style={{ height: 20, marginLeft: 8 }}>{S.keys.thisSession}</span>}</td>
                    <td><span className="chip" style={{ height: 22 }}>{k.scope}</span></td>
                    <td className="mono" style={{ color: revoked ? "var(--text-3)" : "var(--text)" }}>{shown(k)}</td>
                    <td className="muted hide-mobile">{day(k.created_at)}</td>
                    <td className="muted">{used(k.last_used_at)}</td>
                    <td>{revoked ? <span className="chip"><Icon n="revoked" />revoked · {when(new Date(k.revoked_at!))}</span> : <span className="chip chip-success"><Icon n="tick" />active</span>}</td>
                    <td>{!revoked && <div style={{ display: "flex", gap: 4 }}>
                      <button className="btn btn-sm" aria-label={`Rotate ${k.label}`} onClick={() => setModal({ kind: "rotate", key: k })}>Rotate</button>
                      <button className="btn btn-sm btn-ghost" style={{ color: "var(--danger)" }} aria-label={`Revoke ${k.label}`} onClick={() => setModal({ kind: "revoke", key: k })}>Revoke</button>
                    </div>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="help">{S.keys.help}</p>

      {modal?.kind === "create" && <CreateDialog onClose={close} onCreated={async (k) => { setModal({ kind: "reveal", title: S.keys.revealCreated, secret: k.key!, thenSignOut: false }); setToast(fmt(S.keys.toastCreated, { name: k.label })); await load(); }} />}
      {modal?.kind === "rotate" && <RotateDialog k={modal.key} onClose={close} onDone={async (nk) => {
        const mine = isMine(modal.key);
        const note = fmt(S.keys.oldRevoked, { prefix: shown(modal.key), time: when() });
        setToast(fmt(S.keys.toastRotated, { name: modal.key.label, time: when() }));
        setModal({ kind: "reveal", title: fmt(S.keys.revealRotated, { name: modal.key.label }), secret: nk.key!, oldNote: note, thenSignOut: mine });
        if (!mine) await load();           // rotating the key of THIS session ends the session: nothing more can be fetched
      }} holdFor={isMine(modal.key)} />}
      {modal?.kind === "revoke" && <RevokeDialog k={modal.key} onClose={close} onDone={async () => {
        if (isMine(modal.key)) { nav("/sign-in?reason=revoked", { replace: true }); return; }
        setToast(fmt(S.keys.toastRevoked, { name: modal.key.label, time: when() }));
        setModal(null); await load();
      }} />}
      {modal?.kind === "reveal" && <RevealDialog m={modal} onClose={() => { setModal(null); if (modal.thenSignOut) nav("/sign-in?reason=revoked", { replace: true }); }} />}
    </>
  );
}

function CreateDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (k: KeyRow) => void }) {
  const [label, setLabel] = useState("");
  const [scope, setScope] = useState<"ingest" | "read">("ingest");
  const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    const l = label.trim();
    if (!l || l.length > 40) { setErr(S.keys.errLabel); return; }
    setBusy(true);
    try { onCreated(await api<KeyRow>("/keys", { method: "POST", body: { label: l, scope } })); }
    catch (e2) { setErr(S.sys.error.replace("{id}", (e2 as ApiError).requestId ?? "—")); setBusy(false); }
  }
  const opt = (v: "ingest" | "read", help: string) => (
    <label style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "10px 12px", border: `1px solid ${scope === v ? "var(--accent)" : "var(--border-strong)"}`, borderRadius: 6, background: scope === v ? "var(--accent-subtle)" : undefined, cursor: "pointer" }}>
      <input type="radio" name="scope" checked={scope === v} onChange={() => setScope(v)} style={{ marginTop: 3, accentColor: "var(--accent)" }} />
      <span><strong className="t-13">{v}</strong><span className="t-12 muted" style={{ display: "block" }}>{help}</span></span>
    </label>);
  return (
    <Dialog labelledBy="ck-t" onClose={onClose}>
      <form onSubmit={submit} noValidate>
        <div className="dialog-b">
          <h2 id="ck-t" className="t-16" style={{ fontWeight: 600 }}>{S.keys.createTitle}</h2>
          <div className="field"><label className="label" htmlFor="ck-l">{S.keys.label}</label>
            <input id="ck-l" className={`input ${err ? "is-error" : ""}`} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="ci-runner" autoComplete="off" data-autofocus maxLength={60} />
            {err ? <span className="help-error" role="alert"><Icon n="warn" />{err}</span> : <span className="help">{S.keys.labelHelp}</span>}</div>
          <fieldset style={{ border: 0, padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 8 }}>
            <legend className="label" style={{ marginBottom: 6 }}>{S.keys.scope}</legend>{opt("ingest", S.keys.scopeIngest)}{opt("read", S.keys.scopeRead)}
          </fieldset>
        </div>
        <div className="dialog-f"><button type="button" className="btn btn-ghost" onClick={onClose}>{S.keys.cancel}</button><button className="btn btn-primary" disabled={busy}>{S.keys.create}</button></div>
      </form>
    </Dialog>
  );
}

function RotateDialog({ k, onClose, onDone, holdFor }: { k: KeyRow; onClose: () => void; onDone: (nk: KeyRow) => void; holdFor: boolean }) {
  const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  async function go() {
    setBusy(true);
    const release = holdFor ? holdAuthRedirects() : null;   // rotating our own key: the next 401 must not redirect before the key was copied
    try { onDone(await api<KeyRow>(`/keys/${k.id}/rotate`, { method: "POST" })); }
    catch (e) { release?.(); setErr((e as ApiError).code === "key_revoked" ? S.signin.errRevoked : S.sys.error.replace("{id}", (e as ApiError).requestId ?? "—")); setBusy(false); }
  }
  return (
    <Dialog role="alertdialog" labelledBy="rk-t" describedBy="rk-d" onClose={onClose}>
      <div className="dialog-b">
        <h2 id="rk-t" className="t-16" style={{ fontWeight: 600 }}>{fmt(S.keys.rotateTitle, { name: k.label })}</h2>
        <p id="rk-d" className="muted">{S.keys.rotateBody}</p>
        <Banner tone="warn" icon="warn">Any script using <span className="mono">{shown(k)}</span> will start getting 401 errors until you paste the new key.</Banner>
        {err && <span className="help-error" role="alert"><Icon n="warn" />{err}</span>}
      </div>
      <div className="dialog-f"><button className="btn btn-ghost" data-autofocus onClick={onClose}>{S.keys.cancel}</button><button className="btn btn-danger" onClick={go} disabled={busy}>{S.keys.rotateCta}</button></div>
    </Dialog>
  );
}

function RevokeDialog({ k, onClose, onDone }: { k: KeyRow; onClose: () => void; onDone: () => void }) {
  const [typed, setTyped] = useState(""); const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  async function go() {
    setBusy(true);
    try { await api(`/keys/${k.id}/revoke`, { method: "POST" }); onDone(); }
    catch (e) { const a = e as ApiError; setErr(a.code === "last_read_key" ? S.keys.errLast : S.sys.error.replace("{id}", a.requestId ?? "—")); setBusy(false); }
  }
  return (
    <Dialog role="alertdialog" labelledBy="vk-t" describedBy="vk-d" onClose={onClose}>
      <div className="dialog-b">
        <h2 id="vk-t" className="t-16" style={{ fontWeight: 600 }}>{fmt(S.keys.revokeTitle, { name: k.label })}</h2>
        <p id="vk-d" className="muted">{S.keys.revokeBody}</p>
        <div className="field"><label className="label" htmlFor="vk-c">Type <span className="mono">{k.label}</span> to confirm</label><input id="vk-c" className="input mono" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" /></div>
        {err && <span className="help-error" role="alert"><Icon n="warn" />{err}</span>}
      </div>
      <div className="dialog-f"><button className="btn btn-ghost" data-autofocus onClick={onClose}>{S.keys.cancel}</button><button className="btn btn-danger" disabled={typed !== k.label || busy} onClick={go}>{S.keys.revokeCta}</button></div>
    </Dialog>
  );
}

function RevealDialog({ m, onClose }: { m: Extract<Modal, { kind: "reveal" }>; onClose: () => void }) {
  useEffect(() => holdAuthRedirects(), []);   // a new key is on screen: never redirect away from it
  return (
    <Dialog labelledBy="rv-t" onClose={() => { /* Esc / outside click do nothing: the key is shown once */ }} width={560}>
      <div className="dialog-b">
        <h2 id="rv-t" className="t-16" style={{ fontWeight: 600 }}>{m.title}</h2>
        <Banner tone="warn" icon="warn" role="alert"><strong>{S.keys.copyNow}</strong> {S.keys.copyNowRest}</Banner>
        <div style={{ display: "flex", gap: 8 }}>
          <input className="input mono" readOnly aria-label="New key" value={m.secret} style={{ flex: 1, height: 40 }} data-testid="new-key" />
          <CopyButton text={m.secret} className="btn" />
        </div>
        {m.oldNote && <p className="t-13 muted"><span className="chip" style={{ height: 22 }}><Icon n="revoked" />revoked</span> {m.oldNote}</p>}
        {m.thenSignOut && <p className="t-13 muted">This was the key of your current session, so you will be signed out when you continue. Paste the new key to sign in again.</p>}
      </div>
      <div className="dialog-f"><button className="btn btn-primary" data-autofocus onClick={onClose} data-testid="copied">{S.keys.copied}</button></div>
    </Dialog>
  );
}
