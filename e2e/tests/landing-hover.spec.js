const { test, expect } = require("@playwright/test");

test("hero extension CTA only reacts inside its visible bounds", async ({ page }) => {
  await page.goto("/?cta-hover-contract=1");
  await page.waitForFunction(() => document.body.dataset.intro === "done");

  const cta = page.locator(".hero-cta .btn-primary");
  const box = await cta.boundingBox();
  expect(box).not.toBeNull();

  await cta.hover();
  const current = await cta.boundingBox();
  await page.mouse.move(current.x - 8, current.y + current.height / 2, { steps: 12 });
  await page.waitForTimeout(80);
  expect(await cta.evaluate((element) => element.matches(":hover"))).toBe(false);
  const elementAtLeft = await page.evaluate(
    ({ x, y }) => document.elementFromPoint(x, y)?.closest(".btn-primary")?.textContent?.trim() || null,
    { x: box.x - 28, y: box.y + box.height / 2 },
  );
  expect(elementAtLeft).toBeNull();
});
