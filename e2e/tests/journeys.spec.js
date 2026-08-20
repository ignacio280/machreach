const { test, expect } = require("@playwright/test");

async function login(page, email = "browser-e2e@example.test") {
  await page.goto("/login");
  const preLoginConsent = page.locator('#cookie-consent:not([hidden]) [data-consent-choice="essential"]');
  if (await preLoginConsent.isVisible()) await preLoginConsent.click();
  await page.locator("#login-email").fill(email);
  await page.locator("#login-password").fill("e2e-password-123");
  await page.locator('form[action="/login"] button[type="submit"]').click();
  await expect(page).toHaveURL(/\/student(?:$|\/)/);
  await page.waitForFunction(() => {
    const banner = document.getElementById("cookie-consent");
    return !banner || !banner.hidden || document.cookie.includes("cookie_consent=1");
  });
  const consent = page.locator('#cookie-consent:not([hidden]) [data-consent-choice="essential"]');
  if (await consent.isVisible()) await consent.click();
}

test("analytics consent dismisses without reloading and stays dismissed", async ({ page }) => {
  await page.goto("/login");
  const banner = page.locator("#cookie-consent");
  const allow = page.getByRole("button", { name: "Permitir analítica", exact: true });

  await expect(banner).toBeVisible();
  const desktopLayout = await banner.evaluate((element) => {
    const panel = element.querySelector(".mr-consent__panel").getBoundingClientRect();
    const buttons = [...element.querySelectorAll("button")].map((button) => button.getBoundingClientRect());
    return { panel: { width: panel.width, height: panel.height }, buttonHeights: buttons.map((button) => button.height) };
  });
  expect(desktopLayout.panel.width).toBeLessThanOrEqual(1040);
  expect(desktopLayout.buttonHeights.every((height) => height >= 44)).toBe(true);

  await page.setViewportSize({ width: 390, height: 844 });
  const mobileLayout = await banner.evaluate((element) => {
    const panel = element.querySelector(".mr-consent__panel").getBoundingClientRect();
    const buttons = [...element.querySelectorAll("button")].map((button) => button.getBoundingClientRect());
    return {
      panelWidth: panel.width,
      buttons: buttons.map((button) => ({ width: button.width, height: button.height, top: button.top })),
    };
  });
  expect(mobileLayout.panelWidth).toBeLessThanOrEqual(370);
  expect(mobileLayout.buttons.every((button) => button.width >= 330 && button.height >= 46)).toBe(true);
  expect(mobileLayout.buttons[0].top).toBeGreaterThan(mobileLayout.buttons[1].top);

  await page.evaluate(() => { window.__consentPageMarker = "same-document"; });
  await allow.click();

  await expect(banner).toBeHidden();
  await expect.poll(() => page.evaluate(() => window.__consentPageMarker)).toBe("same-document");
  await expect.poll(() => page.evaluate(() => document.cookie)).toContain("cookie_consent=1");
  await expect.poll(() => page.evaluate(() => document.cookie)).toContain("analytics_consent=1");

  await page.goto("/login?consent_check=1", { waitUntil: "commit" });
  await expect(page.locator("#login-email")).toBeVisible();
  await expect(banner).toBeHidden();
});

test("student can register and reaches email verification", async ({ page }, testInfo) => {
  const email = `new-browser-${testInfo.project.name}@example.test`;
  await page.goto("/register");
  const consent = page.locator('#cookie-consent:not([hidden]) [data-consent-choice="essential"]');
  if (await consent.isVisible()) await consent.click();
  await page.locator("#register-name").fill("New Browser Student");
  await page.locator("#register-email").fill(email);
  await page.locator("#register-password").fill("browser-password-123");
  await page.locator("#register-password2").fill("browser-password-123");
  await page.locator('form[action="/register"] button[type="submit"]').click();

  await expect(page).toHaveURL(/\/verify-email-pending/);
  await expect(page.getByText(email)).toBeVisible();
});

