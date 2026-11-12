/** Chart interaction: drag to zoom, Reset zoom, keyboard point stepping (← → Home End Esc). Real browser, real uPlot canvas. */
import { expect, test } from "@playwright/test";

test("drag zooms, zoom survives new data, Reset zoom restores; arrows step through points", async ({ page, request }) => {
  const p = await (await request.post("/api/projects", { data: { name: `chart-${Date.now() % 1000000}` } })).json();
  expect((await page.context().request.post("/api/session", { data: { key: p.keys.read.key } })).status()).toBe(200);
  const ingest = { Authorization: `Bearer ${p.keys.ingest.key}` };
  const run = await (await request.post("/api/runs", { data: { name: "zoom" }, headers: ingest })).json();
  const send = (id: string, from: number, to: number) => request.post(`/api/runs/${run.id}/metrics`, { headers: ingest, data: { batch_id: id, points: Array.from({ length: to - from }, (_, i) => ({ metric: "loss", step: from + i, value: 1 / (1 + from + i) })) } });
  expect((await send("a", 0, 100)).status()).toBe(200);
  await page.goto(`/runs/${run.id}`);
  const chart = page.getByTestId("chart"), readout = page.getByTestId("chart-readout");
  await expect(chart.locator("canvas")).toHaveCount(1);
  const win = async () => ({ min: Number(await chart.getAttribute("data-xmin")), max: Number(await chart.getAttribute("data-xmax")) });
  expect(await win()).toEqual({ min: 0, max: 99 });
  await expect(page.getByTestId("reset-zoom")).toHaveCount(0);

  // keyboard: nothing hovered -> ← lands on the point before the newest (step 98), Home -> 0, End -> 99, Esc clears
  await chart.focus();
  await page.keyboard.press("ArrowLeft");
  await expect(readout).toContainText("step 98 ·");
  await page.keyboard.press("Home");
  await expect(readout).toContainText("step 0 ·");
  await page.keyboard.press("ArrowRight");
  await expect(readout).toContainText("step 1 ·");
  await page.keyboard.press("End");
  await expect(readout).toContainText("step 99 ·");
  await page.keyboard.press("ArrowRight");
  await expect(readout).toContainText("step 99 ·");                                  // clamped
  await page.keyboard.press("Escape");
  await expect(readout).toContainText("Focus the chart");

  // drag over the middle of the plot area: zoomed, Reset appears, stepping now stays inside the window
  const over = chart.locator(".u-over"), box = (await over.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.4, box.y + box.height / 2);
  await page.mouse.down(); await page.mouse.move(box.x + box.width * 0.6, box.y + box.height / 2, { steps: 5 }); await page.mouse.up();
  await expect(page.getByTestId("reset-zoom")).toBeVisible();
  const z = await win();
  expect(z.min).toBeGreaterThan(20); expect(z.max).toBeLessThan(80);                 // the canvas really shows a narrower x window
  await chart.focus();
  await page.keyboard.press("End");
  const hiTxt = await readout.innerText(); const hi = Number(/step (\d+)/.exec(hiTxt)![1]);
  await page.keyboard.press("Home");
  const lo = Number(/step (\d+)/.exec(await readout.innerText())![1]);
  expect(lo).toBeGreaterThan(20); expect(hi).toBeLessThan(80); expect(hi).toBeGreaterThan(lo);   // 40%..60% of 0..99, give or take pixel rounding

  // new points arrive while zoomed: the window is kept (End still inside it), counters still use everything
  expect((await send("b", 100, 150)).status()).toBe(200);
  await expect(page.getByTestId("chart").locator("canvas")).toHaveCount(1);
  await page.waitForTimeout(500);
  expect(await win()).toEqual(z);                                                    // same window on screen, not reset to 0..149
  await chart.focus(); await page.keyboard.press("End");
  expect(Number(/step (\d+)/.exec(await readout.innerText())![1])).toBe(hi);

  await page.getByTestId("reset-zoom").click();
  await expect(page.getByTestId("reset-zoom")).toHaveCount(0);
  expect(await win()).toEqual({ min: 0, max: 149 });
  await chart.focus(); await page.keyboard.press("End");
  await expect(readout).toContainText("step 149 ·");                                 // whole range again, including the new points
});
