// Local-only build for the MachReach landing.
// 1. Concatenate the global-scope JSX (icons, tweaks-panel, hero, features,
//    sections, pricing) + the inline App bootstrap from the source HTML.
// 2. Transpile JSX -> React.createElement (classic, global React) and minify
//    with esbuild  ->  bundle.min.js
// 3. Pre-render <App/> in jsdom to get static markup for #root.
// 4. Emit index.prod.html: prod React UMD + bundle + pre-rendered #root,
//    hydrated on the client.
// Output (bundle.min.js, index.prod.html, vendor prod React) is committed;
// this script is NOT run at deploy time.

import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { transformSync } from "esbuild";
import { JSDOM } from "jsdom";

const __dirname = dirname(fileURLToPath(import.meta.url));
const LANDING = join(__dirname, "..", "static", "machreach_landing");
const SRC_HTML = join(LANDING, "MachReach Landing.html");
const PREFIX = "/static/machreach_landing/";
const BUNDLE_VERSION = "landing-v6-1";

// Stylesheets the source HTML links relatively; rewritten to absolute /static
// paths in the emitted prod file.
const STYLESHEETS = ["v6/theme.css", "v6/ui.css"];

const read = (f) => readFileSync(join(LANDING, f), "utf-8");
const html = readFileSync(SRC_HTML, "utf-8");
// Only matches inline <script type="text/babel"> blocks; the JSX files are
// loaded via src= and have an attribute before the ">", so they don't match.
const babelScripts = [...html.matchAll(/<script type="text\/babel">([\s\S]*?)<\/script>/g)];

// --- 1. Gather source ------------------------------------------------------
// Order matters: these are concatenated into one scope, so anything referenced
// at module level must come first. logo.jsx defines LOGO_COLORS/LOGO_PATHS,
// which intro.jsx reads, and motion.jsx defines the hooks every section uses.
const jsxFiles = [
  "v5/tweaks-panel.jsx",
  "v5/motion.jsx",
  "v5/logo.jsx",
  "v6/icons.jsx",
  "v6/fx.jsx",
  "v6/intro.jsx",
  "v6/plan.jsx",
  "v6/analytics.jsx",
  "v6/hero.jsx",
  "v6/features.jsx",
  "v6/sections.jsx",
  "v6/sections2.jsx",
  "v6/pricing.jsx",
];
let src = jsxFiles.map(read).join("\n\n");

// Extract the inline App bootstrap (last <script type="text/babel"> ... </script>)
const m = babelScripts[babelScripts.length - 1];
if (!m) throw new Error("Could not find inline App bootstrap script in source HTML");
let bootstrap = m[1];
// Drop the browser render call; we add a guarded hydrate/render at the end.
// Tolerant of `<App/>` and `<App />` — a missed match here is silent and would
// render the app twice (once immediately, once from the guarded call below).
const RENDER_CALL = /ReactDOM\.createRoot\(\s*document\.getElementById\("root"\)\s*\)\.render\(\s*<App\s*\/>\s*\);/;
if (!RENDER_CALL.test(bootstrap)) {
  throw new Error("Could not find the ReactDOM render call to strip from the bootstrap");
}
bootstrap = bootstrap.replace(RENDER_CALL, "");
src += "\n\n" + bootstrap + `
;(function () {
  if (typeof document === "undefined") return;
  var el = document.getElementById("root");
  if (!el) return;
  var element = React.createElement(App);
  if (window.__MACHREACH_CAPTURE_APP__) {
    window.__MACHREACH_APP__ = App;
    return;
  }
  if (el.firstElementChild) { el.innerHTML = ""; }
  ReactDOM.createRoot(el).render(element);
})();`;

// --- 2. Transpile + minify -------------------------------------------------
const out = transformSync(src, {
  loader: "jsx",
  jsx: "transform",
  jsxFactory: "React.createElement",
  jsxFragment: "React.Fragment",
  minify: true,
  legalComments: "none",
});
writeFileSync(join(LANDING, "bundle.min.js"), out.code, "utf-8");
console.log(`bundle.min.js written (${out.code.length} bytes)`);

// --- 3. Pre-render via jsdom ----------------------------------------------
const reactUMD = readFileSync(join(LANDING, "vendor", "react.production.min.js"), "utf-8");
const reactDomServerUMD = readFileSync(
  join(__dirname, "node_modules", "react-dom", "umd", "react-dom-server-legacy.browser.production.min.js"),
  "utf-8",
);

const dom = new JSDOM(
  '<!doctype html><html><head></head><body><div id="root"></div></body></html>',
  { runScripts: "dangerously", pretendToBeVisual: true });
