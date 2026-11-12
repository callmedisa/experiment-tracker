export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string, public retryAfter?: number) { super(message); }
}

let authLostHandler: ((reason: "expired" | "revoked") => void) | null = null;
export const onAuthLost = (fn: typeof authLostHandler) => { authLostHandler = fn; };
/** While a one-time key is on screen (rotating the key of the CURRENT session), a 401 must not redirect before it was copied. */
let suppressAuthLost = 0;
export const holdAuthRedirects = () => { suppressAuthLost++; return () => { suppressAuthLost = Math.max(0, suppressAuthLost - 1); }; };

export async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown; signal?: AbortSignal } = {}): Promise<T> {
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-Requested-With"] = "tracker"; // CSRF header; the Origin header is added by the browser
  let res: Response;
  try {
    res = await fetch("/api" + path, { method, headers, credentials: "same-origin", body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined, signal: opts.signal });
  } catch {
    throw new ApiError(0, "network", "network");
  }
  if (res.status === 204) return undefined as T;
  let json: any = null;
  try { json = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    const e = new ApiError(res.status, json?.code ?? "http_error", json?.message ?? res.statusText, json?.request_id,
      res.headers.get("retry-after") ? Number(res.headers.get("retry-after")) : undefined);
    if (res.status === 401 && !(path === "/session" && method === "POST")) {
      if (!suppressAuthLost) {
        if (e.code === "session_expired") authLostHandler?.("expired");
        if (e.code === "key_revoked") authLostHandler?.("revoked");
      }
    }
    throw e;
  }
  return json as T;
}

export interface SessionInfo { project: { id: string; name: string }; key: { label: string; prefix: string; scope: string } }
export interface RunSummary {
  id: string; name: string; status: "running" | "finished" | "failed"; created_at: string; finished_at: string | null;
  events_count: number; duplicates_ignored: number; last_event_at: string | null; primary_metric: string | null;
  latest_value: number | null; spark: number[]; stale: boolean;
}
export interface RunInfo {
  id: string; name: string; status: "running" | "finished" | "failed"; created_at: string; finished_at: string | null;
  last_seq: number; count: number; duplicates_ignored: number; last_event_at: string | null; server_time: string; stale: boolean; stale_after_s: number;
}

export interface KeyRow { id: string; label: string; scope: "ingest" | "read" | "admin"; prefix: string; created_at: string; last_used_at: string | null; revoked_at: string | null; key?: string }
