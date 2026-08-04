// Local-only build for the MachReach app-design preview pages.
//
// Mirrors build.mjs, but emits one artifact per app page instead of one landing:
//   1. Concatenate the shared toolkit (reused from the landing's v5/v6 files),
//      the app/ components, and exactly one pages/<id>.jsx bootstrap.
//   2. Transpile JSX -> React.createElement and minify -> <id>.bundle.min.js
//   3. Pre-render <App/> in jsdom for static markup.
//   4. Emit <id>.prod.html with prod React UMD + the bundle + that markup.
//
// These pages are static mocks served under /design/<id>. They are NOT the live
// student app; nothing here reads real user data.
//
// Output is committed; this script is NOT run at deploy time.

import { readFileSync, writeFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, basename } from "node:path";
import { transformSync } from "esbuild";
import { JSDOM } from "jsdom";

const __dirname = dirname(fileURLToPath(import.meta.url));
const STATIC = join(__dirname, "..", "static");
const LANDING = join(STATIC, "machreach_landing");
const APP = join(STATIC, "machreach_app");
const PREFIX = "/static/machreach_app/";
const LANDING_PREFIX = "/static/machreach_landing/";
const BUNDLE_VERSION = "app-v6-3";

// Shared with the landing so the design system has exactly one source of truth
// — theme.css in particular is edited in place and must not be forked.
const SHARED_JSX = ["v5/tweaks-panel.jsx", "v5/motion.jsx", "v5/logo.jsx", "v6/icons.jsx"];
const SHARED_CSS = ["v6/theme.css"];
const PAGE_DEPS = {
  amigos: {
    css: ["dash.css", "focus.css", "plan.css", "rank.css", "friends.css"],
    jsx: ["shell.jsx", "lock.jsx", "friends.jsx"],
  },
  analiticas: {
    css: ["dash.css", "focus.css", "plan.css", "courses.css", "analytics.css"],
    jsx: ["shell.jsx", "lock.jsx", "analytics.jsx"],
  },
  cuenta: { css: ["pubtop.css", "auth.css"], jsx: ["pubtop.jsx", "auth.jsx"], motion: false },
  cursos: {
    css: ["dash.css", "focus.css", "plan.css", "courses.css"],
    jsx: ["shell.jsx", "lock.jsx", "dash-left.jsx", "courses.jsx"],
  },
  dashboard: {
    css: ["dash.css", "focus.css"],
    jsx: ["shell.jsx", "lock.jsx", "dash-left.jsx", "dash-right.jsx"],
  },
  focus: {
    css: ["dash.css", "focus.css"],
    jsx: ["shell.jsx", "lock.jsx", "ambience.jsx", "focus.jsx"],
  },
  herramientas: {
    css: ["dash.css", "focus.css", "plan.css", "courses.css", "analytics.css", "shop.css", "friends.css", "study.css"],
    jsx: ["shell.jsx", "lock.jsx", "tools.jsx"],
  },
  notas: {
    css: ["dash.css", "focus.css", "plan.css", "courses.css", "analytics.css", "shop.css", "friends.css", "study.css"],
    jsx: ["shell.jsx", "lock.jsx", "notas.jsx"],
  },
  plan: {
    css: ["dash.css", "focus.css", "plan.css"],
    jsx: ["shell.jsx", "lock.jsx", "plan.jsx"],
  },
  perfil: {
    css: ["dash.css", "focus.css", "plan.css", "friends.css", "profile.css"],
    jsx: ["shell.jsx", "lock.jsx", "profile.jsx"],
  },
  "perfil-editar": {
    css: ["dash.css", "focus.css", "plan.css", "friends.css", "profile.css", "profedit.css"],
    jsx: ["shell.jsx", "lock.jsx", "profedit.jsx"],
  },
  ajustes: {
    css: ["dash.css", "study.css", "plan.css", "friends.css", "profile.css", "settings.css"],
    jsx: ["shell.jsx", "lock.jsx", "settings.jsx"],
  },
  setup: {
    css: ["dash.css", "profile.css", "pubtop.css", "setup.css"],
    jsx: ["pubtop.jsx", "setup.jsx"],
    motion: false,
  },
  ranking: {
    css: ["dash.css", "focus.css", "plan.css", "rank.css"],
    jsx: ["shell.jsx", "lock.jsx", "rank.jsx"],
  },
  reviews: {
    css: ["dash.css", "focus.css", "plan.css", "courses.css", "analytics.css", "shop.css", "friends.css", "study.css"],
    jsx: ["shell.jsx", "lock.jsx", "reviews.jsx"],
  },
  tienda: {
    css: ["dash.css", "focus.css", "plan.css", "analytics.css", "shop.css"],
    jsx: ["shell.jsx", "lock.jsx", "shop.jsx"],
  },
};

const readLanding = (f) => readFileSync(join(LANDING, f), "utf-8");
const readApp = (f) => readFileSync(join(APP, f), "utf-8");