const w = dom.window;
// Polyfills jsdom lacks that components may touch during mount.
w.matchMedia = w.matchMedia || function () {
  return { matches: false, media: "", addEventListener() {}, removeEventListener() {},
           addListener() {}, removeListener() {}, onchange: null, dispatchEvent() { return false; } };
};
w.scrollTo = w.scrollTo || function () {};
class _Observer { observe() {} unobserve() {} disconnect() {} takeRecords() { return []; } }
w.IntersectionObserver = w.IntersectionObserver || _Observer;
w.ResizeObserver = w.ResizeObserver || _Observer;

// Components use Math.random() for decorative animation values. Seed the
// prerender so committed HTML is reproducible across local and CI builds.
let prerenderSeed = 0x4d524348;
w.Math.random = function () {
  prerenderSeed = (prerenderSeed + 0x6d2b79f5) | 0;
  let value = Math.imul(prerenderSeed ^ (prerenderSeed >>> 15), 1 | prerenderSeed);
  value = (value + Math.imul(value ^ (value >>> 7), 61 | value)) ^ value;
  return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
};

function inject(code) {
  const s = w.document.createElement("script");
  s.textContent = code;
  w.document.body.appendChild(s);
}
let prerendered = "";
try {
  w.__MACHREACH_CAPTURE_APP__ = true;
  inject(reactUMD);
  inject(reactDomServerUMD);
  inject(out.code);
  if (typeof w.__MACHREACH_APP__ !== "function") {
    throw new Error("Landing App was not exposed for prerendering");
  }
  prerendered = w.ReactDOMServer.renderToString(
    w.React.createElement(w.__MACHREACH_APP__),
  );
} catch (e) {
  console.warn("Prerender failed, shipping empty #root (client will render):", e.message);
} finally {
  w.close();
}
console.log(`prerender: ${prerendered.length} chars captured`);

// --- 4. Emit index.prod.html ----------------------------------------------
let prod = html;
// Rewrite assets to absolute /static paths
for (const sheet of STYLESHEETS) {
  const before = prod;
  prod = prod.replace(`href="${sheet}"`, `href="${PREFIX}${sheet}"`);
  if (prod === before) throw new Error(`Stylesheet link not found in source HTML: ${sheet}`);
}
// The logo links to "#" in the authored file (it is an in-page mock). On the
// real site it must navigate home. This has to be applied to the *prerendered*
// markup — the authored `className="logo"` only ever appears inside the inline
// bootstrap, which is replaced by the prod script tags below, so rewriting the
// source text here would be a no-op.
prerendered = prerendered.replaceAll('href="#" class="logo"', 'href="/" class="logo"');
// Replace dev React/Babel + jsx script tags + inline bootstrap with prod scripts.
let scriptsStart = prod.indexOf('<script src="https://unpkg.com/react@');
if (scriptsStart === -1) scriptsStart = prod.indexOf('<script src="/static/machreach_landing/vendor/react.production.min.js">');
const scriptsEnd = prod.indexOf("</script>", prod.lastIndexOf('ReactDOM.createRoot')) + "</script>".length;
if (scriptsStart === -1 || scriptsEnd === -1) throw new Error("Could not locate script block to replace");
const prodScripts =
`<script src="${PREFIX}vendor/react.production.min.js"></script>
  <script src="${PREFIX}vendor/react-dom.production.min.js"></script>
  <script src="${PREFIX}bundle.min.js?v=${BUNDLE_VERSION}" defer></script>`;
prod = prod.slice(0, scriptsStart) + prodScripts + prod.slice(scriptsEnd);
// Inject pre-rendered markup into #root
prod = prod.replace('<div id="root"></div>', `<div id="root">${prerendered}</div>`);

// Footer links render as href="#" placeholders; patch them by label (runs
// after hydration, re-applying briefly to win against React's hydration).
const footerFix = `<script>
(function(){
  var links={"Features":"#features","C\\u00f3mo funciona":"#how","Precios":"#pricing","Roadmap":"/roadmap","Sobre":"/about","Blog":"/blog","Contacto":"mailto:support@machreach.com","Prensa":"/press","T\\u00e9rminos":"/terms","Privacidad":"/privacy","Cookies":"/cookies","Status":"/status"};
  function patch(){document.querySelectorAll("footer a").forEach(function(a){var t=(a.textContent||"").trim();if(links[t])a.setAttribute("href",links[t]);});}
  document.addEventListener("DOMContentLoaded",function(){patch();var n=0,id=setInterval(function(){patch();if(++n>20)clearInterval(id);},150);});
})();
</script>`;
prod = prod.replace("</body>", footerFix + "\n</body>");

writeFileSync(join(LANDING, "index.prod.html"), prod, "utf-8");
console.log(`index.prod.html written (${prod.length} bytes)`);
