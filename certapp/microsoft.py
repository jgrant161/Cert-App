"""Sign in with Microsoft 365 (Microsoft Entra ID), for firm staff.

Only accounts in the firm's own tenant can sign in, and only if an
administrator has already added that email as a user. Multi-factor
authentication and password rules come from the firm's Microsoft 365 setup.

Settings (from the Entra app registration):
    CERTAPP_MS_TENANT_ID      Directory (tenant) ID
    CERTAPP_MS_CLIENT_ID      Application (client) ID
    CERTAPP_MS_CLIENT_SECRET  Client secret value
"""

from __future__ import annotations

import os


def configured() -> bool:
    return all(os.environ.get(k) for k in
               ("CERTAPP_MS_TENANT_ID", "CERTAPP_MS_CLIENT_ID", "CERTAPP_MS_CLIENT_SECRET"))


class MicrosoftLogin:
    def __init__(self, app_factory=None):
        self._factory = app_factory or self._msal_app
        self._app = None

    @staticmethod
    def _msal_app():
        import msal
        tenant = os.environ["CERTAPP_MS_TENANT_ID"]
        return msal.ConfidentialClientApplication(
            os.environ["CERTAPP_MS_CLIENT_ID"],
            authority=f"https://login.microsoftonline.com/{tenant}",
            client_credential=os.environ["CERTAPP_MS_CLIENT_SECRET"],
        )

    @property
    def app(self):
        if self._app is None:
            self._app = self._factory()
        return self._app

    def start(self, redirect_uri: str) -> dict:
        """Begin sign-in. Keep the returned flow in the session and send the user to flow['auth_uri']."""
        # The answer comes back as a normal redirect (query string) rather than a cross-site form post,
        # so the sign-in cookie (SameSite=Lax) arrives with it. State, nonce and PKCE protect the code.
        return self.app.initiate_auth_code_flow(scopes=[], redirect_uri=redirect_uri, prompt="select_account")

    def finish(self, flow: dict, query_params: dict) -> tuple[str | None, str | None]:
        """Complete sign-in. Returns (email, None) or (None, error message)."""
        try:
            result = self.app.acquire_token_by_auth_code_flow(flow, query_params)
        except ValueError:  # state mismatch or replayed callback
            return None, "The Microsoft sign-in could not be completed. Please try again."
        if "error" in result:
            return None, result.get("error_description") or "Microsoft sign-in was not completed."
        claims = result.get("id_token_claims") or {}
        tenant = os.environ.get("CERTAPP_MS_TENANT_ID", "")
        if tenant and claims.get("tid") and claims["tid"] != tenant:
            return None, "That Microsoft account is not part of the firm's organization."
        email = claims.get("preferred_username") or claims.get("email") or claims.get("upn")
        if not email:
            return None, "Microsoft did not return an email address for this account."
        return email.lower(), None
