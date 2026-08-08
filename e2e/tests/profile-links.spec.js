/*
 * Reaching another student's profile.
 *
 * /student/profile/<id> was built to be opened "by clicking them on the
 * leaderboard", but when those pages became React the rows stopped linking
 * anywhere. The rows kept their hover lift, so they still looked clickable —
 * the page just quietly did nothing, which is the kind of break no Python test
 * can see and no CI step rebuilds the bundle to catch.
 */
const { test, expect } = require("@playwright/test");

async function login(page, email = "browser-e2e@example.test") {
  await page.goto("/login");
  const preLoginConsent = page.locator('#cookie-consent:not([hidden]) [data-consent-choice="essential"]');
  if (await preLoginConsent.isVisible()) await preLoginConsent.click();
  await page.locator("#login-email").fill(email);
  await page.locator("#login-password").fill("e2e-password-123");
  await page.locator('form[action="/login"] button[type="submit"]').click();
  await expect(page).toHaveURL(/\/student(?:$|\/)/);
  const consent = page.locator('#cookie-consent:not([hidden]) [data-consent-choice="essential"]');
  if (await consent.isVisible()) await consent.click();
}

test("a student on the leaderboard opens from the board", async ({ page }) => {
  await login(page);
  await page.goto("/student/leaderboard");

  const rival = page.locator('a[href^="/student/profile/"]', { hasText: "Rival Student" }).first();
  await expect(rival).toBeVisible();

  await rival.click();

  await expect(page).toHaveURL(/\/student\/profile\/\d+$/);
  await expect(page.getByText("Rival Student").first()).toBeVisible();
});

test("a friend opens from the friends list without arming their action buttons", async ({ page }) => {
  await login(page);
  await page.goto("/student/friends");

  const row = page.locator(".fr-row", { hasText: "Rival Student" }).first();
  await expect(row).toBeVisible();

  // Eliminar / Reportar / Bloquear must stay outside the link, or the menu
  // would navigate away instead of opening.
  await expect(row.locator("a.fr-open button")).toHaveCount(0);

  await row.locator("a.fr-open").click();

  await expect(page).toHaveURL(/\/student\/profile\/\d+$/);
  await expect(page.getByText("Rival Student").first()).toBeVisible();
});
