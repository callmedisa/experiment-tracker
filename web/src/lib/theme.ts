import { useSyncExternalStore } from "react";

const KEY = "tracker-theme";
export type Mode = "light" | "dark";

function initial(): Mode {
  try { const v = localStorage.getItem(KEY); if (v === "dark" || v === "light") return v; } catch { /* storage blocked */ }
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
let mode: Mode = initial();
const subs = new Set<() => void>();

export function toggleTheme(): void {
  mode = mode === "dark" ? "light" : "dark";
  try { localStorage.setItem(KEY, mode); } catch { /* the page still works without persistence */ }
  subs.forEach((f) => f());
}
export function useTheme(): Mode {
  return useSyncExternalStore((f) => { subs.add(f); return () => { subs.delete(f); }; }, () => mode);
}
