import { describe, expect, it } from "vitest";
import { stepIndex, visibleRange } from "../src/lib/chartNav";

const xs = [0, 1, 2, 5, 6, 9];
describe("visibleRange", () => {
  it("whole range when not zoomed, none for empty data", () => { expect(visibleRange(xs, null, null)).toEqual([0, 5]); expect(visibleRange([], null, null)).toBeNull(); });
  it("inclusive window on x values, not on indices", () => { expect(visibleRange(xs, 1.5, 6)).toEqual([2, 4]); expect(visibleRange(xs, 3, 4)).toBeNull(); });
});
describe("stepIndex", () => {
  const r: [number, number] = [2, 4];
  it("starts at the newest visible point; left goes one back, right stays", () => { expect(stepIndex(r, null, "ArrowLeft")).toBe(3); expect(stepIndex(r, null, "ArrowRight")).toBe(4); });
  it("steps and clamps inside the zoom window", () => { expect(stepIndex(r, 3, "ArrowRight")).toBe(4); expect(stepIndex(r, 4, "ArrowRight")).toBe(4); expect(stepIndex(r, 2, "ArrowLeft")).toBe(2); });
  it("Home/End jump to the window edges; a hover outside the window restarts from the newest", () => { expect(stepIndex(r, 3, "Home")).toBe(2); expect(stepIndex(r, 3, "End")).toBe(4); expect(stepIndex(r, 0, "ArrowLeft")).toBe(3); });
  it("Esc clears; other keys are left alone; empty window gives null", () => { expect(stepIndex(r, 3, "Escape")).toBeNull(); expect(stepIndex(r, 3, "a")).toBeUndefined(); expect(stepIndex(null, 1, "ArrowLeft")).toBeNull(); });
});
