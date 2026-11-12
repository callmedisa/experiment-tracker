import { describe, expect, it } from "vitest";
import { BACKOFF_MAX_MS, backoffMs, ConnDeps, DEAD_AFTER_MS, ES, RunConnection } from "../src/lib/connection";
import { MetricEvent } from "../src/lib/types";

class FakeES implements ES {
  listeners: Record<string, ((ev: { data: string }) => void)[]> = {};
  onerror: ((ev: unknown) => void) | null = null;
  closed = false;
  constructor(public url: string) {}
  addEventListener(t: string, cb: (ev: { data: string }) => void) { (this.listeners[t] ||= []).push(cb); }
  close() { this.closed = true; }
  emit(t: string, data: unknown) { if (!this.closed) (this.listeners[t] || []).forEach((f) => f({ data: JSON.stringify(data) })); }
  fail() { this.onerror?.({}); }
}

function rig(opts: { online?: boolean } = {}) {
  let t = 0, online = opts.online ?? true;
  const timers: { at: number; fn: () => void; id: number }[] = [];
  let nid = 0;
  const sources: FakeES[] = [];
  const received: number[] = [];
  let cursor = 0;
  const log = { resync: [] as number[], caught: [] as number[], dups: 0, closed: 0, auth: 0 };
  const deps: ConnDeps = {
    url: (a) => `/stream?after_seq=${a}`,
    es: (u) => { const e = new FakeES(u); sources.push(e); return e; },
    now: () => t,
    setTimer: (fn, ms) => { const x = { at: t + ms, fn, id: ++nid }; timers.push(x); return x.id; },
    clearTimer: (h) => { const i = timers.findIndex((x) => x.id === h); if (i >= 0) timers.splice(i, 1); },
    random: () => 0.5,
    isOnline: () => online,
    onMetric: (e) => { received.push(e.seq); if (e.seq === cursor + 1) cursor = e.seq; },
    cursor: () => cursor,
    onResync: (s) => log.resync.push(s),
    onCaughtUp: (n) => log.caught.push(n),
    onDuplicate: () => log.dups++,
    onClosed: () => log.closed++,
    onAuthCheck: () => log.auth++,
  };
  const c = new RunConnection(deps);
  const advance = (ms: number) => {
    const end = t + ms;
    for (;;) {
      timers.sort((a, b) => a.at - b.at);
      const n = timers[0];
      if (!n || n.at > end) break;
      timers.shift(); t = n.at; n.fn();
    }
    t = end;
  };
  const metric = (seq: number): MetricEvent => ({ seq, point_id: `p${seq}`, metric: "loss", step: seq, value: seq, ts: null, received_at: "", is_late: false });
  const stats = (last: number) => ({ last_seq: last, count: last, duplicates_ignored: 0, status: "running", last_event_at: null, server_time: "" });
  return { c, sources, received, log, advance, setOnline: (v: boolean) => (online = v), metric, stats, cur: () => cursor, now: () => t };
}

