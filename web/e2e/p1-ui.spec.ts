/** P1 UI: Keys page flows and dialogs (incl. #25 position:fixed), dark theme, 390 px mobile layout. Screenshots go to results/ui/. */
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { resolve } from "node:path";
import { REPO } from "./helpers";

const ORIGIN = process.env.E2E_BASE ?? "http://localhost:8081";
const CSRF = { "X-Requested-With": "tracker", Origin: ORIGIN };
const SHOTS = resolve(REPO, "results/ui");

async function project(page: Page, request: any, name: string) {
  const p = await (await request.post("/api/projects", { data: { name: `${name}-${Date.now() % 1000000}` } })).json();
  // admin key: the Keys page needs it (a read session can look at runs but not manage keys)
  expect((await page.context().request.post("/api/session", { data: { key: p.keys.admin.key } })).status()).toBe(200);
  return { p, ingest: { Authorization: `Bearer ${p.keys.ingest.key}` } };
}
const row = (page: Page, label: string) => page.getByTestId("key-row").filter({ hasText: label });

test.describe("keys page", () => {
  test("a read-key session cannot manage keys: the page says so and offers no Create button", async ({ page, request }) => {
    const p = await (await request.post("/api/projects", { data: { name: `keys-read-${Date.now() % 1000000}` } })).json();
    expect((await page.context().request.post("/api/session", { data: { key: p.keys.read.key } })).status()).toBe(200);
    await page.goto("/keys");
    await expect(page.getByText(/Managing keys needs an admin key/)).toBeVisible();
    await expect(page.getByTestId("create-key")).toHaveCount(0);
    expect((await page.context().request.post("/api/keys", { data: { label: "x", scope: "admin" }, headers: CSRF })).status()).toBe(403);
  });

  test("list, create, copy, use the new key, validation", async ({ page, request }) => {
    const { p } = await project(page, request, "keys-create");
    await page.goto("/keys");
    await expect(page.getByTestId("key-row")).toHaveCount(3);
    await expect(row(page, "Default admin")).toContainText("this session");
    await expect(row(page, "Default admin")).toContainText(`trk_adm_${p.keys.admin.prefix}…`);
    await expect(page.locator("body")).not.toContainText(p.keys.admin.key);               // the list never shows full keys

    await page.getByTestId("create-key").click();
    await page.getByRole("button", { name: "Create key" }).last().click();               // empty label
    await expect(page.getByText("Enter a label")).toBeVisible();
    await page.getByLabel("Label").fill("ci-runner");
    await page.getByRole("button", { name: "Create key" }).last().click();
    const secret = await page.getByTestId("new-key").inputValue();
    expect(secret).toMatch(/^trk_ing_[A-Za-z0-9]{32}$/);
    await page.getByTestId("copied").click();
    await expect(page.getByText("“ci-runner” created.")).toBeVisible();
    await expect(row(page, "ci-runner")).toContainText("active");
    const ok = await request.post("/api/runs", { data: { name: "with-new-key" }, headers: { Authorization: `Bearer ${secret}` } });
    expect(ok.status()).toBe(201);                                                     // the shown key really works
    await page.screenshot({ path: resolve(SHOTS, "keys-light-desktop.png") });
  });

  test("rotate: alert dialog focus, Esc, reveal with old key revoked, old key dead", async ({ page, request }) => {
    const { p, ingest } = await project(page, request, "keys-rotate");
    await page.goto("/keys");
    await page.getByRole("button", { name: "Rotate Default ingest" }).click();
    const dlg = page.getByRole("alertdialog");
    await expect(dlg).toContainText("The old key stops working immediately");
    await expect(page.getByRole("button", { name: "Cancel" })).toBeFocused();           // alert dialogs start on Cancel
    await page.keyboard.press("Escape");
    await expect(dlg).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Rotate Default ingest" })).toBeFocused();   // focus returns to the trigger
    await page.getByRole("button", { name: "Rotate Default ingest" }).click();
    await page.getByRole("button", { name: "Rotate key" }).click();
    const secret = await page.getByTestId("new-key").inputValue();
    await expect(page.getByRole("dialog")).toContainText("was revoked at");
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("new-key")).toBeVisible();                            // Esc must not discard a one-time key
    await page.getByTestId("copied").click();
    const old = await request.post("/api/runs", { data: { name: "x" }, headers: ingest });
    expect(old.status()).toBe(401); expect((await old.json()).code).toBe("key_revoked");
    expect((await request.post("/api/runs", { data: { name: "x" }, headers: { Authorization: `Bearer ${secret}` } })).status()).toBe(201);
    await expect(page.getByTestId("key-row").filter({ hasText: "revoked" })).toHaveCount(1);
    expect(secret).not.toBe(p.keys.ingest.key);
  });

  test("revoke needs the typed label; the last admin key cannot be revoked", async ({ page, request }) => {
    await project(page, request, "keys-revoke");
    await page.goto("/keys");
    await page.getByTestId("create-key").click();
    await page.getByLabel("Label").fill("temp-key");
    await page.getByRole("button", { name: "Create key" }).last().click();
    await page.getByTestId("copied").click();
    await page.getByRole("button", { name: "Revoke temp-key" }).click();
    const go = page.getByRole("button", { name: "Revoke key" });
    await expect(go).toBeDisabled();
    await page.getByLabel(/Type .* to confirm/).fill("temp-key");
    await expect(go).toBeEnabled();
    await go.click();
    await expect(row(page, "temp-key")).toContainText("revoked");
    // the only admin key is the one this session uses
    await page.getByRole("button", { name: "Revoke Default admin" }).click();
    await page.getByLabel(/Type .* to confirm/).fill("Default admin");
    await page.getByRole("button", { name: "Revoke key" }).click();
    await expect(page.getByText("Create another admin key before revoking the last one")).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/keys");                                // still signed in
  });

  test("rotating the key of the current session: the new key stays on screen until copied, then sign-in", async ({ page, request }) => {
    await project(page, request, "keys-self");
    await page.goto("/keys");
    await page.getByRole("button", { name: "Rotate Default admin" }).click();
    await page.getByRole("button", { name: "Rotate key" }).click();
    const secret = await page.getByTestId("new-key").inputValue();
    expect(secret).toMatch(/^trk_adm_/);
    await expect(page.getByRole("dialog")).toContainText("signed out when you continue");
    await page.waitForTimeout(2500);                                                   // the session is dead now; nothing may redirect yet
    expect(new URL(page.url()).pathname).toBe("/keys");
    await expect(page.getByTestId("new-key")).toHaveValue(secret);
    await page.getByTestId("copied").click();
    await page.waitForURL(/\/sign-in\?reason=revoked/);
    await page.getByLabel("Project key").fill(secret);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.waitForURL(/\/runs$/);                                                  // the new key signs in
  });

  test("dialogs keep focus inside, and (#25) stay in the viewport on a long page scrolled to the bottom", async ({ page, request }) => {
    const { p } = await project(page, request, "keys-fixed");
    for (let i = 0; i < 14; i++) await page.context().request.post("/api/keys", { data: { label: `filler-${i}`, scope: "ingest" }, headers: CSRF });
    await page.goto("/keys");
    await expect(page.getByTestId("key-row")).toHaveCount(17);
    const tall = await page.evaluate(() => document.documentElement.scrollHeight > window.innerHeight + 100);
    expect(tall, "the page must be taller than the viewport for this check to mean anything").toBe(true);
    await page.getByRole("button", { name: "Rotate Default ingest" }).scrollIntoViewIfNeeded();
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    const y = await page.evaluate(() => window.scrollY);
    expect(y).toBeGreaterThan(100);
    await page.getByRole("button", { name: "Rotate Default ingest" }).click();
    const box = await page.getByTestId("dialog").boundingBox();
    const scrim = await page.getByTestId("scrim").boundingBox();
    const vh = page.viewportSize()!.height;
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.y + box!.height).toBeLessThanOrEqual(vh);
    expect(scrim!.y).toBeLessThanOrEqual(0);
    expect(scrim!.height).toBeGreaterThanOrEqual(vh);
    // Tab / Shift+Tab never leave the dialog
    for (let i = 0; i < 6; i++) { await page.keyboard.press("Tab"); expect(await page.evaluate(() => !!document.activeElement?.closest("[role=alertdialog]"))).toBe(true); }
    for (let i = 0; i < 6; i++) { await page.keyboard.press("Shift+Tab"); expect(await page.evaluate(() => !!document.activeElement?.closest("[role=alertdialog]"))).toBe(true); }
    test.info().annotations.push({ type: "evidence #25", description: `scrollY ${y}, viewport height ${vh}, dialog y ${Math.round(box!.y)}..${Math.round(box!.y + box!.height)}, scrim y ${Math.round(scrim!.y)} height ${Math.round(scrim!.height)}` });
    void p;
  });
});

