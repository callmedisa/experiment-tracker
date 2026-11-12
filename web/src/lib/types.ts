export interface MetricEvent {
  seq: number; point_id: string; metric: string; step: number; value: number;
  ts: string | null; received_at: string; is_late: boolean;
  replayed?: boolean; // applied while catching up after a reconnect
}
export interface Stats {
  last_seq: number; count: number; duplicates_ignored: number; status: "running" | "finished" | "failed";
  last_event_at: string | null; server_time: string;
}
export type ConnState = "connecting" | "live" | "reconnecting" | "catching_up" | "offline" | "resync";
