const { test, expect } = require("@playwright/test");

async function login(page, email = "browser-e2e@example.test") {
  await page.goto("/login");
  await page.locator("#login-email").fill(email);
  await page.locator("#login-password").fill("e2e-password-123");
  await page.locator('form[action="/login"] button[type="submit"]').click();
  await expect(page).toHaveURL(/\/student(?:$|\/)/);
}

test("student can register and reaches email verification", async ({ page }) => {
  await page.goto("/register");
  await page.locator("#register-name").fill("New Browser Student");
  await page.locator("#register-email").fill("new-browser@example.test");
  await page.locator("#register-password").fill("browser-password-123");
  await page.locator("#register-password2").fill("browser-password-123");
  await page.locator('form[action="/register"] button[type="submit"]').click();

  await expect(page).toHaveURL(/\/verify-email-pending/);
  await expect(page.getByText("new-browser@example.test")).toBeVisible();
});

test("student can log in and create a manual course", async ({ page }) => {
  await login(page);
  await page.goto("/student/courses");
  await page.locator("#mc-code").fill("PHY-201");
  await page.locator("#mc-name").fill("Applied Physics");
  await page.getByRole("button", { name: "Agregar curso", exact: true }).click();

  await expect(page.getByText("Applied Physics", { exact: true })).toBeVisible();
  await expect(page.getByText("PHY-201", { exact: true })).toBeVisible();
});

test("AI generation completes through the web queue and worker", async ({ page }) => {
  await login(page);

  const result = await page.evaluate(async () => {
    const response = await fetch("/api/student/quizzes/generate-async", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_text: "Velocity is distance divided by time.",
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

test("paid plan sends the browser to hosted checkout", async ({ page }) => {
  await login(page);
  await page.route("https://checkout.lemonsqueezy.test/**", (route) => route.fulfill({
    status: 200,
    contentType: "text/html",
    body: "<title>Hosted checkout</title><h1>Hosted checkout</h1>",
  }));
  await page.goto("/student/shop");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Mejorar a Plus", exact: true }).click();

  await expect(page).toHaveURL("https://checkout.lemonsqueezy.test/buy/e2e-plus");
  await expect(page.getByRole("heading", { name: "Hosted checkout" })).toBeVisible();
});

test("student can permanently delete the account", async ({ page }) => {
  await login(page, "delete-e2e@example.test");
  await page.goto("/student/settings");
  await page.getByRole("button", { name: /eliminar mi cuenta|delete my account/i }).click();
  await page.locator('form[action="/settings/delete-account"] input[name="confirm"]').fill("DELETE");
  await page.locator('form[action="/settings/delete-account"] button[type="submit"]').click();

  await expect(page).toHaveURL("/");
  await page.goto("/login");
  await page.locator("#login-email").fill("delete-e2e@example.test");
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