test.describe("dark theme", () => {
  test("follows the OS preference, toggles, persists, and the chart palette changes", async ({ page, request }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    const { ingest } = await project(page, request, "theme");
    const run = await (await request.post("/api/runs", { data: { name: "t" }, headers: ingest })).json();
    await request.post(`/api/runs/${run.id}/metrics`, { headers: ingest, data: { batch_id: "t1", points: Array.from({ length: 30 }, (_, i) => ({ metric: "loss", step: i, value: 1 / (1 + i) })) } });
    await page.goto(`/runs/${run.id}`);
    const isDark = () => page.evaluate(() => document.querySelector(".trk")!.classList.contains("dark"));
    const chart1 = () => page.evaluate(() => getComputedStyle(document.querySelector(".trk")!).getPropertyValue("--chart-1").trim());
    expect(await isDark()).toBe(true);                                                  // first visit follows prefers-color-scheme
    const darkColor = await chart1();
    await page.screenshot({ path: resolve(SHOTS, "run-dark-desktop.png") });
    await page.getByTestId("theme-toggle").click();
    expect(await isDark()).toBe(false);
    expect(await chart1()).not.toBe(darkColor);
    await expect(page.getByTestId("chart").locator("canvas")).toHaveCount(1);          // chart rebuilt, not duplicated
    await page.screenshot({ path: resolve(SHOTS, "run-light-desktop.png") });
    await page.reload();                                                                // persisted choice beats the OS preference
    expect(await page.evaluate(() => localStorage.getItem("tracker-theme"))).toBe("light");
    expect(await isDark()).toBe(false);
    await page.getByTestId("theme-toggle").click();
    expect(await page.evaluate(() => localStorage.getItem("tracker-theme"))).toBe("dark");
  });
});

