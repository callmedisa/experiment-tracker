import { ReactNode, useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { api } from "../api";
import { toggleTheme, useTheme } from "../lib/theme";
import { Dialog } from "./Dialog";
import { Icon, IconName } from "./ui";
import { Trk } from "./Trk";

export function Logo({ text = true }: { text?: boolean }) {
  return <Link to="/" className="logo" style={{ textDecoration: "none" }}><span className="logo-mark"><Icon n="logo" /></span>{text && <span className="hide-mobile">Tracker</span>}</Link>;
}

const NAV: { to: string; label: string; icon: IconName }[] = [{ to: "/runs", label: "Runs", icon: "runs" }, { to: "/keys", label: "Keys", icon: "key" }];

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  // NavLink puts aria-current="page" on the active anchor, which is what `.nav-item[aria-current="page"]` in tracker.css styles.
  return <>{NAV.map((n) => <NavLink key={n.to} to={n.to} className="nav-item" onClick={onNavigate}><Icon n={n.icon} />{n.label}</NavLink>)}</>;
}

/** Top bar + side nav (a drawer below 760 px) + theme toggle. */
export function AppShell({ project, children }: { project: string; children: ReactNode }) {
  const nav = useNavigate();
  const mode = useTheme();
  const [drawer, setDrawer] = useState(false);
  const signOut = async () => { try { await api("/session", { method: "DELETE" }); } catch { /* already gone */ } nav("/"); };
  return (
    <Trk style={{ minHeight: "100%" }}>
      <a href="#main" className="btn btn-sm" style={{ position: "absolute", left: -9999 }} onFocus={(e) => (e.currentTarget.style.left = "8px")} onBlur={(e) => (e.currentTarget.style.left = "-9999px")}>Skip to run data</a>
      <header className="topbar">
        <button className="btn btn-ghost icon-btn only-mobile" aria-label="Open navigation" aria-expanded={drawer} style={{ width: 40, height: 40 }} onClick={() => setDrawer(true)}><Icon n="menu" /></button>
        <Logo />
        <span className="crumb-sep" aria-hidden="true">/</span>
        <span style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} data-testid="project-name">{project}</span>
        <span style={{ flex: 1 }} />
        <button className="btn btn-ghost icon-btn" aria-label="Switch theme" aria-pressed={mode === "dark"} data-testid="theme-toggle" onClick={toggleTheme}><Icon n={mode === "dark" ? "sun" : "moon"} /></button>
        <button className="btn btn-ghost btn-sm" onClick={signOut} aria-label="Sign out"><Icon n="out" /><span className="hide-mobile">Sign out</span></button>
      </header>
      <div className="shell-row" style={{ minHeight: "calc(100vh - 52px)" }}>
        <nav className="sidenav" aria-label="Main"><NavLinks /></nav>
        <main id="main" className="main" style={{ gap: 12 }}>{children}</main>
      </div>
      {drawer && <Drawer onClose={() => setDrawer(false)} />}
    </Trk>
  );
}

function Drawer({ onClose }: { onClose: () => void }) {
  // Rendered through the same Dialog machinery (focus trap, Esc, portal) as the other overlays.
  return (
    <Dialog labelledBy="nav-t" onClose={onClose} top={0} width={280}>
      <div className="dialog-b" style={{ gap: 4, padding: 12 }}>
        <h2 id="nav-t" className="t-13" style={{ fontWeight: 600, padding: "4px 10px" }}>Navigation</h2>
        <nav aria-label="Main (drawer)" style={{ display: "flex", flexDirection: "column", gap: 2 }}><NavLinks onNavigate={onClose} /></nav>
      </div>
      <div className="dialog-f"><button className="btn btn-ghost" data-autofocus onClick={onClose}>Close</button></div>
    </Dialog>
  );
}
