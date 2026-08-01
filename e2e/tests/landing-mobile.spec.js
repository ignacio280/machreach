const { test, expect } = require("@playwright/test");

test("landing uses its authored phone composition on real mobile emulation", async ({ page }, testInfo) => {
  test.skip(!testInfo.project.name.startsWith("mobile"), "mobile projects only");

  await page.goto("/");
  await page.evaluate(() => {
    sessionStorage.setItem("mr_splash_seen", "1");
    localStorage.setItem("machreach:intro-complete", "1");
  });
  await page.reload();

  const layout = await page.evaluate(() => {
    const rect = (selector) => {
      const element = document.querySelector(selector);
      const box = element?.getBoundingClientRect();
      return element && box
        ? { display: getComputedStyle(element).display, left: box.left, right: box.right, width: box.width }
        : null;
    };
    return {
      viewport: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
      heroPreview: rect(".hero-anim-card"),
      mobileLeaderboard: rect(".mobile-leaderboard-demo"),
      desktopLeaderboard: rect(".lb-wrap"),
      priceCards: [...document.querySelectorAll(".price-grid > div")].map((element) => {
        const box = element.getBoundingClientRect();
        return { left: box.left, right: box.right, width: box.width };
      }),
      featureCards: [...document.querySelectorAll(".feat-grid > div")].slice(0, 3).map((element) => {
        const box = element.getBoundingClientRect();
        return { left: box.left, right: box.right, width: box.width };
      }),
      statsColumns: getComputedStyle(document.querySelector(".stats-grid")).gridTemplateColumns,
      howColumns: getComputedStyle(document.querySelector(".how-grid")).gridTemplateColumns,
      quizColumns: getComputedStyle(document.querySelector(".quiz-wrap")).gridTemplateColumns,
      footerColumns: getComputedStyle(document.querySelector(".foot-grid")).gridTemplateColumns,
      essentialHeadingsVisible: [...document.querySelectorAll("h1, section h2")].every((element) => {
        const style = getComputedStyle(element);
        const box = element.getBoundingClientRect();
        return style.opacity !== "0" && style.visibility !== "hidden" && box.width > 0 && box.height > 0;
      }),
      revealTransforms: [...new Set(
        [...document.querySelectorAll(".motion-reveal")].map((element) => getComputedStyle(element).transform),
      )],
    };
  });

  expect(layout.viewport).toBeLessThanOrEqual(430);
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewport);
  expect(layout.heroPreview.display).toBe("none");
  expect(layout.desktopLeaderboard.display).toBe("none");
  expect(layout.mobileLeaderboard.display).not.toBe("none");
  expect(layout.mobileLeaderboard.width).toBeGreaterThan(layout.viewport - 60);
  expect(layout.priceCards).toHaveLength(2);
  expect(layout.priceCards.every((card) => card.width > layout.viewport - 60)).toBe(true);
  expect(
    layout.featureCards.every((card) => card.width > layout.viewport - 60),
    JSON.stringify(layout),
  ).toBe(true);
  expect(layout.statsColumns.split(" ")).toHaveLength(2);
  expect(layout.howColumns.split(" ")).toHaveLength(1);
  expect(layout.quizColumns.split(" ")).toHaveLength(1);
  expect(layout.footerColumns.split(" ")).toHaveLength(1);
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
    total: document.querySelectorAll(".motion-reveal").length,
    visible: document.querySelectorAll(".motion-reveal.is-visible").length,
    oldGimmicks: document.querySelectorAll(
      ".motion-flip,.motion-deal,.motion-note,.motion-page,.motion-hotspot,.mri-ember,.mri-burst-p",
    ).length,
    clippedReveals: [...document.querySelectorAll(".motion-reveal.is-visible")].filter(
      (element) => !["none", "auto"].includes(getComputedStyle(element).clipPath),
    ).length,
    progress: getComputedStyle(document.getElementById("landing-progress-bar")).transform,
    paperShift: getComputedStyle(document.documentElement).getPropertyValue("--landing-paper-y").trim(),
  }));

  expect(motion.scrollWidth).toBeLessThanOrEqual(motion.viewport);
  expect(motion.total).toBeGreaterThan(12);
  expect(motion.visible).toBeGreaterThan(0);
  expect(motion.oldGimmicks).toBe(0);
  expect(motion.clippedReveals).toBe(0);
  expect(motion.progress).not.toBe("none");
  expect(motion.paperShift).not.toBe("0px");
});
