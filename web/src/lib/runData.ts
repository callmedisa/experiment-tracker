import { checksum, PointRow } from "./checksum";
import { MetricEvent } from "./types";

export interface Gap { from: number; to: number; reason: "disconnect" | "jump"; status: "pending" | "filling" | "filled" | "open"; at: number }
export interface SeriesPoint { step: number; value: number; seq: number }

/**
 * Everything the page knows about one run. Events are keyed by seq (so a repeated seq is a no-op).
 * `cursor` = highest seq such that 1..cursor are ALL present: this is what a reconnect resumes from, so a hole is
 * always re-requested instead of being skipped.
 */
export class RunData {
  events = new Map<number, MetricEvent>();
  cursor = 0;
  maxSeq = 0;
  /** chart rule: per metric, per step, the row with the highest seq */
  series = new Map<string, Map<number, SeriesPoint>>();
  metricOrder: string[] = [];
  gaps: Gap[] = [];
  lateRecent: { seq: number; metric: string; step: number; at: number }[] = [];
  version = 0;

  get received(): number { return this.events.size; }

  /** Returns true if the event was new. */
  apply(e: MetricEvent, now: number): boolean {
    if (this.events.has(e.seq)) return false;
    this.events.set(e.seq, e);
    if (e.seq > this.maxSeq) this.maxSeq = e.seq;
    while (this.events.has(this.cursor + 1)) this.cursor++;
    let m = this.series.get(e.metric);
    if (!m) { m = new Map(); this.series.set(e.metric, m); this.metricOrder.push(e.metric); }
    const cur = m.get(e.step);
    if (!cur || e.seq > cur.seq) m.set(e.step, { step: e.step, value: e.value, seq: e.seq });
    if (e.is_late) { this.lateRecent.push({ seq: e.seq, metric: e.metric, step: e.step, at: now }); if (this.lateRecent.length > 200) this.lateRecent.shift(); }
    this.version++;
    return true;
  }

  /** Seqs in 1..maxSeq that are not present (a real jump, not just "behind"). */
  missing(limit = 1000): number[] {
    const out: number[] = [];
    for (let s = this.cursor + 1; s < this.maxSeq && out.length < limit; s++) if (!this.events.has(s)) out.push(s);
    return out;
  }

  checksumUpTo(upToSeq: number): { count: number; checksum: string } {
    const rows: PointRow[] = [];
    for (const e of this.events.values()) if (e.seq <= upToSeq) rows.push(e);
    return { count: rows.length, checksum: checksum(rows) };
  }

  /** Ordered series for the chart, as the chart draws it. */
  seriesOrdered(metric: string): SeriesPoint[] {
    const m = this.series.get(metric);
    return m ? [...m.values()].sort((a, b) => a.step - b.step) : [];
  }

  stats(metric: string) {
    const pts = this.seriesOrdered(metric);
    if (!pts.length) return null;
    let min = Infinity, max = -Infinity;
    for (const p of pts) { if (p.value < min) min = p.value; if (p.value > max) max = p.value; }
    const last = pts[pts.length - 1];
    return { last: last.value, step: last.step, min, max, n: pts.length };
  }

  reset(): void {
    this.events.clear(); this.series.clear(); this.metricOrder = []; this.cursor = 0; this.maxSeq = 0;
    this.lateRecent = []; this.version++;
  }
}