test.describe("mobile 390", () => {
  test.use({ viewport: { width: 390, height: 844 } });
  test("drawer navigation, no horizontal page scroll, run page and keys page usable", async ({ page, request }) => {
    const { ingest } = await project(page, request, "mobile");
    const run = await (await request.post("/api/runs", { data: { name: "mobile-run" }, headers: ingest })).json();
    await request.post(`/api/runs/${run.id}/metrics`, { headers: ingest, data: { batch_id: "m1", points: Array.from({ length: 40 }, (_, i) => ({ metric: i % 2 ? "loss" : "accuracy", step: Math.floor(i / 2), value: 1 / (1 + i) })) } });
    const noHScroll = () => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1);
    for (const path of ["/runs", "/keys", `/runs/${run.id}`]) {
      await page.goto(path);
      await page.waitForTimeout(800);
      expect(await noHScroll(), `horizontal scroll on ${path}`).toBe(true);
    }
    await page.goto("/runs");
    await expect(page.locator(".sidenav")).toBeHidden();
    await page.getByRole("button", { name: "Open navigation" }).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page.screenshot({ path: resolve(SHOTS, "mobile-drawer.png") });
    await page.getByRole("dialog").getByRole("link", { name: "Keys" }).click();
    await page.waitForURL(/\/keys$/);
    await expect(page.getByRole("dialog")).toHaveCount(0);                              // drawer closes on navigation
    await page.screenshot({ path: resolve(SHOTS, "mobile-keys.png"), fullPage: true });
    await page.goto(`/runs/${run.id}`);
    await expect(page.getByTestId("conn-chip")).toBeVisible();
    await expect(page.getByTestId("verify")).toBeVisible();
    await expect(page.getByTestId("chart").locator("canvas")).toHaveCount(1);
    await expect(page.getByTestId("latest-card")).toHaveCount(2);
    await page.screenshot({ path: resolve(SHOTS, "mobile-run-light.png"), fullPage: true });
    await page.emulateMedia({ colorScheme: "dark" });
    await page.getByTestId("theme-toggle").click();
    await page.screenshot({ path: resolve(SHOTS, "mobile-run-dark.png"), fullPage: true });
  });
});
