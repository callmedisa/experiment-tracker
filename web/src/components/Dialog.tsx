import { ReactNode, useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { Trk } from "./Trk";

// #25 (see results/ui/fixed-position-experiment.md): tracker.css gives `.trk` `container-type: inline-size`. In the Chromiums tested that does NOT
// make `.trk` the containing block of `position: fixed` children, so a fixed scrim inside the page works. Dialogs are still portalled to <body>
// (robust if another engine differs). The portal wrapper must NOT keep `container-type` (a fixed element that is itself an inline-size
// container broke hit-testing and painting in Chromium 129: nothing was clickable and nothing was drawn).
const PORTAL = true;

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select,textarea,[tabindex]:not([tabindex="-1"])';

/** Modal dialog: Esc closes, Tab stays inside, focus starts on [data-autofocus] (Cancel for alert dialogs) and returns to the trigger. */
export function Dialog({ role = "dialog", labelledBy, describedBy, onClose, width, children, top = 140 }:
  { role?: "dialog" | "alertdialog"; labelledBy: string; describedBy?: string; onClose: () => void; width?: number; children: ReactNode; top?: number }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const el = box.current!;
    (el.querySelector<HTMLElement>("[data-autofocus]") ?? el.querySelector<HTMLElement>(FOCUSABLE))?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); return; }
      if (e.key !== "Tab") return;
      const items = [...el.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, [onClose]);

  const scrim = (
    <div className="scrim" data-testid="scrim" style={{ position: "fixed", inset: 0, zIndex: 100, alignItems: "flex-start", paddingTop: top, overflow: "auto" }}
         onMouseDown={(e) => { if (e.target === e.currentTarget && role === "dialog") onClose(); }}>
      <div ref={box} className="dialog" role={role} aria-modal="true" aria-labelledby={labelledBy} aria-describedby={describedBy} style={width ? { width } : undefined} data-testid="dialog">
        {children}
      </div>
    </div>
  );
  return PORTAL ? createPortal(<Trk style={{ position: "fixed", inset: 0, zIndex: 100, minHeight: 0, background: "transparent", pointerEvents: "none", containerType: "normal" } as React.CSSProperties}><div style={{ pointerEvents: "auto", position: "absolute", inset: 0 }}>{scrim}</div></Trk>, document.body) : scrim;
}
