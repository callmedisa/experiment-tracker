/** Silent-failure checks that are visible in the UI (TZ_dev.md §8.3: duplicates, queue overflow / resync, old key after rotation). */
import { expect, test } from "@playwright/test";
import type { APIRequestContext, Page } from "@playwright/test";
import { checksumOf, dbRows, dbRun } from "./helpers";

const ORIGIN = process.env.E2E_BASE ?? "http://localhost:8081";
const CSRF = { "X-Requested-With": "tracker", Origin: ORIGIN };

async function setup(page: Page, request: APIRequestContext, name: string) {
  const p = await (await request.post("/api/projects", { data: { name: `${name}-${Date.now() % 1000000}` } })).json();
  const bearer = { Authorization: `Bearer ${p.keys.ingest.key}` };
  const runId = (await (await request.post("/api/runs", { data: { name }, headers: bearer })).json()).id as string;
  expect((await page.context().request.post("/api/session", { data: { key: p.keys.read.key } })).status()).toBe(200);
  const send = (batch_id: string, points: unknown[]) => request.post(`/api/runs/${runId}/metrics`, { data: { batch_id, points }, headers: bearer });
  return { p, bearer, runId, send };
}
const pts = (from: number, n: number) => Array.from({ length: n }, (_, i) => ({ metric: "loss", step: from + i, value: 1 / (1 + from + i) }));
const state = (page: Page) => page.evaluate(() => { const t = (window as any).__tracker; return { count: t.count, state: t.state, last: t.lastSeq, missing: t.missing, checksum: t.checksum, history: t.history.map((h: any) => h.state) }; });

test("duplicates: the same batch again shows a toast, counts exactly, and a conflicting resend stores nothing", async ({ page, request }) => {
  const { runId, send } = await setup(page, request, "dup");
  await page.goto(`/runs/${runId}`);
  await page.waitForFunction(() => (window as any).__tracker?.state === "live");
  expect((await send("b1", pts(0, 10))).status()).toBe(200);
  await page.waitForFunction(() => (window as any).__tracker.count === 10);
  // same batch_id again (client retry)
  const again = await (await send("b1", pts(0, 10))).json();
  expect(again).toEqual({ accepted: 10, duplicates: 0, first_seq: 1, last_seq: 10 });   // the ORIGINAL answer
  await expect(page.getByText(/Duplicate batch b1 ignored/)).toBeVisible({ timeout: 5000 });
  // re-split: same points under a new batch id
  expect((await (await send("b2", pts(5, 8))).json())).toMatchObject({ accepted: 3, duplicates: 5 });
  await expect(page.getByText(/Duplicate batch b2 ignored/)).toBeVisible({ timeout: 5000 });
  // same point id, different value: whole batch rejected, nothing stored
  const conflict = await send("b3", [{ metric: "loss", step: 0, value: 999 }, ...pts(100, 3)]);
  expect(conflict.status()).toBe(409);
  expect((await conflict.json()).code).toBe("point_conflict");
  await page.waitForTimeout(1500);
  const s = await state(page);
  const db = dbRows(runId), run = dbRun(runId);
  expect(db.length).toBe(13);                       // 10 + 3 new from b2; nothing from b1 again or b3
  expect(run.dups).toBe(10 + 5);                    // b1 retry (10 points) + b2 re-split (5 duplicate points)
  expect(s.count).toBe(13);
  expect(s.checksum).toBe(checksumOf(db));
  // the page shows the server's authoritative duplicate counter via stats (shown in the run header chips is P1; asserted via the hook-free API)
  const api = await (await page.context().request.get(`/api/runs/${runId}`)).json();
  expect(api.duplicates_ignored).toBe(15);
});

test("overflow: a reader that falls more than 10,000 events behind gets a resync, recovers, and matches the database", async ({ page, request }) => {
  test.setTimeout(240_000);
  const { runId, send } = await setup(page, request, "overflow");
  await page.goto(`/runs/${runId}`);
  await page.waitForFunction(() => (window as any).__tracker?.state === "live");
  await send("seed", pts(0, 50));
  await page.waitForFunction(() => (window as any).__tracker.count === 50);
  await page.evaluate(() => window.dispatchEvent(new Event("offline")));            // the reader stops
  const t0 = Date.now();
  for (let i = 0; i < 13; i++) expect((await send(`big-${i}`, pts(50 + i * 1000, 1000))).status() ).toBe(200);   // 13,000 events while it is away
  const ingestMs = Date.now() - t0;
  await page.evaluate(() => window.dispatchEvent(new Event("online")));
  await page.waitForFunction(() => { const t = (window as any).__tracker; return t.state === "live" && t.count === 13050; }, null, { timeout: 90_000 });
  const s = await state(page);
  const db = dbRows(runId);
  expect(s.history).toContain("resync");
  expect(db.length).toBe(13050);
  expect(s.count).toBe(13050);
  expect(s.missing).toEqual([]);
  expect(s.checksum).toBe(checksumOf(db));
  test.info().annotations.push({ type: "evidence", description: `13,000 events ingested in ${ingestMs} ms while the reader was away; history: ${s.history.join(" > ")}` });
});

test("old key after rotation: REST is 401 and the open page is signed out within seconds", async ({ browser, request }) => {
  // a read key for this page's session, an admin key to operate the keys
  const ctxAdmin = await browser.newContext({ baseURL: ORIGIN });
  const ctxViewer = await browser.newContext({ baseURL: ORIGIN });
  const p = await (await request.post("/api/projects", { data: { name: `rot-${Date.now() % 1000000}` } })).json();
  const bearer = { Authorization: `Bearer ${p.keys.ingest.key}` };
  const runId = (await (await request.post("/api/runs", { data: { name: "rot" }, headers: bearer })).json()).id as string;
  await ctxAdmin.request.post("/api/session", { data: { key: p.keys.admin.key } });
  const viewerKey = await (await ctxAdmin.request.post("/api/keys", { data: { label: "viewer", scope: "read" }, headers: CSRF })).json();
  await ctxViewer.request.post("/api/session", { data: { key: viewerKey.key } });
  const page = await ctxViewer.newPage();
  await page.goto(`/runs/${runId}`);
  await page.waitForFunction(() => (window as any).__tracker?.state === "live");
  const t0 = Date.now();
  const rot = await ctxAdmin.request.post(`/api/keys/${viewerKey.id}/rotate`, { headers: CSRF });
  expect(rot.status()).toBe(201);
  await page.waitForURL(/\/sign-in\?reason=revoked/, { timeout: 15_000 });
  const secs = (Date.now() - t0) / 1000;
  await expect(page.getByText(/signed out because your key was revoked or rotated/i)).toBeVisible();
  expect(secs).toBeLessThan(10);
  const old = await ctxViewer.request.get(`/api/runs/${runId}`);
  expect(old.status()).toBe(401);
  expect((await old.json()).code).toBe("key_revoked");
  // the old ingest key is dead too
  const rotIng = await ctxAdmin.request.post(`/api/keys/${p.keys.ingest.id}/rotate`, { headers: CSRF });
  expect(rotIng.status()).toBe(201);
  const oldIng = await request.post(`/api/runs/${runId}/metrics`, { data: { batch_id: "x", points: pts(0, 1) }, headers: bearer });
  expect(oldIng.status()).toBe(401);
  test.info().annotations.push({ type: "evidence", description: `page signed out ${secs.toFixed(1)} s after rotation` });
  await ctxAdmin.close(); await ctxViewer.close();
});
