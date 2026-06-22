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
const BUNDLE_VERSION = "landing-plan-11";

const read = (f) => readFileSync(join(LANDING, f), "utf-8");
const html = readFileSync(SRC_HTML, "utf-8");
const babelScripts = [...html.matchAll(/<script type="text\/babel">([\s\S]*?)<\/script>/g)];

// --- 1. Gather source ------------------------------------------------------
const jsxFiles = ["icons.jsx", "tweaks-panel.jsx", "hero.jsx",
                  "features.jsx", "sections.jsx", "pricing.jsx"];
const inlinePrereqs = babelScripts
  .map((m) => m[1])
  .filter((code) => code.includes("const LOGO_COLORS") || code.includes("function IntroOverlay"));
let src = inlinePrereqs.join("\n\n") + "\n\n" + jsxFiles.map(read).join("\n\n");

// Extract the inline App bootstrap (last <script type="text/babel"> ... </script>)
const m = babelScripts[babelScripts.length - 1];
if (!m) throw new Error("Could not find inline App bootstrap script in source HTML");
let bootstrap = m[1];
// Drop the browser render call; we add a guarded hydrate/render at the end.
bootstrap = bootstrap.replace(
  /ReactDOM\.createRoot\(document\.getElementById\("root"\)\)\.render\(<App\/>\);/,
  "");
src += "\n\n" + bootstrap + `
;(function () {
  if (typeof document === "undefined") return;
  var el = document.getElementById("root");
  if (!el) return;
  var element = React.createElement(App);
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
const reactDomUMD = readFileSync(join(LANDING, "vendor", "react-dom.production.min.js"), "utf-8");

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

function inject(code) {
  const s = w.document.createElement("script");
  s.textContent = code;
  w.document.body.appendChild(s);
}
let prerendered = "";
try {
  inject(reactUMD);
  inject(reactDomUMD);
  inject(out.code);
  await new Promise((r) => setTimeout(r, 250)); // let React flush + effects run
  prerendered = w.document.getElementById("root").innerHTML;
} catch (e) {
  console.warn("Prerender failed, shipping empty #root (client will render):", e.message);
} finally {
  w.close();
}
console.log(`prerender: ${prerendered.length} chars captured`);

// --- 4. Emit index.prod.html ----------------------------------------------
let prod = html;
// Rewrite assets to absolute /static paths
prod = prod.replace('href="styles.css"', `href="${PREFIX}styles.css"`);
prod = prod.replace('href="#" className="logo"', 'href="/" className="logo"');
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
