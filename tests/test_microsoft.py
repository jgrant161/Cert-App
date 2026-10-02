"""Drive the real MSAL library offline to check the sign-in request we build."""

import json
from urllib.parse import parse_qs, urlsplit

import msal

from certapp.microsoft import MicrosoftLogin, configured

TENANT = "11111111-2222-3333-4444-555555555555"


class FakeResponse:
    def __init__(self, data):
        self.status_code = 200
        self.text = json.dumps(data)
        self.headers = {}

    def raise_for_status(self):
        pass


class FakeHttp:
    """Answers MSAL's discovery request the way Microsoft's endpoint would."""

    def get(self, url, **kw):
        base = f"https://login.microsoftonline.com/{TENANT}"
        return FakeResponse({
            "authorization_endpoint": f"{base}/oauth2/v2.0/authorize",
            "token_endpoint": f"{base}/oauth2/v2.0/token",
            "issuer": f"{base}/v2.0",
        })

    def post(self, url, **kw):
        raise AssertionError("no token request expected in this test")

    def close(self):
        pass


def test_configured_needs_all_three_settings(monkeypatch):
    monkeypatch.setenv("CERTAPP_MS_TENANT_ID", TENANT)
    monkeypatch.setenv("CERTAPP_MS_CLIENT_ID", "client-id")
    assert not configured()
    monkeypatch.setenv("CERTAPP_MS_CLIENT_SECRET", "secret")
    assert configured()


def test_sign_in_request_targets_firm_tenant():
    def factory():
        return msal.ConfidentialClientApplication(
            "client-id", authority=f"https://login.microsoftonline.com/{TENANT}",
            client_credential="secret", http_client=FakeHttp(), instance_discovery=False)

    flow = MicrosoftLogin(app_factory=factory).start("https://certs.firm.com/auth/microsoft/callback")
    url = urlsplit(flow["auth_uri"])
    params = parse_qs(url.query)
    assert url.netloc == "login.microsoftonline.com" and TENANT in url.path
    assert params["redirect_uri"] == ["https://certs.firm.com/auth/microsoft/callback"]
    assert params["prompt"] == ["select_account"]
    assert "openid" in params["scope"][0]
    assert params["code_challenge_method"] == ["S256"]      # PKCE protects the sign-in code
    assert flow["state"] and flow["nonce"]


def test_finish_checks_tenant_and_reads_email(monkeypatch):
    monkeypatch.setenv("CERTAPP_MS_TENANT_ID", TENANT)

    class App:
        def __init__(self, result):
            self.result = result

        def acquire_token_by_auth_code_flow(self, flow, params):
            if params.get("state") != flow["state"]:
                raise ValueError("state mismatch")
            return self.result

    ok = MicrosoftLogin(app_factory=lambda: App({"id_token_claims": {"tid": TENANT, "preferred_username": "Kim@Firm.com"}}))
    assert ok.finish({"state": "s"}, {"state": "s", "code": "c"}) == ("kim@firm.com", None)

    other = MicrosoftLogin(app_factory=lambda: App({"id_token_claims": {"tid": "someone-else", "preferred_username": "x@y.com"}}))
    assert other.finish({"state": "s"}, {"state": "s"})[1].startswith("That Microsoft account is not part")

    assert ok.finish({"state": "s"}, {"state": "forged"})[0] is None

    denied = MicrosoftLogin(app_factory=lambda: App({"error": "access_denied", "error_description": "User cancelled"}))
    assert denied.finish({"state": "s"}, {"state": "s"}) == (None, "User cancelled")
