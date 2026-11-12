/** Keyboard stepping over the points of a chart. Pure functions so they can be unit-tested without a canvas.
 *  xs = sorted x values (steps) of the aligned chart data; the zoom window is [min, max] in x units (null = everything). */

/** Inclusive index range of the xs inside the zoom window, or null when no point is inside. */
export function visibleRange(xs: number[], min: number | null, max: number | null): [number, number] | null {
  if (!xs.length) return null;
  let lo = 0, hi = xs.length - 1;
  if (min != null) while (lo <= hi && xs[lo] < min) lo++;
  if (max != null) while (hi >= lo && xs[hi] > max) hi--;
  return lo <= hi ? [lo, hi] : null;
}

/** Next hover index for a key. With nothing hovered, ← and → both start from the newest visible point (as the design board does), so ← lands on
 *  the one before it and → stays on it. Returns the new index (null = clear), or `undefined` if the key is not a navigation key. */
export function stepIndex(range: [number, number] | null, cur: number | null, key: string): number | null | undefined {
  if (key === "Escape") return null;
  if (key !== "ArrowLeft" && key !== "ArrowRight" && key !== "Home" && key !== "End") return undefined;
  if (!range) return null;
  const [lo, hi] = range;
  if (key === "Home") return lo;
  if (key === "End") return hi;
  const base = cur == null || cur < lo || cur > hi ? hi : cur;
  return Math.max(lo, Math.min(hi, base + (key === "ArrowRight" ? 1 : -1)));
}
