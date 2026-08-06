const { test, expect } = require("@playwright/test");

async function login(page) {
  // The tab bar is fixed to the bottom, and Playwright scrolls an element into
  // view before clicking it. With smooth scrolling on, that scroll animates,
  // the fixed bar's box moves every frame, and the click waits forever for it
  // to hold still. Asking for reduced motion settles the page — and is the
  // path a real user with that preference gets anyway.
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/login");
  const consent = page.locator('#cookie-consent:not([hidden]) [data-consent-choice="essential"]');
  if (await consent.isVisible()) await consent.click();
  await page.locator("#login-email").fill("browser-e2e@example.test");
  await page.locator("#login-password").fill("e2e-password-123");
  await page.locator('form[action="/login"] button[type="submit"]').click();
  await expect(page).toHaveURL(/\/student(?:$|\/)/);

  // A first visit opens the welcome tour, whose card sits over the tab bar:
  // elementFromPoint on the "Más" button returned .tour-card, so the click
  // spent its timeout against an overlay. Dismiss it the way a student does.
  // It mounts through a portal a moment after the dashboard settles, so this
  // waits for it rather than looking once and moving on.
  const tour = page.locator(".tour-card");
  await tour.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  if (await tour.count()) {
    await page.locator(".tour-skip").click();
    await expect(tour).toHaveCount(0);
  }
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
  // Focus is fenced off twice, and which fence fires depends on how the phone
  // presents itself: the server turns a mobile User-Agent away before the app
  // renders (.focus-mobile-locked), and the page itself blocks a narrow or
  // coarse-pointer viewport (.focus-mobile-blocker). This asserted only the
  // second, so a real phone — which the server catches first — read as a
  // failure. What matters is that the timer is unreachable, by either route.
  await expect(
    page.locator(".focus-mobile-locked, .focus-mobile-blocker"),
  ).toBeVisible();
  await expect(page.locator(".fx-start")).toHaveCount(0);
});
