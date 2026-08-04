"""The legal pages are served in Spanish, and must keep saying the things that
made them defensible: what is kept, for how long, and what a payment does."""


def test_privacy_policy_describes_current_processors_rights_and_retention(client):
    response = client.get("/privacy")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "28 de julio de 2026" in body
    assert "portabilidad" in body.lower()
    assert "Conservación y eliminación" in body
    assert "1 de diciembre de 2026" in body
    assert "mismo ramo canónico" in body
    assert "al menos cinco estudiantes" in body
    assert "Verificación de universidad para premios físicos" in body
    # Every processor still has to be named.
    for processor in ("OpenAI", "Canvas", "Lemon Squeezy", "Render", "Sentry", "PostHog"):
        assert processor in body


def test_terms_explain_renewal_cancellation_failures_refunds_and_chilean_rights(client):
    response = client.get("/terms")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "se renuevan automáticamente" in body
    assert "Si un pago falla" in body
    assert "Packs de monedas" in body
    assert "derecho de retracto" in body
    assert "ley chilena" in body.lower()
    assert "Ley 19.496" in body
    assert "no es prueba de matrícula" in body
    assert "verifique su matrícula o vínculo con la universidad" in body


def test_the_public_pages_are_in_spanish(client):
    """The audience is Chilean students; the English originals are gone."""
    for path, marker in [
        ("/about", "Sobre MachReach"),
        ("/privacy", "Política de Privacidad"),
        ("/terms", "Términos del Servicio"),
        ("/cookies", "Cookies esenciales"),
    ]:
        body = client.get(path).get_data(as_text=True)
        assert marker in body, path
        assert "Last updated" not in body


def test_the_retired_pages_are_gone(client):
    for path in ("/roadmap", "/blog", "/press", "/status"):
        assert client.get(path).status_code == 404

    sitemap = client.get("/sitemap.xml").get_data(as_text=True)
    for path in ("/roadmap", "/blog", "/press", "/status"):
        assert path not in sitemap


def test_the_error_pages_speak_spanish(client):
    body = client.get("/definitely-not-a-page").get_data(as_text=True)

    assert "Esta página se perdió" in body
    assert "Volver al inicio" in body
    assert "Back to home" not in body
