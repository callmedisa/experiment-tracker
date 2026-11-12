import { describe, expect, it } from "vitest";
import { RunData } from "../src/lib/runData";
import { MetricEvent } from "../src/lib/types";

const ev = (seq: number, metric: string, step: number, value: number, late = false): MetricEvent =>
  ({ seq, point_id: `${metric}:${step}:${seq}`, metric, step, value, ts: null, received_at: "", is_late: late });

describe("RunData", () => {
  it("ignores a repeated seq and counts distinct events", () => {
    const d = new RunData();
    expect(d.apply(ev(1, "loss", 0, 1), 0)).toBe(true);
    expect(d.apply(ev(1, "loss", 0, 1), 0)).toBe(false);
    expect(d.received).toBe(1);
  });
  it("cursor is the contiguous prefix, and holes are reported as missing", () => {
    const d = new RunData();
    [1, 2, 4, 5].forEach((s) => d.apply(ev(s, "loss", s, s), 0));
    expect(d.cursor).toBe(2);
    expect(d.missing()).toEqual([3]);
    d.apply(ev(3, "loss", 3, 3), 0);
    expect(d.cursor).toBe(5);
    expect(d.missing()).toEqual([]);
  });
  it("chart rule: per metric and step the highest seq wins, regardless of arrival order", () => {
    const d = new RunData();
    d.apply(ev(1, "loss", 5, 1.0), 0);
    d.apply(ev(2, "loss", 5, 2.0), 0);          // same step, later seq: wins
    d.apply(ev(4, "loss", 5, 9.0), 0);          // arrives before seq 3
    d.apply(ev(3, "loss", 5, 3.0), 0);          // older seq arriving late must not overwrite
    expect(d.seriesOrdered("loss")).toEqual([{ step: 5, value: 9.0, seq: 4 }]);
  });
  it("orders by step, keeps metrics apart, and computes card stats", () => {
    const d = new RunData();
    d.apply(ev(1, "loss", 10, 0.5), 0); d.apply(ev(2, "loss", 2, 3.0, true), 0); d.apply(ev(3, "acc", 1, 0.1), 0);
    expect(d.seriesOrdered("loss").map((p) => p.step)).toEqual([2, 10]);
    expect(d.stats("loss")).toEqual({ last: 0.5, step: 10, min: 0.5, max: 3.0, n: 2 });
    expect(d.metricOrder).toEqual(["loss", "acc"]);
    expect(d.lateRecent.map((l) => l.seq)).toEqual([2]);
  });
  it("checksumUpTo only covers seq <= N", () => {
    const d = new RunData();
    for (let i = 1; i <= 5; i++) d.apply(ev(i, "loss", i, i), 0);
    expect(d.checksumUpTo(3).count).toBe(3);
    expect(d.checksumUpTo(3).checksum).not.toBe(d.checksumUpTo(5).checksum);
  });
});
