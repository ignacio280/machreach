from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "static" / "machreach_landing"


def test_mobile_landing_has_authored_composition_and_resilient_intro():
    css = (LANDING / "styles.css").read_text(encoding="utf-8")
    hero = (LANDING / "hero.jsx").read_text(encoding="utf-8")
    sections = (LANDING / "sections.jsx").read_text(encoding="utf-8")
    source = (LANDING / "MachReach Landing.html").read_text(encoding="utf-8")

    assert "mobile-leaderboard-demo" in sections
    assert ".mobile-leaderboard-demo" in css
    assert ".price-grid" in css and "grid-template-columns: 1fr" in css
    assert ".hero-anim-card" in css and "display: none" in css
    assert "Empezar" in source
    assert "machreach:intro-complete" in source
    assert "prefers-reduced-motion: reduce" in source
    assert "localStorage" in source
    assert "section .section-head" not in sections.split("const revealSelectors", 1)[1].split(
        "].join", 1
    )[0]
    assert "HeroLeaderboard" in hero


def test_public_landing_route_serves_the_mobile_artifact(client):
    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'name="viewport" content="width=device-width, initial-scale=1"' in body
    assert "landing-mobile-phone-15" in body
    assert ".hero-anim-card { display: none !important; }" in body
    assert ".mobile-leaderboard-demo" in body
    assert "nav-cta-short" in body
