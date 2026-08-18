"""Landing (v6) composition, motion and build contracts.

The landing is authored in `MachReach Landing.html` + `v5/`/`v6/` component
files and compiled by landing_build/build.mjs into bundle.min.js and
index.prod.html, which are committed. These tests pin the parts that are easy
to break silently: the authored source, the responsive rules, the intro
contract, and the fact that the served artifact is the built one.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "static" / "machreach_landing"

BUNDLE_VERSION = "landing-v6-2"

# Every component the entry HTML pulls in, in dependency order.
V6_SOURCES = [
    "v5/tweaks-panel.jsx", "v5/motion.jsx", "v5/logo.jsx",
    "v6/icons.jsx", "v6/fx.jsx", "v6/intro.jsx", "v6/plan.jsx",
    "v6/analytics.jsx", "v6/hero.jsx", "v6/features.jsx",
    "v6/sections.jsx", "v6/sections2.jsx", "v6/pricing.jsx",
]


def test_authored_sources_and_stylesheets_exist():
    """The build reads these by name; a rename would break it at build time,
    which is later than here."""
    for rel in V6_SOURCES + ["v6/theme.css", "v6/ui.css", "MachReach Landing.html"]:
        assert (LANDING / rel).is_file(), f"missing authored source: {rel}"


def test_entry_html_loads_every_component_it_needs():
    source = (LANDING / "MachReach Landing.html").read_text(encoding="utf-8")
    for rel in V6_SOURCES:
        assert f'src="{rel}"' in source, f"{rel} not referenced by the entry HTML"
    assert 'href="v6/theme.css"' in source
    assert 'href="v6/ui.css"' in source


def test_landing_is_responsive():
    """The layout collapses to one column on small screens rather than
    overflowing."""
    ui = (LANDING / "v6/ui.css").read_text(encoding="utf-8")
    assert "@media (max-width:1000px)" in ui
    assert "@media (max-width:640px)" in ui
    assert ".hero-grid{grid-template-columns:1fr" in ui
    # Feature and pricing grids stack on phones.
    assert ".feat-grid,.price-grid{grid-template-columns:1fr}" in ui


def test_hero_cta_offers_the_account_not_the_extension_on_phones():
    """Paid traffic lands on phones, where a Chrome extension cannot be
    installed at all. Under 768px the hero's primary button must offer the
    account instead, and the Canvas mark that sells the extension goes with
    it — otherwise the only above-the-fold call to action is one the visitor
    is unable to act on."""
    hero = (LANDING / "v6/hero.jsx").read_text(encoding="utf-8")
    assert 'className="hero-cta-full">Usar la extensión</span>' in hero
    assert 'className="hero-cta-short">Crear cuenta gratis</span>' in hero

    theme = (LANDING / "v6/theme.css").read_text(encoding="utf-8")
    assert ".hero-cta-short{display:none}" in theme
    assert "@media (max-width:768px)" in theme
    for rule in [".hero-cta-full{display:none}", ".hero-cta-short{display:inline}",
                 ".hero-cta-primary svg{display:none}"]:
        assert rule in theme, f"missing phone rule for the hero CTA: {rule}"


def test_stats_strip_hides_thin_numbers_behind_the_beta_band():
    """A stranger who arrives from an ad reads two-digit activity counts as an
    empty room. Below the floor the strip must not print a number at all — the
    beta band takes its place, which is true whatever the count is."""
    features = (LANDING / "v6/features.jsx").read_text(encoding="utf-8")
    floor = next(
        line for line in features.splitlines()
        if line.startswith("const STATS_MIN_STUDENTS")
    )
    assert int(floor.split("=")[1].strip(" ;")) >= 150, floor
    assert "live.students_week < STATS_MIN_STUDENTS" in features
    # Under the floor: the band, and no figure anywhere inside it.
    assert 'className="stats-beta pop in"' in features
    assert "Cada lunes el ranking vuelve a cero" in features

    ui = (LANDING / "v6/ui.css").read_text(encoding="utf-8")
    assert ".stats-beta{" in ui


def test_intro_is_remembered_and_respects_reduced_motion():
    """Returning and reduced-motion visitors skip directly to the page while
    the authored replay control can deliberately clear the completion gate."""
    intro = (LANDING / "v6/intro.jsx").read_text(encoding="utf-8")
    assert "function IntroV6" in intro
    assert 'localStorage.getItem("machreach:intro-complete")' in intro
    assert 'localStorage.setItem("machreach:intro-complete", "1")' in intro
    assert "prefers-reduced-motion: reduce" in intro

    source = (LANDING / "MachReach Landing.html").read_text(encoding="utf-8")
    assert 'localStorage.removeItem("machreach:intro-complete")' in source
    # Intro is on by default and its overlay clears the flag when it finishes.
    assert '"playIntro": true' in source
    assert 'document.body.dataset.intro = "done"' in source


def test_motion_primitives_are_defined_once_in_the_theme():
    """The v6 reveal vocabulary. Components reference these class names, so a
    rename in the stylesheet would silently leave elements invisible."""
    theme = (LANDING / "v6/theme.css").read_text(encoding="utf-8")
    for cls in [".rv{", ".rv-scale,.pop{", ".slap{", ".lmask{", ".draw{"]:
        assert cls in theme, f"missing motion primitive: {cls}"
    # Reduced motion neutralises all of them.
    assert "@media (prefers-reduced-motion:reduce)" in theme
    assert ".rv,.rv-scale,.pop,.slap{opacity:1;transform:none;filter:none}" in theme


def test_cta_sheen_stays_inside_buttons_in_dark_mode():
    """The animated sheen must never escape into the page as a floating
    rectangular highlight, especially against the dark paper background."""
    theme = (LANDING / "v6/theme.css").read_text(encoding="utf-8")
    assert ".btn-primary{background:var(--brand);color:var(--brand-fg);overflow:hidden;isolation:isolate}" in theme
    assert "--brand-fg:#241834" in theme
    assert "pointer-events:none" in theme
    assert '[data-theme="dark"] .btn-primary::after' in theme


def test_built_artifact_is_current_and_self_hosted():
    """index.prod.html must ship local React, not the CDN copies the authored
    file uses for development."""
    prod = (LANDING / "index.prod.html").read_text(encoding="utf-8")
    assert "unpkg.com" not in prod, "prod artifact still points at the CDN"
    assert "text/babel" not in prod, "prod artifact still needs the Babel transpiler"
    assert "/static/machreach_landing/vendor/react.production.min.js" in prod
    assert f"bundle.min.js?v={BUNDLE_VERSION}" in prod
    assert f'href="/static/machreach_landing/v6/theme.css?v={BUNDLE_VERSION}"' in prod
    assert f'href="/static/machreach_landing/v6/ui.css?v={BUNDLE_VERSION}"' in prod


def test_built_artifact_is_prerendered():
    """#root ships with real markup so the page is not blank before hydration."""
    prod = (LANDING / "index.prod.html").read_text(encoding="utf-8")
    assert '<div id="root"></div>' not in prod, "#root is empty — prerender failed"
    for marker in ["hero-panel", "plan-grid", "an-chart", "price-grid", "faq-item"]:
        assert marker in prod, f"section missing from prerendered markup: {marker}"


def test_public_landing_route_serves_the_built_artifact(client):
    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in body
    assert f"bundle.min.js?v={BUNDLE_VERSION}" in body
    assert "unpkg.com" not in body
    # v6 sections the nav anchors point at.
    for anchor in ['id="how"', 'id="pricing"', 'id="faq"', 'id="features"']:
        assert anchor in body, f"missing landing anchor: {anchor}"


def test_landing_route_logo_links_home_not_to_a_fragment(client):
    """The authored file uses href="#" for the in-page mock; the build rewrites
    it so the real site's logo navigates home."""
    body = client.get("/").get_data(as_text=True)
    assert 'href="#" class="logo"' not in body
    assert 'href="/" class="logo"' in body
