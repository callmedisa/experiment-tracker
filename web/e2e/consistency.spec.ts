/**
 * E2E consistency (the dev spec §8.1): shuffled workload with duplicates and late points, a chaos proxy that cuts the
 * browser's SSE connection and the simulator's requests (including responses lost after the server processed them),
 * then: page count/checksum/chart series == the database, recomputed independently here.
 * Seeds: E2E_SEEDS="1,2,3" (default below). Raw per-seed evidence is written to results/e2e/.
 */
import { expect, test } from "@playwright/test";
import { spawn, execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(here, "../..");
const PY = process.env.E2E_PYTHON ?? resolve(REPO, ".venv/bin/python");
const CHAOS = process.env.E2E_CHAOS ?? "http://localhost:8099";
const SIM_URL = process.env.E2E_SIM_URL ?? "http://localhost:8082"; // simulator -> chaos(sim) -> api
const OUT = resolve(REPO, "results/e2e");
const SEEDS = (process.env.E2E_SEEDS ?? "1,2,3,4,5,6,7,8").split(",").map(Number);

// ---------- small helpers (each is a second implementation, not the app's code) ----------
function mulberry32(a: number) { return () => { a |= 0; a = (a + 0x6d2b79f5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const chaos = (path: string) => fetch(CHAOS + path, { method: "POST" }).then((r) => r.json());

interface Row { seq: number; point_id: string; metric: string; step: number; hex: string }
function dbRows(runId: string): Row[] {
  const sql = `select seq, point_id, metric, step, encode(float8send(value),'hex') from metric_events where run_id='${runId}' order by seq`;
  const out = execFileSync("docker", ["compose", "exec", "-T", "postgres", "psql", "-U", "tracker", "-d", "tracker", "-At", "-F", "|", "-c", sql], { cwd: REPO, encoding: "utf8", maxBuffer: 1 << 28 });
  return out.split("\n").filter(Boolean).map((l) => { const [seq, point_id, metric, step, hex] = l.split("|"); return { seq: +seq, point_id, metric, step: +step, hex }; });
}
function dbRunRow(runId: string): { last_seq: number; dups: number; status: string } {
  const out = execFileSync("docker", ["compose", "exec", "-T", "postgres", "psql", "-U", "tracker", "-d", "tracker", "-At", "-F", "|", "-c", `select last_seq, duplicates_ignored, status from runs where id='${runId}'`], { cwd: REPO, encoding: "utf8" }).trim();
  const [a, b, c] = out.split("|"); return { last_seq: +a, dups: +b, status: c };
}
function checksumOf(rows: Row[]): string {
  const sorted = [...rows].sort((x, y) => (x.point_id < y.point_id ? -1 : x.point_id > y.point_id ? 1 : 0));
  return createHash("sha256").update(sorted.map((r) => `${r.point_id}|${r.metric}|${r.step}|${r.hex}\n`).join("")).digest("hex");
}
function chartRule(rows: Row[]): Record<string, { step: number; value: number; seq: number }[]> {
  const m = new Map<string, Map<number, { value: number; seq: number }>>();
  for (const r of rows) { // rows are in seq order: later rows overwrite earlier ones (highest seq wins)
    if (!m.has(r.metric)) m.set(r.metric, new Map());
    m.get(r.metric)!.set(r.step, { value: Buffer.from(r.hex, "hex").readDoubleBE(0), seq: r.seq });
  }
  const out: Record<string, { step: number; value: number; seq: number }[]> = {};
  for (const [metric, steps] of m) out[metric] = [...steps].map(([step, v]) => ({ step, ...v })).sort((a, b) => a.step - b.step);
  return out;
}
function hasSubsequence(h: string[], want: string[]): boolean { let i = 0; for (const s of h) if (s === want[i]) i++; return i >= want.length; }

for (const seed of SEEDS) {
  test(`seed ${seed}: page == database after cut connections, lost responses, duplicates and late points`, async ({ page, request }) => {
    mkdirSync(OUT, { recursive: true });
    const rnd = mulberry32(seed * 7919);
    const evidence: Record<string, unknown> = { seed };

    // --- project + keys, session cookie for the browser context ---
    const created = await (await request.post("/api/projects", { data: { name: `e2e-seed-${seed}-${Date.now() % 100000}` } })).json();
    const ingest = created.keys.ingest.key, read = created.keys.read.key;
    const signin = await page.context().request.post("/api/session", { data: { key: read } });
    expect(signin.status()).toBe(200);

    const chaosBefore = await (await fetch(CHAOS + "/status")).json(); // counters are cumulative: report deltas per seed
    // --- chaos on the simulator's path: some requests die before the server sees them, some lose the response after it did ---
    const flaky = { reset: 0.12, lost: 0.12 };
    await chaos(`/sim/flaky?reset=${flaky.reset}&lost=${flaky.lost}&seed=${seed}`);
    evidence.flaky = flaky;

    // --- simulator (python client) ---
    const simOut = resolve(OUT, `seed-${seed}-sim.json`);
    const steps = 400;
    const sim = spawn(PY, [resolve(here, "simulator.py"), "--url", SIM_URL, "--key", ingest, "--seed", String(seed), "--steps", String(steps), "--delay", "3", "--out", simOut], { cwd: REPO });
    let simLog = ""; sim.stdout.on("data", (d) => (simLog += d)); sim.stderr.on("data", (d) => (simLog += d));
    const simDone = new Promise<number>((res) => sim.on("close", (c) => res(c ?? -1)));
    let runId = "";
    for (let i = 0; i < 100 && !runId; i++) { const m = /RUN (run_\w+)/.exec(simLog); if (m) runId = m[1]; else await sleep(100); }
    expect(runId, `simulator did not print a run id:\n${simLog}`).toBeTruthy();
    evidence.run_id = runId;

    await page.goto(`/runs/${runId}`);
    await page.waitForFunction(() => (window as any).__tracker?.state === "live", null, { timeout: 15_000 });

    // --- cut the browser's SSE connection at random moments while events flow ---
    const disruptions: string[] = [];
    const t0 = Date.now();
    let simFinished = false; void simDone.then(() => (simFinished = true));
    while (!simFinished) {   // keep cutting for as long as events are flowing
      await sleep(800 + Math.floor(rnd() * 2200));
      if (simFinished) break;
      // cut only a LIVE stream: otherwise the cut hits nothing (seen in the first full run, seeds 3 and 4)
      await page.waitForFunction(() => (window as any).__tracker?.state === "live", null, { timeout: 15_000 }).catch(() => {});
      if (simFinished) break;
      if (rnd() < 0.5) { const r = await chaos("/browser/drop"); disruptions.push(`drop(open=${r.dropped}) @${Date.now() - t0}ms`); }
      else { const secs = 1 + Math.floor(rnd() * 2); await chaos(`/browser/mode?mode=block&seconds=${secs}`); const r = await chaos("/browser/drop"); disruptions.push(`block ${secs}s+drop(open=${r.dropped}) @${Date.now() - t0}ms`); await sleep(secs * 1000 + 500); }
    }
    evidence.disruptions = disruptions;

    // --- let the simulator finish (it retries through the chaos), then lift the faults ---
    const simExit = await simDone;
    await chaos("/sim/pass"); await chaos("/browser/pass");
    evidence.sim_exit = simExit; evidence.sim_log = simLog.trim().split("\n").slice(-3);
    expect(simExit, `simulator failed:\n${simLog}`).toBe(0);
    const sum = JSON.parse(readFileSync(simOut, "utf8"));

    // --- wait for the page to quiesce: the server closed the stream and the page applied everything ---
    const db = dbRows(runId);
    await page.waitForFunction((n) => { const t = (window as any).__tracker; return t && t.closedByServer && t.count === n && t.state === "live"; }, db.length, { timeout: 40_000 })
      .catch(async () => { evidence.timeout_page = await page.evaluate(() => { const t = (window as any).__tracker; return { state: t.state, count: t.count, last: t.lastSeq, closed: t.closedByServer, missing: t.missing.slice(0, 20) }; }); });

    const pageState = await page.evaluate(() => { const t = (window as any).__tracker; return { count: t.count, lastSeq: t.lastSeq, checksum: t.checksum, state: t.state, missing: t.missing, history: t.history.map((h: any) => h.state), series: t.series, closed: t.closedByServer }; });
    const runRow = dbRunRow(runId);
    const dbChecksum = checksumOf(db), dbSeries = chartRule(db);
    evidence.db = { count: db.length, checksum: dbChecksum, last_seq: runRow.last_seq, duplicates_ignored: runRow.dups, status: runRow.status };
    evidence.page = { count: pageState.count, checksum: pageState.checksum, last_seq: pageState.lastSeq, missing: pageState.missing, closed: pageState.closed, history: pageState.history };
    evidence.sim = { unique_points: sum.intended.length, stats: sum.stats, batches: sum.batches, same_batch_resends: sum.same_batch_resends, resplit_resends: sum.resplit_resends, held_back_late: sum.held_back_late };
    const chaosAfter = await (await fetch(CHAOS + "/status")).json();
    const delta = (l: string) => Object.fromEntries(Object.keys(chaosAfter[l]).filter((k) => typeof chaosAfter[l][k] === "number" && k !== "open").map((k) => [k, chaosAfter[l][k] - chaosBefore[l][k]]));
    evidence.chaos = { sim: delta("sim"), browser: delta("browser") };
    writeFileSync(resolve(OUT, `seed-${seed}.json`), JSON.stringify(evidence, null, 2));

    // ===== assertions =====
    // 1. database: seq contiguous, run counter agrees, nothing lost or invented relative to what the simulator meant to send
    expect(db.map((r) => r.seq)).toEqual(Array.from({ length: db.length }, (_, i) => i + 1));
    expect(runRow.last_seq).toBe(db.length);
    expect(new Set(db.map((r) => r.point_id)).size).toBe(db.length);
    expect(db.map((r) => [r.point_id, r.metric, r.step, r.hex]).sort()).toEqual(sum.intended);
    // 2. the page equals the database
    expect(pageState.missing).toEqual([]);
    expect(pageState.count).toBe(db.length);
    expect(pageState.lastSeq).toBe(db.length);
    expect(pageState.checksum).toBe(dbChecksum);
    expect(pageState.series).toEqual(dbSeries);          // chart rule: per metric/step the highest seq, ordered by step
    // 3. the server's own /integrity agrees with the independent computation
    const integ = await (await page.context().request.get(`/api/runs/${runId}/integrity`)).json();
    expect(integ).toEqual({ count: db.length, checksum: dbChecksum, last_seq: db.length });
    // 4. the UI really went through the states
    expect(hasSubsequence(pageState.history, ["live", "reconnecting", "catching_up", "live"]), `history: ${pageState.history.join(" > ")}`).toBe(true);
    expect(pageState.closed).toBe(true);
    // 5. duplicates were really exercised
    expect(runRow.dups).toBeGreaterThan(0);
    expect(sum.stats.retries).toBeGreaterThan(0);
    // 6. the chaos was real: several cuts actually hit an open stream, and some responses were lost after processing
    expect(disruptions.filter((d) => !d.includes("open=0")).length).toBeGreaterThanOrEqual(3);
    expect((evidence.chaos as any).sim.lost).toBeGreaterThan(0);
  });
}
