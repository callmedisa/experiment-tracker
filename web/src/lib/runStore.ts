import { api, ApiError, RunInfo } from "../api";
import { RunConnection } from "./connection";
import { Gap, RunData } from "./runData";
import { ConnState, MetricEvent, Stats } from "./types";
import { S, fmt } from "../strings";

export interface Toast { id: number; kind: "duplicate" | "late" | "caught"; text: string }
export type VerifyState = { status: "idle" | "busy" | "match" | "mismatch"; checksum?: string; missing?: number[]; pageCount?: number; serverCount?: number; note?: string };

export const DEFICIT_WARN_MS = 6000; // stats arrive every 5 s; a shortfall shorter than that is normal, not a warning

/** Everything the run page shows. Plain TypeScript (no React) so it can be driven from tests and the test hook. */
export class RunStore {
  data = new RunData();
  conn: RunConnection;
  run: RunInfo | null = null;
  stats: Stats | null = null;
  skew = 0; // server_time - client time, from the latest stats
  loading = true;
  error: ApiError | null = null;
  verify: VerifyState = { status: "idle" };
  toasts: Toast[] = [];
  paused = false; pausedAtSeq = 0;
  deficitSince = 0;
  closed: { status: string; finished_at: string | null } | null = null;
  lostAtText = 0;
  v = 0;
  private listeners = new Set<() => void>();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private toastId = 0;
  private currentGap: Gap | null = null;
  private filling = false;
  private disposed = false;

  constructor(public runId: string, private opts: { onAuthCheck: () => void }) {
    this.conn = new RunConnection({
      url: (after) => `/api/runs/${encodeURIComponent(runId)}/stream?after_seq=${after}`,
      es: (url) => new EventSource(url) as any,
      now: () => Date.now(),
      setTimer: (fn, ms) => setTimeout(fn, ms),
      clearTimer: (h) => clearTimeout(h as any),
      random: () => Math.random(),
      isOnline: () => navigator.onLine,
      cursor: () => this.data.cursor,
      onMetric: (e, replayed) => this.applyEvent(e, replayed),
      onStats: (s) => this.onStats(s),
      onDuplicate: (d) => this.toast("duplicate", d.first_seq != null && d.last_seq != null
        ? fmt(S.run.toastDuplicate, { id: d.batch_id, from: d.first_seq, to: d.last_seq }) : fmt(S.run.toastDuplicateNoRange, { id: d.batch_id })),
      onResync: () => { void this.reloadSnapshot(true); },
      onClosed: (c) => { this.closed = c; this.emit(); },
      onCaughtUp: (n) => this.toast("caught", fmt(S.run.toastCaught, { n })),
      onAuthCheck: opts.onAuthCheck,
      onChange: () => this.onConnChange(),
    });
    window.addEventListener("online", this.onOnline);
    window.addEventListener("offline", this.onOffline);
  }

