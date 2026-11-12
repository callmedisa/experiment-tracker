import { Component, ReactNode } from "react";
import { S } from "../strings";
import { Trk } from "./Trk";

/** Last resort: a render crash shows the 500 page instead of a blank screen. */
export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error) { console.error("render error:", error.message); }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <Trk style={{ minHeight: "100%", display: "grid", placeItems: "center", padding: 32 }}>
        <div role="alert" style={{ maxWidth: 480, display: "flex", flexDirection: "column", gap: 12 }} data-testid="error-page">
          <h1 className="t-24">Server error</h1>
          <p className="muted">{S.sys.error.replace("{id}", "—").replace(" Request ID —", "")}</p>
          <pre className="code" style={{ whiteSpace: "pre-wrap" }}>{this.state.error.message}</pre>
          <button className="btn" onClick={() => location.assign("/")}>Reload</button>
        </div>
      </Trk>
    );
  }
}
