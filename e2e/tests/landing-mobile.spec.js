const { test, expect } = require("@playwright/test");

test("landing uses its authored phone composition on real mobile emulation", async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith("mobile"), "mobile projects only");

  await page.goto("/");
  await page.evaluate(() => {
    sessionStorage.setItem("mr_splash_seen", "1");
    localStorage.setItem("machreach:intro-complete", "1");
  });
  await page.reload();
  await page.waitForFunction(() => document.body.dataset.intro === "done");
  await page.waitForTimeout(1800);

  const layout = await page.evaluate(() => {
    const rect = (selector) => {
      const element = document.querySelector(selector);
      const box = element?.getBoundingClientRect();
      return element && box
        ? { display: getComputedStyle(element).display, left: box.left, right: box.right, width: element.offsetWidth }
        : null;
    };
    const cols = (selector) => {
      const element = document.querySelector(selector);
      return element ? getComputedStyle(element).gridTemplateColumns : null;
    };
    return {
      viewport: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      heroPreview: rect(".hero-panel-wrap"),
      leaderboard: rect(".lb-arena"),
      priceCards: [...document.querySelectorAll(".price-grid > div")].map((element) => {
        const box = element.getBoundingClientRect();
        return { left: box.left, right: box.right, width: element.offsetWidth };
      }),
      featureCards: [...document.querySelectorAll(".feat-grid > div")].slice(0, 3).map((element) => {
        const box = element.getBoundingClientRect();
        return { left: box.left, right: box.right, width: element.offsetWidth };
      }),
      // The stats strip only renders once there are real figures worth
      // showing, so on a fresh database it is absent — and reading its columns
      // unconditionally threw before any assertion ran. Absent is a valid
      // composition; only the shape it has when present is interesting.
      statsColumns: cols(".stats"),
      howColumns: cols(".how"),
      quizColumns: cols(".quiz-grid"),
      footerColumns: cols(".foot-grid"),
      essentialHeadingsVisible: [...document.querySelectorAll("h1, section h2")].every((element) => {
        const style = getComputedStyle(element);
        const box = element.getBoundingClientRect();
        return style.opacity !== "0" && style.visibility !== "hidden" && box.width > 0 && box.height > 0;
      }),
      revealTransforms: [...new Set(
        [...document.querySelectorAll(".rv.in,.rv-scale.in,.pop.in,.slap.in")].map((element) => getComputedStyle(element).transform),
      )],
    };
  });

  expect(layout.viewport).toBeLessThanOrEqual(430);
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewport);
  expect(layout.heroPreview.display).not.toBe("none");
  expect(layout.heroPreview.width).toBeGreaterThan(layout.viewport - 60);
  expect(layout.leaderboard.display).not.toBe("none");
  expect(layout.leaderboard.width).toBeGreaterThan(layout.viewport - 60);
  expect(layout.priceCards).toHaveLength(2);
  expect(layout.priceCards.every((card) => card.width > layout.viewport - 60)).toBe(true);
  expect(
    layout.featureCards.every((card) => card.width > layout.viewport - 60),
    JSON.stringify(layout),
  ).toBe(true);
  // Two columns when the strip is on screen; nothing to check when the numbers
  // are too thin for it to appear at all.
  if (layout.statsColumns) expect(layout.statsColumns.split(" ")).toHaveLength(2);
  expect(layout.howColumns.split(" ")).toHaveLength(1);
  expect(layout.quizColumns.split(" ")).toHaveLength(1);
  expect(layout.footerColumns.split(" ")).toHaveLength(2);
  expect(layout.essentialHeadingsVisible).toBe(true);
  expect(
    layout.revealTransforms.every(
      (transform) => transform === "none" || transform === "matrix(1, 0, 0, 1, 0, 0)",
    ),
  ).toBe(true);
});

test("landing motion assembles cleanly without gimmick classes", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium", "one desktop engine covers motion choreography");

  await page.goto("/");
  await page.waitForFunction(() => document.body.dataset.intro === "done");

  await page.evaluate(() => window.scrollTo(0, Math.min(760, document.body.scrollHeight / 5)));
  await page.waitForTimeout(950);

  const motion = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    total: document.querySelectorAll(".rv,.rv-scale,.pop,.slap").length,
    visible: document.querySelectorAll(".rv.in,.rv-scale.in,.pop.in,.slap.in").length,
    oldGimmicks: document.querySelectorAll(
      ".motion-flip,.motion-deal,.motion-note,.motion-page,.motion-hotspot,.mri-ember,.mri-burst-p",
    ).length,
    clippedReveals: [...document.querySelectorAll(".rv.in,.rv-scale.in,.pop.in,.slap.in")].filter(
      (element) => !["none", "auto"].includes(getComputedStyle(element).clipPath),
    ).length,
    progress: getComputedStyle(document.querySelector("#prog i")).transform,
  }));

  expect(motion.scrollWidth).toBeLessThanOrEqual(motion.viewport);
  expect(motion.total).toBeGreaterThan(12);
  expect(motion.visible).toBeGreaterThan(0);
  expect(motion.oldGimmicks).toBe(0);
  expect(motion.clippedReveals).toBe(0);
  expect(motion.progress).not.toBe("none");
});
