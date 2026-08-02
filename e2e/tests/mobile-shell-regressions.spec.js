const { test, expect } = require("@playwright/test");

async function login(page) {
  await page.goto("/login");
  const consent = page.locator('#cookie-consent:not([hidden]) [data-consent-choice="essential"]');
  if (await consent.isVisible()) await consent.click();
  await page.locator("#login-email").fill("browser-e2e@example.test");
  await page.locator("#login-password").fill("e2e-password-123");
  await page.locator('form[action="/login"] button[type="submit"]').click();
  await expect(page).toHaveURL(/\/student(?:$|\/)/);
}

test("landing logo mark stays inside the mobile header", async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith("mobile"), "mobile projects only");
  await page.goto("/");
  await page.evaluate(() => localStorage.setItem("machreach:intro-complete", "1"));
  await page.reload();
  await page.waitForFunction(() => document.body.dataset.intro === "done");

  const bounds = await page.evaluate(() => {
    const header = document.querySelector(".nav-inner").getBoundingClientRect();
    const mark = document.querySelector(".nav-inner .logo-mark svg").getBoundingClientRect();
    return { header: { top: header.top, bottom: header.bottom }, mark: { top: mark.top, bottom: mark.bottom } };
  });
  expect(bounds.mark.top).toBeGreaterThanOrEqual(bounds.header.top);
  expect(bounds.mark.bottom).toBeLessThanOrEqual(bounds.header.bottom);
});

test("mobile app navigation exposes all study pages and excludes Focus", async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith("mobile"), "mobile projects only");
  await login(page);

  const bar = page.locator(".tabbar");
  await expect(bar).toBeVisible();
  await expect(bar.getByText("Enfoque", { exact: true })).toHaveCount(0);
  await bar.getByRole("button", { name: "Más" }).click();

  const menu = page.locator(".mobile-more-menu");
  await expect(menu).toBeVisible();
  for (const label of ["Cursos", "Notas", "Quiz", "Flashcards", "Analíticas", "Tienda", "Cuenta"]) {
    await expect(menu.getByRole("link", { name: label, exact: true })).toBeVisible();
  }
});

test("Focus is blocked on mobile", async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith("mobile"), "mobile projects only");
  await login(page);
  await page.goto("/student/focus");
  await expect(page.locator(".focus-mobile-blocker")).toBeVisible();
  await expect(page.locator(".fx-start")).toBeHidden();
});
