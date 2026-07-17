def test_privacy_policy_describes_current_processors_rights_and_retention(client):
    response = client.get("/privacy")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "July 17, 2026" in body
    assert "Google Calendar" in body
    assert "data portability" in body.lower()
    assert "Retention and Deletion" in body
    assert "December 1, 2026" in body


def test_terms_explain_renewal_cancellation_failures_refunds_and_chilean_rights(client):
    response = client.get("/terms")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "auto-renew" in body
    assert "payment fails" in body
    assert "Coin packs" in body
    assert "right of withdrawal" in body
    assert "Chilean law" in body