test("student can log in and create a manual course", async ({ page }, testInfo) => {
  await login(page);
  await page.goto("/student/courses");
  const suffix = testInfo.project.name;
  await page.getByRole("button", { name: "Agregar a mano", exact: true }).click();
  await page.locator("#mc-code").fill(`PHY-${suffix}`);
  await page.locator("#mc-name").fill(`Applied Physics ${suffix}`);
  await page.getByRole("button", { name: "Agregar curso", exact: true }).click();

  await expect(page.getByText(`Applied Physics ${suffix}`, { exact: true })).toBeVisible();
  await expect(page.getByText(`PHY-${suffix}`, { exact: true })).toBeVisible();
});

test("course details open from the course card", async ({ page }, testInfo) => {
  await login(page);
  await page.goto("/student/courses");
  const courseName = `Course Detail ${testInfo.project.name}`;
  await page.getByRole("button", { name: "Agregar a mano", exact: true }).click();
  await page.locator("#mc-code").fill(`DETAIL-${testInfo.project.name}`);
  await page.locator("#mc-name").fill(courseName);
  await page.getByRole("button", { name: "Agregar curso", exact: true }).click();

  const card = page.locator(".ccard", { hasText: courseName });
  const details = card.locator(".cdet");
  await expect(details).toHaveCount(0);
  const detailsButton = card.getByRole("button", { name: /ver detalles/i });
  await expect(detailsButton).toBeVisible();
  // WebKit can treat the global entrance transform as continuously unstable
  // while cards are re-rendered; dispatch the same native button click after
  // visibility so this journey tests the CSP-bound handler itself.
  await expect.poll(async () => {
    if (await details.isHidden()) {
      await detailsButton.evaluate((button) => button.click());
    }
    return details.isVisible();
  }).toBe(true);
});

test("semester controls remain usable", async ({ page }) => {
  await login(page);
  await page.goto("/student/courses");

  // This asserted twelve buttons in the header, which was true only while the
  // page listed every semester up front. Semesters now appear there once the
  // student actually has more than one, so a fresh account shows none — and
  // the count told us nothing about the thing being tested anyway. What this
  // journey is really for is that the React handlers survive the enforced CSP,
  // so it drives the control that is always there and checks it did something.
  const correct = page.locator(".sem .sem-fix");
  await expect(correct).toBeVisible();
  await correct.click();

  const picker = page.locator(".sem-grid .sem-pick");
  await expect(picker.first()).toBeVisible();
  // I through XII, the full span a degree can run to.
  await expect(picker).toHaveCount(12);

  // Picking a semester marks it, which only happens if the handler ran.
  const target = picker.nth(2);
  await target.click();
  await expect(target).toHaveClass(/on/);
});

test("grade-sheet controls work under the enforced CSP", async ({ page }) => {
  await login(page);
  await page.goto("/student/gpa");
  if (await page.locator(".nt-course").count() === 0) {
    await page.getByRole("button", { name: /Agregar ramo/ }).click();
  }
  if (await page.locator(".grade").count() === 0) {
    await page.locator(".nt-course").first().getByRole("button", { name: /Evaluación/ }).click();
  }
  await page.locator(".nt-name").first().fill("CSP-safe Calculus");
  await page.locator(".grade").first().fill("6.5");

  const saved = await page.evaluate(() => {
    const key = Object.keys(localStorage).find((item) => item.startsWith("mr_planilla_v1_"));
    return key ? JSON.parse(localStorage.getItem(key)) : null;
  });
  expect(saved.sems[saved.current].courses[0].name).toBe("CSP-safe Calculus");
  expect(saved.sems[saved.current].courses[0].evals[0].grade).toBe("6.5");
});

test("AI generation completes through the web queue and worker", async ({ page }) => {
  await login(page);

  const result = await page.evaluate(async () => {
    const response = await fetch("/api/student/quizzes/generate-async", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        // Long enough to clear the "is there anything to build questions
        // from?" floor: a shorter string is treated as an empty document.
        source_text:
          "Velocity is distance divided by time, and acceleration is the rate " +
          "at which velocity changes. Newton's second law relates the net force " +
          "on a body to its mass and its acceleration. Momentum is conserved in " +
          "an isolated system, which is why a collision can be solved without " +
          "knowing the forces acting during the impact.",
        title: "Physics foundations",
        count: 5,
      }),
    });
    return { status: response.status, body: await response.json() };
  });

  expect(result.status).toBe(200);
  expect(result.body.queued).toBe(true);
  expect(result.body.quiz_status.status).toBe("queued");

  const completed = await page.evaluate(async () => {
    await fetch("/__e2e__/run-quiz-worker", { method: "POST" });
    const status = await fetch("/api/student/quizzes/generate/status");
    const quizzes = await fetch("/api/student/quizzes");
    return {
      status: await status.json(),
      quizzes: (await quizzes.json()).quizzes,
    };
  });

  expect(completed.status.status).toBe("done");
  expect(completed.status.question_count).toBe(1);
  expect(completed.quizzes.some((quiz) => quiz.title === "Physics foundations")).toBe(true);
});