describe("RunConnection", () => {
  it("first connect goes straight to live, even if events exist (the snapshot already has them)", () => {
    const r = rig();
    r.c.start();
    expect(r.c.state).toBe("connecting");
    r.sources[0].emit("stats", r.stats(50));
    expect(r.c.state).toBe("live");
    expect(r.c.history.map((h) => h.state)).toEqual(["connecting", "live"]);
  });

  it("live -> reconnecting -> catching_up -> live, resuming from the cursor", () => {
    const r = rig();
    r.c.start();
    r.sources[0].emit("stats", r.stats(0));
    [1, 2, 3].forEach((s) => r.sources[0].emit("metric", r.metric(s)));
    r.sources[0].fail();
    expect(r.c.state).toBe("reconnecting");
    expect(r.log.auth).toBe(1);
    r.advance(1000);                                    // backoff (1 s, jitter factor 1.0 at random 0.5)
    expect(r.sources).toHaveLength(2);
    expect(r.sources[1].url).toBe("/stream?after_seq=3");
    r.sources[1].emit("stats", r.stats(8));              // 5 events were missed
    expect(r.c.state).toBe("catching_up");
    expect([r.c.replayDone, r.c.replayTotal]).toEqual([0, 5]);
    [4, 5, 6, 7].forEach((s) => r.sources[1].emit("metric", r.metric(s)));
    expect(r.c.state).toBe("catching_up");
    expect(r.c.replayDone).toBe(4);
    r.sources[1].emit("metric", r.metric(8));
    expect(r.c.state).toBe("live");
    expect(r.log.caught).toEqual([5]);
    expect(r.received).toEqual([1, 2, 3, 4, 5, 6, 7, 8]);
    expect(r.c.history.map((h) => h.state)).toEqual(["connecting", "live", "reconnecting", "catching_up", "live"]);
  });

  it("reconnect with nothing missed goes reconnecting -> live without catching_up", () => {
    const r = rig();
    r.c.start();
    r.sources[0].emit("stats", r.stats(0));
    r.sources[0].fail();
    r.advance(1000);
    r.sources[1].emit("stats", r.stats(0));
    expect(r.c.history.map((h) => h.state)).toEqual(["connecting", "live", "reconnecting", "live"]);
  });

  it("backs off 1,2,4,... up to 30 s with jitter and resets after a successful stats", () => {
    expect([1, 2, 3, 4, 5, 6, 7, 20].map((n) => backoffMs(n, 0.5))).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000]);
    expect(backoffMs(3, 0)).toBe(3200);
    expect(backoffMs(3, 1)).toBe(4800);
    expect(backoffMs(99, 1)).toBeLessThanOrEqual(BACKOFF_MAX_MS * 1.2);
    const r = rig();
    r.c.start();
    r.sources[0].emit("stats", r.stats(0));
    r.sources[0].fail(); r.advance(1000);
    r.sources[1].fail(); expect(r.c.attempt).toBe(2); r.advance(2000);
    expect(r.sources).toHaveLength(3);
    r.sources[2].emit("stats", r.stats(0));
    expect(r.c.attempt).toBe(0);
  });

  it("two missed heartbeats (> 10 s without stats) are treated as a dead connection", () => {
    const r = rig();
    r.c.start();
    r.sources[0].emit("stats", r.stats(0));
    r.advance(DEAD_AFTER_MS - 500);
    expect(r.c.state).toBe("live");
    r.advance(2000);
    expect(r.c.state).toBe("reconnecting");
    expect(r.sources[0].closed).toBe(true);
  });

  it("heartbeats keep it alive", () => {
    const r = rig();
    r.c.start();
    for (let i = 0; i < 6; i++) { r.sources[0].emit("stats", r.stats(0)); r.advance(5000); }
    expect(r.c.state).toBe("live");
  });

  it("offline: no retry timer while offline, reconnect at once when online again", () => {
    const r = rig();
    r.c.start();
    r.sources[0].emit("stats", r.stats(0));
    r.setOnline(false);
    r.c.goOffline();
    expect(r.c.state).toBe("offline");
    r.advance(60_000);
    expect(r.sources).toHaveLength(1);
    r.setOnline(true);
    r.c.goOnline();
    expect(r.sources).toHaveLength(2);
    r.sources[1].emit("stats", r.stats(4));
    expect(r.c.state).toBe("catching_up");
  });

  it("resync: closes the stream, reports once, resumes only when told", () => {
    const r = rig();
    r.c.start();
    r.sources[0].emit("stats", r.stats(0));
    r.sources[0].emit("resync", { last_seq: 20000 });
    expect(r.c.state).toBe("resync");
    expect(r.log.resync).toEqual([20000]);
    expect(r.sources[0].closed).toBe(true);
    r.advance(60_000);
    expect(r.sources).toHaveLength(1);
    r.c.resumeAfterResync();
    expect(r.sources).toHaveLength(2);
    r.sources[1].emit("stats", r.stats(20000));
    expect(r.c.state).toBe("live");
  });

  it("closed by the server: stays closed, no reconnect even if the socket then errors", () => {
    const r = rig();
    r.c.start();
    r.sources[0].emit("stats", r.stats(2));
    r.sources[0].emit("closed", { status: "finished", finished_at: "x" });
    r.sources[0].fail();
    r.advance(120_000);
    expect(r.log.closed).toBe(1);
    expect(r.sources).toHaveLength(1);
    expect(r.c.closedByServer).toBe(true);
  });

  it("never applies a metric twice if the server re-sends (cursor-based: the caller dedupes by seq)", () => {
    const r = rig();
    r.c.start();
    r.sources[0].emit("stats", r.stats(0));
    r.sources[0].emit("metric", r.metric(1));
    r.sources[0].emit("metric", r.metric(1));
    expect(r.received).toEqual([1, 1]); // delivered twice to the callback; RunData.apply() ignores the second (tested there)
  });
});