const pages = readdirSync(join(APP, "pages"))
  .filter((f) => f.endsWith(".jsx"))
  .map((f) => basename(f, ".jsx"))
  .filter((id) => Object.hasOwn(PAGE_DEPS, id))
  .sort();

if (!pages.length) throw new Error("No page bootstraps found in machreach_app/pages");

// --- shared prerender scaffolding (built once, reused per page) -------------
const reactUMD = readFileSync(join(LANDING, "vendor", "react.production.min.js"), "utf-8");
const reactDomServerUMD = readFileSync(
  join(__dirname, "node_modules", "react-dom", "umd", "react-dom-server-legacy.browser.production.min.js"),
  "utf-8",
);

function prerender(code) {
  const dom = new JSDOM(
    '<!doctype html><html><head></head><body><div id="root"></div></body></html>',
    { runScripts: "dangerously", pretendToBeVisual: true });
  const w = dom.window;
  w.matchMedia = w.matchMedia || function () {
    return { matches: false, media: "", addEventListener() {}, removeEventListener() {},
             addListener() {}, removeListener() {}, onchange: null, dispatchEvent() { return false; } };
  };
  w.scrollTo = w.scrollTo || function () {};
  class _Observer { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } }
  w.IntersectionObserver = w.IntersectionObserver || _Observer;
  w.ResizeObserver = w.ResizeObserver || _Observer;
  // Seeded so committed HTML is reproducible across local and CI builds.
  let seed = 0x4d524150;
  w.Math.random = function () {
    seed = (seed + 0x6d2b79f5) | 0;
    let v = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    v = (v + Math.imul(v ^ (v >>> 7), 61 | v)) ^ v;
    return ((v ^ (v >>> 14)) >>> 0) / 4294967296;
  };
  const inject = (src) => { const s = w.document.createElement("script"); s.textContent = src; w.document.body.appendChild(s); };
  let out = "";
  try {
    w.__MACHREACH_CAPTURE_APP__ = true;
    inject(reactUMD);
    inject(reactDomServerUMD);
    inject(code);
    if (typeof w.__MACHREACH_APP__ !== "function") throw new Error("App was not exposed for prerendering");
    out = w.ReactDOMServer.renderToString(w.React.createElement(w.__MACHREACH_APP__));
  } catch (e) {
    console.warn(`  prerender failed, shipping empty #root (client will render): ${e.message}`);
  } finally {
    w.close();
  }
  return out;
}

for (const id of pages) {
  const deps = PAGE_DEPS[id];
  const sharedJsx = deps.motion === false
    ? SHARED_JSX.filter((f) => f !== "v5/motion.jsx")
    : SHARED_JSX;
  const sharedSrc = [
    ...sharedJsx.map(readLanding),
    ...deps.jsx.map((f) => readApp(join("app", f))),
  ].join("\n\n");
  const cssLinks = [
    ...SHARED_CSS.map((f) => `<link rel="stylesheet" href="${LANDING_PREFIX}${f}"/>`),
    ...deps.css.map((f) => `<link rel="stylesheet" href="${PREFIX}app/${f}"/>`),
  ].join("\n");
  const bootstrap = readApp(join("pages", `${id}.jsx`));
  const src = sharedSrc + "\n\n" + bootstrap + `
;(function () {
  if (typeof document === "undefined") return;
  var el = document.getElementById("root");
  if (!el) return;
  if (window.__MACHREACH_CAPTURE_APP__) { window.__MACHREACH_APP__ = App; return; }
  if (el.firstElementChild) { el.innerHTML = ""; }
  ReactDOM.createRoot(el).render(React.createElement(App));
})();`;

  const out = transformSync(src, {
    loader: "jsx", jsx: "transform",
    jsxFactory: "React.createElement", jsxFragment: "React.Fragment",
    minify: true, legalComments: "none",
  });
  writeFileSync(join(APP, `${id}.bundle.min.js`), out.code, "utf-8");

  const markup = prerender(out.code);

  // Title/sub are declared by the bootstrap; pull them out for <title>.
  const title = (bootstrap.match(/PAGE_TITLE\s*=\s*"([^"]+)"/) || [, id])[1];

  const html = `<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>MachReach — ${title}</title>
<meta name="robots" content="noindex"/>
<meta name="theme-color" content="#FFF6E9"/>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700;12..96,800&family=Nunito:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet"/>
${cssLinks}
</head>
<body>
<div id="root">${markup}</div>
<script src="${LANDING_PREFIX}vendor/react.production.min.js"></script>
<script src="${LANDING_PREFIX}vendor/react-dom.production.min.js"></script>
<script src="${PREFIX}${id}.bundle.min.js?v=${BUNDLE_VERSION}" defer></script>
</body>
</html>
`;
  writeFileSync(join(APP, `${id}.prod.html`), html, "utf-8");
  console.log(`${id}: bundle ${out.code.length}B, prerender ${markup.length} chars, html ${html.length}B`);
}

console.log(`built ${pages.length} app page(s): ${pages.join(", ")}`);