test("paid plan sends the browser to hosted checkout", async ({ page }, testInfo) => {
  await login(page, `checkout-e2e-${testInfo.project.name}@example.test`);
  await page.route("https://checkout.lemonsqueezy.test/**", (route) => route.fulfill({
    status: 200,
    contentType: "text/html",
    body: "<title>Hosted checkout</title><h1>Hosted checkout</h1>",
  }));
  await page.goto("/student/shop");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Desbloquear Plus", exact: true }).click();

  await expect(page).toHaveURL("https://checkout.lemonsqueezy.test/buy/e2e-plus");
  await expect(page.getByRole("heading", { name: "Hosted checkout" })).toBeVisible();
});

test("shop product cards use dark surfaces in dark mode", async ({ page }) => {
  await login(page);
  await page.goto("/student/shop");
  await page.evaluate(() => {
    document.documentElement.dataset.theme = "dark";
  });

  const surfaces = await page.locator(".plan, .pack, .cos").evaluateAll((elements) =>
    elements.slice(0, 8).map((element) => getComputedStyle(element).backgroundColor),
  );

  expect(surfaces.length).toBeGreaterThan(1);
  for (const color of surfaces) {
    const channels = color.match(/\d+(?:\.\d+)?/g).slice(0, 3).map(Number);
    expect(Math.max(...channels)).toBeLessThan(90);
  }

  const featureColor = await page.locator(".plan li").first().evaluate(
    (element) => getComputedStyle(element).color,
  );
  const featureChannels = featureColor.match(/\d+(?:\.\d+)?/g).slice(0, 3).map(Number);
  expect(featureChannels.reduce((total, channel) => total + channel, 0)).toBeGreaterThan(600);
});

test("student can permanently delete the account", async ({ page }, testInfo) => {
  const email = `delete-e2e-${testInfo.project.name}@example.test`;
  await login(page, email);
  await page.goto("/student/settings");

  // Settings is tabbed now and opens on the profile tab, so the deletion form
  // is not on screen at all until "Cuenta" is picked. On top of that the page
  // went Spanish and the form gained a native confirm(): this looked for a
  // button named "eliminar mi cuenta", clicked it before filling the
  // confirmation, and never answered the dialog. Open the tab, type the word,
  // submit, then agree.
  await page.getByRole("button", { name: "Cuenta", exact: true }).click();

  const form = page.locator('form[action="/settings/delete-account"]');
  await form.locator('input[name="confirm"]').fill("ELIMINAR");
  // A standing handler rather than a one-shot, and the navigation awaited
  // alongside the click: on WebKit the confirm() and the submit that follows it
  // raced the assertion, leaving the page on /student/settings often enough to
  // show up as a flake.
  page.on("dialog", (dialog) => dialog.accept());
  await Promise.all([
    page.waitForURL("/"),
    form.getByRole("button", { name: /eliminar cuenta/i }).click(),
  ]);
  await page.goto("/login");
  await page.locator("#login-email").fill(email);
  await page.locator("#login-password").fill("e2e-password-123");
  await page.getByRole("button", { name: /iniciar sesi[oó]n|sign in/i }).click();
  await expect(page).toHaveURL(/\/login$/);
});

test("login and course pages fit a mobile viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/login");
  await expect(page.locator("#login-email")).toBeVisible();
  await expect.poll(() => page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth + 1,
  )).toBe(true);

  await login(page);
  await page.goto("/student/courses");
  await expect(page.locator("#manual-course-panel")).toBeVisible();
  await expect.poll(() => page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth + 1,
  )).toBe(true);
});
