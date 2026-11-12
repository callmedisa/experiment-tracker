import { ConnState, MetricEvent, Stats } from "./types";

/** The slice of EventSource we use, so tests can substitute a fake. */
export interface ES {
  addEventListener(type: string, cb: (ev: { data: string }) => void): void;
  onerror: ((ev: unknown) => void) | null;
  close(): void;
}
export type ESFactory = (url: string) => ES;

export interface ConnDeps {
  url: (afterSeq: number) => string;
  es: ESFactory;
  now: () => number;
  setTimer: (fn: () => void, ms: number) => unknown;
  clearTimer: (h: unknown) => void;
  random: () => number;
  isOnline: () => boolean;
  /** called for every metric event, in order */
  onMetric: (e: MetricEvent, replayed: boolean) => void;
  cursor: () => number;
  onStats?: (s: Stats) => void;
  onDuplicate?: (d: { batch_id: string; first_seq: number | null; last_seq: number | null; duplicates: number }) => void;
  onResync: (lastSeq: number) => void;
  onClosed?: (c: { status: string; finished_at: string | null }) => void;
  onCaughtUp?: (replayed: number) => void;
  onAuthCheck?: () => void;
  onChange?: () => void;
}

export const DEAD_AFTER_MS = 10_000;
export const BACKOFF_MIN_MS = 1000;
export const BACKOFF_MAX_MS = 30_000;

export function backoffMs(attempt: number, random: number): number {
  const base = Math.min(BACKOFF_MAX_MS, BACKOFF_MIN_MS * 2 ** Math.max(0, attempt - 1));
  return Math.round(base * (0.8 + 0.4 * random)); // +-20 % jitter
}

/**
 * Connection layer around EventSource. States: connecting (first load only) -> live; on any loss -> reconnecting
 * (or offline) -> catching_up (if the first stats after reconnect say events are missing) -> live; resync on request.
 * EventSource's own auto-reconnect is NOT used: on error we close it and reconnect ourselves with ?after_seq=<cursor>.
 */
export class RunConnection {
  state: ConnState = "connecting";
  attempt = 0;
  retryAt = 0;
  replayDone = 0;
  replayTotal = 0;
  lostAt = 0;
  closedByServer = false;
  lastStatsAt = -1; // -1 = no stats received yet
  history: { state: ConnState; t: number }[] = [];
  private es: ES | null = null;
  private timer: unknown = null;
  private watchdog: unknown = null;
  private everLive = false;
  private gotStats = false;
  private target = 0;
  private stopped = false;
  private reconnecting = false;

  constructor(private d: ConnDeps) { this.record(); }

  private record() { this.history.push({ state: this.state, t: this.d.now() }); }
  private set(s: ConnState) { if (s !== this.state) { this.state = s; this.record(); this.d.onChange?.(); } }

  start(): void {
    this.stopped = false;
    this.open();
    this.armWatchdog();
  }

  stop(): void {
    this.stopped = true;
    this.closeEs();
    if (this.timer) this.d.clearTimer(this.timer);
    if (this.watchdog) this.d.clearTimer(this.watchdog);
    this.timer = this.watchdog = null;
  }

  /** after a resync snapshot has been loaded */
  resumeAfterResync(): void {
    this.attempt = 0;
    this.reconnecting = false;
    this.gotStats = false;
    this.set("connecting");
    this.open();
  }

  retryNow(): void {
    if (this.timer) this.d.clearTimer(this.timer);
    this.timer = null;
    if (!this.stopped && this.state !== "live" && this.state !== "catching_up") { this.open(); }
  }

  goOffline(): void {
    if (this.closedByServer) return;
    this.closeEs();
    if (this.timer) this.d.clearTimer(this.timer);
    this.timer = null;
    if (!this.lostAt) this.lostAt = this.d.now();
    this.reconnecting = true;
    this.set("offline");
  }

  goOnline(): void {
    if (this.state === "offline") { this.attempt = 0; this.open(); }
  }

  private closeEs() {
    if (this.es) { this.es.onerror = null; this.es.close(); this.es = null; }
  }

  private open(): void {
    if (this.stopped || this.closedByServer) return;
    this.closeEs();
    this.gotStats = false;
    if (this.reconnecting && this.state !== "reconnecting" && this.state !== "offline") this.set("reconnecting");
    const es = this.d.es(this.d.url(this.d.cursor()));
    this.es = es;
    es.addEventListener("stats", (ev) => this.onStats(JSON.parse(ev.data)));
    es.addEventListener("metric", (ev) => this.onMetric(JSON.parse(ev.data)));
    es.addEventListener("duplicate_batch", (ev) => this.d.onDuplicate?.(JSON.parse(ev.data)));
    es.addEventListener("resync", (ev) => this.onResync(JSON.parse(ev.data).last_seq));
    es.addEventListener("closed", (ev) => this.onClosed(JSON.parse(ev.data)));
    es.onerror = () => this.onError();
  }

  private onStats(s: Stats): void {
    this.lastStatsAt = this.d.now();
    this.d.onStats?.(s);
    if (!this.gotStats) {
      this.gotStats = true;
      this.attempt = 0;
      const missing = s.last_seq - this.d.cursor();
      if (!this.reconnecting || !this.everLive) {
        // first connect of this page: never "catching up"; the snapshot already holds everything up to the cursor
        this.everLive = true; this.reconnecting = false; this.set("live");
      } else if (missing > 0) {
        this.target = s.last_seq; this.replayTotal = missing; this.replayDone = 0;
        this.set("catching_up");
      } else {
        this.reconnecting = false; this.lostAt = 0; this.set("live");
      }
    }
  }

  private onMetric(e: MetricEvent): void {
    const catching = this.state === "catching_up";
    this.d.onMetric(e, catching);
    if (catching) {
      this.replayDone++;
      if (this.d.cursor() >= this.target) {
        const n = this.replayDone;
        this.reconnecting = false; this.lostAt = 0;
        this.set("live");
        this.d.onCaughtUp?.(n);
        return;
      }
    }
  }

  private onResync(lastSeq: number): void {
    this.closeEs();
    this.set("resync");
    this.d.onResync(lastSeq);
  }

  private onClosed(c: { status: string; finished_at: string | null }): void {
    this.closedByServer = true;
    this.closeEs();
    this.reconnecting = false;
    if (this.state !== "live") this.set("live");
    this.d.onClosed?.(c);
    this.d.onChange?.();
  }

  private onError(): void {
    if (this.stopped || this.closedByServer) return;
    this.lose();
    this.d.onAuthCheck?.(); // EventSource does not expose the status; the app checks if the session is still valid
  }

  private lose(): void {
    this.closeEs();
    if (!this.lostAt) this.lostAt = this.d.now();
    this.reconnecting = true;
    if (!this.d.isOnline()) { this.set("offline"); return; }
    this.attempt++;
    const ms = backoffMs(this.attempt, this.d.random());
    this.retryAt = this.d.now() + ms;
    this.set("reconnecting");
    this.d.onChange?.();   // attempt / retryAt changed even if the state did not
    if (this.timer) this.d.clearTimer(this.timer);
    this.timer = this.d.setTimer(() => { this.timer = null; this.open(); }, ms);
  }

  private armWatchdog(): void {
    this.watchdog = this.d.setTimer(() => {
      if (!this.stopped && !this.closedByServer && (this.state === "live" || this.state === "catching_up")
          && this.lastStatsAt >= 0 && this.d.now() - this.lastStatsAt > DEAD_AFTER_MS) {
        this.lose(); // two missed heartbeats: treat as dead even if the browser did not notice
      }
      this.armWatchdog();
    }, 1000);
  }
}
