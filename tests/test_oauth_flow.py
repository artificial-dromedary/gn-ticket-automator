"""The OAuth login handshake.

google-auth-oauthlib generates a PKCE code verifier inside authorization_url() and
keeps it on that Flow instance. /login and /oauth/callback build separate Flow
objects, so the verifier has to travel through the session — without it Google
rejects the token exchange with "(invalid_grant) Missing code verifier".
"""
import pytest

import main


@pytest.fixture
def client(monkeypatch):
    main.app.config["TESTING"] = True
    main.app.secret_key = "test-secret"
    monkeypatch.setitem(main.CONFIG, "GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setitem(main.CONFIG, "GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setitem(main.CONFIG, "OAUTH_REDIRECT_URI", "https://example.com/oauth/callback")
    monkeypatch.setattr(main, "CONFIG_VALID", True)
    return main.app.test_client()


def test_login_stores_the_pkce_verifier_in_the_session(client):
    response = client.get("/login")

    assert response.status_code == 200
    with client.session_transaction() as session:
        assert session.get("oauth_state")
        assert session.get("oauth_code_verifier"), "verifier must survive into the callback request"


def test_the_auth_url_actually_carries_a_pkce_challenge(client):
    """If Google is sent a challenge, it will demand the verifier back."""
    body = client.get("/login").get_data(as_text=True)

    assert "code_challenge=" in body


def test_the_callback_restores_the_verifier_onto_its_own_flow(client, monkeypatch):
    client.get("/login")
    with client.session_transaction() as session:
        state = session["oauth_state"]
        verifier = session["oauth_code_verifier"]

    seen = {}

    class FakeFlow:
        def __init__(self):
            self.redirect_uri = None
            self.code_verifier = None

        def fetch_token(self, authorization_response=None):
            seen["code_verifier"] = self.code_verifier

        @property
        def credentials(self):
            raise AssertionError("stop after fetch_token")

    monkeypatch.setattr(main, "create_flow", lambda: FakeFlow())

    client.get(f"/oauth/callback?code=abc123&state={state}")

    assert seen["code_verifier"] == verifier


def test_a_callback_with_a_mismatched_state_is_refused(client, monkeypatch):
    client.get("/login")

    monkeypatch.setattr(main, "create_flow",
                        lambda: pytest.fail("must not exchange a token on a bad state"))

    response = client.get("/oauth/callback?code=abc123&state=not-the-right-state")

    assert response.status_code == 302
    assert "login-error" in response.headers["Location"]


def test_a_callback_with_no_prior_login_is_refused(client, monkeypatch):
    monkeypatch.setattr(main, "create_flow",
                        lambda: pytest.fail("must not exchange a token without a stored state"))

    response = client.get("/oauth/callback?code=abc123&state=anything")

    assert response.status_code == 302


def test_a_callback_without_a_code_is_refused(client, monkeypatch):
    monkeypatch.setattr(main, "create_flow",
                        lambda: pytest.fail("must not build a flow without a code"))

    response = client.get("/oauth/callback")

    assert response.status_code == 302
