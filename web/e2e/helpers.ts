import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

export const REPO = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
export interface Row { seq: number; point_id: string; metric: string; step: number; hex: string }

/** Ground truth straight from Postgres (psql in the postgres container): independent of the API under test. */
export function dbRows(runId: string): Row[] {
  const sql = `select seq, point_id, metric, step, encode(float8send(value),'hex') from metric_events where run_id='${runId}' order by seq`;
  const out = execFileSync("docker", ["compose", "exec", "-T", "postgres", "psql", "-U", "tracker", "-d", "tracker", "-At", "-F", "|", "-c", sql], { cwd: REPO, encoding: "utf8", maxBuffer: 1 << 28 });
  return out.split("\n").filter(Boolean).map((l) => { const [seq, point_id, metric, step, hex] = l.split("|"); return { seq: +seq, point_id, metric, step: +step, hex }; });
}
export function dbRun(runId: string): { last_seq: number; dups: number; status: string } {
  const out = execFileSync("docker", ["compose", "exec", "-T", "postgres", "psql", "-U", "tracker", "-d", "tracker", "-At", "-F", "|", "-c", `select last_seq, duplicates_ignored, status from runs where id='${runId}'`], { cwd: REPO, encoding: "utf8" }).trim();
  const [a, b, c] = out.split("|"); return { last_seq: +a, dups: +b, status: c };
}
export function checksumOf(rows: Row[]): string {
  const sorted = [...rows].sort((x, y) => (x.point_id < y.point_id ? -1 : x.point_id > y.point_id ? 1 : 0));
  return createHash("sha256").update(sorted.map((r) => `${r.point_id}|${r.metric}|${r.step}|${r.hex}\n`).join("")).digest("hex");
}
