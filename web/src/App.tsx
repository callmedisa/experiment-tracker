import { ReactNode, useEffect, useState } from "react";
import { BrowserRouter, Route, Routes, useLocation, useNavigate, Link } from "react-router-dom";
import { api, ApiError, onAuthLost, SessionInfo } from "./api";
import { Trk } from "./components/Trk";
import { AppShell } from "./components/Shell";
import KeyReveal from "./pages/KeyReveal";
import Keys from "./pages/Keys";
import Landing from "./pages/Landing";
import RunPage from "./pages/RunPage";
import RunsList from "./pages/RunsList";
import { S } from "./strings";

function Authed({ children }: { children: ReactNode }) {
  const nav = useNavigate();
  const loc = useLocation();
  const [s, setS] = useState<SessionInfo | null>(null);
  const [err, setErr] = useState<ApiError | null>(null);
  useEffect(() => {
    onAuthLost((reason) => nav(`/sign-in?reason=${reason}`, { replace: true }));
    api<SessionInfo>("/session").then(setS).catch((e: ApiError) => { if (e.status !== 401) setErr(e); else nav("/", { replace: true }); });
    return () => onAuthLost(null);
  }, [nav]);
  if (err) return <Trk style={{ padding: 32 }}><p role="alert">{err.code === "network" ? S.signin.errNetwork : S.sys.error.replace("{id}", err.requestId ?? "—")}</p></Trk>;
  if (!s) return <Trk style={{ minHeight: "100%" }}><span aria-busy="true" data-path={loc.pathname} /></Trk>;
  return <AppShell project={s.project.name}>{children}</AppShell>;
}

function NotFound() {
  return <Trk style={{ minHeight: "100%", display: "grid", placeItems: "center", padding: 32 }}>
    <div style={{ textAlign: "center", maxWidth: 440, display: "flex", flexDirection: "column", gap: 12 }} data-testid="not-found-page">
      <h1 className="t-32">404</h1><p className="muted">{S.sys.notFound}</p><Link to="/">Go to start</Link>
    </div></Trk>;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/sign-in" element={<Landing />} />
        <Route path="/keys-created" element={<KeyReveal />} />
        <Route path="/runs" element={<Authed><RunsList /></Authed>} />
        <Route path="/keys" element={<Authed><Keys /></Authed>} />
        <Route path="/runs/:id" element={<Authed><RunPage /></Authed>} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </BrowserRouter>
  );
}
