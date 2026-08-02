const { test, expect } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;

const wcagTags = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

async function expectNoHorizontalOverflow(page) {
  await expect.poll(() => page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth + 1,
  )).toBe(true);
}

async function expectNoWcagViolations(page) {
  await page.evaluate(async () => {
    const finiteAnimations = document.getAnimations().filter((animation) => {
      const timing = animation.effect && animation.effect.getComputedTiming();
      return timing && Number.isFinite(timing.endTime);
    });
    await Promise.all(finiteAnimations.map((animation) => animation.finished.catch(() => undefined)));
  });
  const results = await new AxeBuilder({ page }).withTags(wcagTags).analyze();
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
}

test("login is usable and WCAG 2.2 AA clean", async ({ page }) => {
  await page.goto("/login");
  await expect(page.locator("#login-email")).toBeVisible();
  await expect(page.locator("#login-password")).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectNoWcagViolations(page);
});

test("registration is usable and WCAG 2.2 AA clean", async ({ page }) => {
  await page.goto("/register");
  await expect(page.locator("#register-name")).toBeVisible();
  await expect(page.locator("#register-email")).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectNoWcagViolations(page);
});

test("authenticated course navigation works without overflow", async ({ page }) => {
  await page.goto("/login");
  const consent = page.locator('#cookie-consent:not([hidden]) [data-consent-choice="essential"]');
  if (await consent.isVisible()) await consent.click();
  await page.locator("#login-email").fill("browser-e2e@example.test");
  await page.locator("#login-password").fill("e2e-password-123");
  await page.locator('form[action="/login"] button[type="submit"]').click();
  await expect(page).toHaveURL(/\/student(?:$|\/)/);
  await page.goto("/student/courses");
  await expect(page.getByRole("button", { name: "Agregar a mano" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
  await expectNoWcagViolations(page);
});