  private onOnline = () => this.conn.goOnline();
  private onOffline = () => this.conn.goOffline();

  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };
  getVersion = () => this.v;

  emit(immediate = false): void {
    if (this.disposed) return;
    const fire = () => { this.timer = null; this.v++; this.listeners.forEach((l) => l()); };
    if (immediate) { if (this.timer) clearTimeout(this.timer); fire(); return; }
    if (!this.timer) this.timer = setTimeout(fire, 100); // at most 10 renders/s however fast events arrive
  }

  async init(): Promise<void> {
    try {
      await this.reloadSnapshot(false);
    } catch (e) {
      this.error = e as ApiError; this.loading = false; this.emit(true);
    }
  }

  /** Loads run info + every event from seq 1 (replaces what the page holds), then (re)opens the stream. */
  async reloadSnapshot(fromResync: boolean): Promise<void> {
    this.loading = !fromResync;
    this.data.reset();
    this.verify = { status: "idle" };
    this.emit(true);
    this.run = await api<RunInfo>(`/runs/${encodeURIComponent(this.runId)}`);
    let after = 0;
    for (;;) {
      const page = await api<{ events: MetricEvent[]; last_seq: number }>(`/runs/${encodeURIComponent(this.runId)}/events?after_seq=${after}&limit=5000`);
      if (this.disposed) return;
      for (const e of page.events) this.data.apply(e, Date.now());
      if (page.events.length < 5000) break;
      after = page.events[page.events.length - 1].seq;
    }
    if (this.disposed) return;
    this.loading = false;
    this.emit(true);
    if (fromResync) this.conn.resumeAfterResync(); else this.conn.start();
  }

  dispose(): void {
    this.disposed = true;
    this.conn.stop();
    window.removeEventListener("online", this.onOnline);
    window.removeEventListener("offline", this.onOffline);
    if (this.timer) clearTimeout(this.timer);
    this.listeners.clear();
  }

  private applyEvent(e: MetricEvent, replayed: boolean): void {
    this.trackDeficit();
    const isNew = this.data.apply(replayed ? { ...e, replayed: true } : e, Date.now());
    if (isNew && this.data.maxSeq > this.data.cursor + 1) void this.fillJump();
    this.emit();
  }

  private trackDeficit(): void {
    const behind = this.data.received < this.stored && this.conn.state === "live";
    if (behind) { if (!this.deficitSince) this.deficitSince = Date.now(); } else this.deficitSince = 0;
  }

  private onStats(s: Stats): void {
    this.stats = s;
    this.trackDeficit();
    this.skew = new Date(s.server_time).getTime() - Date.now();
    if (this.run) { this.run = { ...this.run, status: s.status, duplicates_ignored: s.duplicates_ignored, last_event_at: s.last_event_at }; }
    this.emit();
  }

  /** stored = what the server says it has; a page can never have received more than is stored. */
  get stored(): number { return Math.max(this.stats?.count ?? this.run?.count ?? 0, this.data.maxSeq); }

  private onConnChange(): void {
    const st = this.conn.state;
    if (st === "catching_up" && !this.currentGap) {
      this.currentGap = { from: this.data.cursor + 1, to: this.conn.replayTotal + this.data.cursor, reason: "disconnect", status: "filling", at: Date.now() };
      this.data.gaps.push(this.currentGap);
    }
    if ((st === "live") && this.currentGap) { this.currentGap.status = "filled"; this.currentGap = null; }
    this.emit(true);
  }

  /** A real jump in seq (should not happen: the server's seq is contiguous). Ask the server for the hole. */
  private async fillJump(): Promise<void> {
    if (this.filling) return;
    this.filling = true;
    const gap: Gap = { from: this.data.cursor + 1, to: this.data.maxSeq - 1, reason: "jump", status: "filling", at: Date.now() };
    this.data.gaps.push(gap);
    try {
      const page = await api<{ events: MetricEvent[] }>(`/runs/${encodeURIComponent(this.runId)}/events?after_seq=${this.data.cursor}&limit=5000`);
      for (const e of page.events) this.data.apply({ ...e, replayed: true }, Date.now());
      gap.status = this.data.missing().length ? "open" : "filled";
    } catch { gap.status = "open"; }
    this.filling = false;
    this.emit(true);
  }

  toast(kind: Toast["kind"], text: string): void {
    const id = ++this.toastId;
    this.toasts = [...this.toasts.slice(-2), { id, kind, text }];
    this.emit(true);
    setTimeout(() => { this.toasts = this.toasts.filter((t) => t.id !== id); this.emit(true); }, 5000);
  }

  pause(): void { this.paused = true; this.pausedAtSeq = this.data.maxSeq; this.emit(true); }
  resume(): void { this.paused = false; this.emit(true); }

  /**
   * Verify (accepted rule): ask the server for count/checksum, wait until this page has applied everything up to the
   * server's last_seq, compare over seq <= that last_seq. Only a hole that persists counts as a mismatch.
   */
  async doVerify(): Promise<void> {
    this.verify = { status: "busy" }; this.emit(true);
    try {
      const id = encodeURIComponent(this.runId);
      const srv = await api<{ count: number; checksum: string; last_seq: number }>(`/runs/${id}/integrity`);
      const deadline = Date.now() + 10_000;
      while (this.data.cursor < srv.last_seq && Date.now() < deadline) await new Promise((r) => setTimeout(r, 200));
      const page = this.data.checksumUpTo(srv.last_seq);
      if (page.count === srv.count && page.checksum === srv.checksum) {
        this.verify = { status: "match", checksum: srv.checksum, pageCount: page.count, serverCount: srv.count };
      } else {
        const seqs = await api<{ ranges: [number, number][] }>(`/runs/${id}/seqs`);
        const missing: number[] = [];
        for (const [a, b] of seqs.ranges) for (let s = a; s <= b && missing.length < 500; s++) if (!this.data.events.has(s) && s <= srv.last_seq) missing.push(s);
        this.verify = { status: "mismatch", checksum: srv.checksum, missing, pageCount: page.count, serverCount: srv.count };
      }
    } catch (e) {
      this.verify = { status: "idle", note: e instanceof ApiError && e.code === "network" ? S.signin.errNetwork : "Verify failed" };
    }
    this.emit(true);
  }

  testHook() {
    const s = this;
    return {
      get lastSeq() { return s.data.cursor; },
      get count() { return s.data.received; },
      get checksum() { return s.data.checksumUpTo(Infinity).checksum; },
      get state(): ConnState { return s.conn.state; },
      get missing() { return s.data.missing(); },
      get history() { return s.conn.history; },
      get series() {
        const out: Record<string, { step: number; value: number; seq: number }[]> = {};
        for (const m of s.data.metricOrder) out[m] = s.data.seriesOrdered(m);
        return out;
      },
      get closedByServer() { return s.conn.closedByServer; },
    };
  }
}
