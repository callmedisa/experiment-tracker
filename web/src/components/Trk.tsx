import { CSSProperties, ReactNode, useEffect } from "react";
import { useTheme } from "../lib/theme";

/** The app root class from design/tracker.css: `.trk`, plus `.dark` for the dark theme. Every screen uses this wrapper. */
export function Trk({ children, style, className = "" }: { children: ReactNode; style?: CSSProperties; className?: string }) {
  const mode = useTheme();
  useEffect(() => {
    document.documentElement.style.colorScheme = mode;
    document.body.style.background = mode === "dark" ? "#0E0F12" : "#F6F7F9"; // same as --bg, so overscroll and gaps match
  }, [mode]);
  return <div className={`trk ${mode === "dark" ? "dark" : ""} ${className}`} style={style}>{children}</div>;
}
