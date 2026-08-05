"""Rate limits sized for a campus, with brute force fenced off per account.

Limits are counted per IP, and a university shares one. A budget that suits a
single person locks out a lecture hall — which on launch day reads as "the app
is down", not "you are going too fast".
"""
import app as appmod


def test_the_budgets_fit_a_shared_network():
    def per_minute(value):
        return int(value.split()[0])

    assert per_minute(appmod.RATE_DEFAULT) >= 1000
    assert per_minute(appmod.RATE_LOGIN) >= 30
    assert per_minute(appmod.RATE_REGISTER) >= 30
    # The per-account limit is the one that stays tight.
    assert per_minute(appmod.RATE_LOGIN_ACCOUNT) <= 12


def test_every_limit_can_be_widened_without_a_deploy(monkeypatch):
    monkeypatch.setenv("RATELIMIT_LOGIN", "99 per minute")

    assert appmod._rate("LOGIN", "40 per minute") == "99 per minute"
    assert appmod._rate("NOT_SET_ANYWHERE", "7 per minute") == "7 per minute"


def test_guessing_one_password_is_stopped_regardless_of_the_network(client, flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)

    codes = [
        client.post("/login", data={"email": "victim@limits.test", "password": f"g{i}"}).status_code
        for i in range(14)
    ]

    assert 429 in codes, "an account can be brute forced"
    assert codes.index(429) <= 12


def test_a_classmate_is_not_punished_for_someone_elses_attempts(client, flask_app, monkeypatch):
    """The whole point: one person failing must not lock out the building."""
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    for i in range(14):
        client.post("/login", data={"email": "loud@limits.test", "password": f"g{i}"})

    mine = client.post("/login", data={"email": "quiet@limits.test", "password": "x"})

    assert mine.status_code != 429


def test_the_rate_limit_page_explains_itself(client, flask_app, monkeypatch):
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)
    for i in range(14):
        client.post("/login", data={"email": "wall@limits.test", "password": f"g{i}"})

    blocked = client.post("/login", data={"email": "wall@limits.test", "password": "x"})
    body = blocked.get_data(as_text=True)

    assert blocked.status_code == 429
    assert "Vamos demasiado rápido" in body
    assert "Espera un minuto" in body
    assert "no perdiste nada" in body


def test_the_email_endpoints_stay_stingy_per_address(client, flask_app, monkeypatch):
    """These each send a real email; the address, not the network, is the cap."""
    monkeypatch.setitem(flask_app.config, "WTF_CSRF_ENABLED", False)

    codes = [
        client.post("/forgot-password", data={"email": "spam@limits.test"}).status_code
        for _ in range(6)
    ]

    assert 429 in codes
