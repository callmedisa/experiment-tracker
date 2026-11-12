import { useEffect, useMemo, useRef, useState } from "react";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { RunData } from "../lib/runData";
import { useTheme } from "../lib/theme";
import { stepIndex, visibleRange } from "../lib/chartNav";

export const DASHES: number[][] = [[], [8, 5], [1.5, 4], [10, 4, 2, 4]];
export const COLORS = ["--chart-1", "--chart-2", "--chart-3", "--chart-4"];
const css = (el: Element, v: string) => getComputedStyle(el).getPropertyValue(v).trim();

/** uPlot chart. x = step; each metric is drawn by the chart rule (highest seq per step). Late points of the last 5 s get a warn ring. */
export function MetricChart({ data, metrics, hidden, version, label }: { data: RunData; metrics: string[]; hidden: Set<string>; version: number; label: string }) {
  const host = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const theme = useTheme();
  const dataRef = useRef(data);
  dataRef.current = data;
  const zoomRef = useRef<{ min: number; max: number } | null>(null);   // x window set by drag; survives data updates and rebuilds
  const [zoomed, setZoomed] = useState(false);
  const [hoverStep, setHoverStep] = useState<number | null>(null);    // the hovered STEP (an index would shift when a late point lands earlier)
  const hoverRef = useRef<number | null>(null);                         // index into the current aligned x array, for the draw hook

  const aligned = useMemo(() => {
    const visible = metrics.filter((m) => !hidden.has(m));
    const steps = new Set<number>();
    const per = visible.map((m) => data.seriesOrdered(m));
    per.forEach((pts) => pts.forEach((p) => steps.add(p.step)));
    const xs = [...steps].sort((a, b) => a - b);
    const idx = new Map(xs.map((s, i) => [s, i]));
    const ys = per.map((pts) => { const a: (number | null)[] = new Array(xs.length).fill(null); pts.forEach((p) => (a[idx.get(p.step)!] = p.value)); return a; });
    return { visible, data: [xs, ...ys] as uPlot.AlignedData };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [version, metrics.join("|"), [...hidden].join("|")]);

  useEffect(() => {
    const el = host.current;
    if (!el) return;
    const root = el.closest(".trk") ?? document.body;
    const series: uPlot.Series[] = [{}, ...aligned.visible.map((m) => {
      const slot = metrics.indexOf(m) % 4;
      return { label: m, stroke: css(root, COLORS[slot]), width: 2, dash: DASHES[slot].length ? DASHES[slot] : undefined, spanGaps: true, points: { show: false } } as uPlot.Series;
    })];
    const axis = css(root, "--chart-axis"), grid = css(root, "--chart-grid");
    const opts: uPlot.Options = {
      width: Math.max(300, el.clientWidth), height: 300, series, legend: { show: false }, cursor: { drag: { x: true, y: false, setScale: false } },
      scales: { x: { time: false, range: (_u, min, max) => (zoomRef.current ? [zoomRef.current.min, zoomRef.current.max] : [min, max]) } },
      axes: [
        { stroke: axis, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid }, font: "11px JetBrains Mono, monospace" },
        { stroke: axis, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid }, font: "11px JetBrains Mono, monospace", size: 56 },
      ],
      hooks: {
        setSelect: [(u) => {
          if (u.select.width < 8) return;                                  // a click, not a drag
          const min = u.posToVal(u.select.left, "x"), max = u.posToVal(u.select.left + u.select.width, "x");
          u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
          if (!(max > min)) return;
          zoomRef.current = { min, max };
          setZoomed(true);
          u.setScale("x", { min, max });
        }],
        draw: [(u) => {
          el.dataset.xmin = String(u.scales.x.min); el.dataset.xmax = String(u.scales.x.max);   // the visible x window, readable by tests
          const hi = hoverRef.current, xs = u.data[0] as number[];
          if (hi != null && hi < xs.length) {                              // keyboard / hover marker: a line and one dot per visible series
            const x = u.valToPos(xs[hi], "x", true), box = u.bbox;
            u.ctx.save(); u.ctx.strokeStyle = axis; u.ctx.lineWidth = 1; u.ctx.setLineDash([3, 3]);
            u.ctx.beginPath(); u.ctx.moveTo(x, box.top); u.ctx.lineTo(x, box.top + box.height); u.ctx.stroke(); u.ctx.setLineDash([]);
            for (let si = 1; si < u.data.length; si++) {
              const v = u.data[si][hi]; if (v == null) continue;
              u.ctx.fillStyle = (u.series[si].stroke as string); u.ctx.beginPath(); u.ctx.arc(x, u.valToPos(v, "y", true), 4 * devicePixelRatio, 0, Math.PI * 2); u.ctx.fill();
            }
            u.ctx.restore();
          }

          const now = Date.now(), ctx = u.ctx, warn = css(root, "--warn");
          const late = dataRef.current.lateRecent.filter((l) => now - l.at < 5000);
          ctx.save(); ctx.strokeStyle = warn; ctx.lineWidth = 2;
          for (const l of late) {
            const si = aligned.visible.indexOf(l.metric);
            if (si < 0) continue;
            const pt = dataRef.current.series.get(l.metric)?.get(l.step);
            if (!pt) continue;
            const x = u.valToPos(l.step, "x", true), y = u.valToPos(pt.value, "y", true);
            ctx.beginPath(); ctx.arc(x, y, 7 * devicePixelRatio, 0, Math.PI * 2); ctx.stroke();
          }
          ctx.restore();
        }],
      },
    };
    const u = new uPlot(opts, aligned.data, el);
    plot.current = u;
    const ro = new ResizeObserver(() => u.setSize({ width: Math.max(300, el.clientWidth), height: 300 }));
    ro.observe(el);
    return () => { ro.disconnect(); u.destroy(); plot.current = null; };
    // series set changes rebuild the chart; plain data changes go through setData below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aligned.visible.join("|"), theme]);

  useEffect(() => { plot.current?.setData(aligned.data); }, [aligned]);
  const xs = aligned.data[0] as number[];
  const hi0 = hoverStep == null ? -1 : xs.indexOf(hoverStep);
  const hover = hi0 < 0 ? null : hi0;
  hoverRef.current = hover;
  useEffect(() => { plot.current?.redraw(false); }, [hover, aligned]);

  const range = () => visibleRange(xs, zoomRef.current?.min ?? null, zoomRef.current?.max ?? null);
  const resetZoom = () => { zoomRef.current = null; setZoomed(false); plot.current?.setData(aligned.data, true); };
  const onKeyDown = (e: React.KeyboardEvent) => {
    const next = stepIndex(range(), hover, e.key);
    if (next === undefined) return;
    e.preventDefault(); setHoverStep(next == null ? null : xs[next]);
  };
  const readout = hover != null && hover < xs.length
    ? `step ${xs[hover]} · ` + aligned.visible.map((m, i) => `${m} ${aligned.data[i + 1][hover] ?? "–"}`).join(" · ")
    : "";

  // redraw once more when the 5 s ring window ends
  useEffect(() => {
    if (!data.lateRecent.length) return;
    const t = setTimeout(() => plot.current?.redraw(), 5200);
    return () => clearTimeout(t);
  }, [version, data]);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "flex-end", minHeight: 28 }}>
        {zoomed && <button className="btn btn-sm" data-testid="reset-zoom" onClick={resetZoom}>Reset zoom</button>}
      </div>
      <div ref={host} role="group" aria-roledescription="chart" aria-label={label} tabIndex={0} data-testid="chart" onKeyDown={onKeyDown}
        onMouseLeave={() => { if (document.activeElement !== host.current) setHoverStep(null); }}
        style={{ width: "100%", minHeight: 300 }} />
      <p className="help mono" role="status" data-testid="chart-readout" style={{ minHeight: 18, paddingTop: 4 }}>{readout || "Focus the chart: ← → step through points, Home/End jump, Esc clears. Drag to zoom."}</p>
    </div>
  );
}
