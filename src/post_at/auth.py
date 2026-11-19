"""Azure AD B2C authentication as performed by the Österreichische Post Android app.

The app uses MSAL against the custom policy ``B2C_1A_signup_signin_mobileapp``
(public client, authorization code + PKCE). The BFF accepts tokens for three
resources; all of them are minted from the same refresh token:

* :data:`SCOPE_APP` – sent as ``Authorization`` on every authenticated call.
* :data:`SCOPE_USER_CONTEXT` – additionally sent as ``X-Authorization`` for
  user profile and receiving-option endpoints.
* :data:`SCOPE_ID_AUSTRIA` – additionally sent as ``X-Authorization`` for the
  identification endpoints.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx

from .errors import AuthenticationError, InteractionRequiredError, NotAuthenticatedError

CLIENT_ID = "228c4360-3391-4b1c-93a2-e5d7d946b647"
REDIRECT_URI = f"msal{CLIENT_ID}://at.post.app/auth"
LOGIN_HOST = "https://login.post.at"
TENANT_ID = "f098c632-5a55-45ba-9bf4-c13870157cf1"
POLICY = "B2C_1A_signup_signin_mobileapp"
AUTHORITY = f"{LOGIN_HOST}/{TENANT_ID}/{POLICY}"
AUTHORIZE_URL = f"{AUTHORITY}/oauth2/v2.0/authorize"
TOKEN_URL = f"{AUTHORITY}/oauth2/v2.0/token"
LOGOUT_URL = f"{AUTHORITY}/oauth2/v2.0/logout"

SCOPE_APP = "https://login.post.at/postag/PostApp2.AllAccess"
SCOPE_USER_CONTEXT = "https://kiamprod.onmicrosoft.com/kiam-api/KIAM.Service.UserContext.All"
SCOPE_ID_AUSTRIA = "https://kiamprod.onmicrosoft.com/kiam-api/Kiam.Identifications.IdAustria.Create"

# MSAL always adds the OIDC scopes to whatever resource scope is requested.
_OIDC_SCOPES = "openid offline_access profile"
# Refresh this many seconds before the access token actually expires.
_EXPIRY_SKEW = 60
_BROWSER_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"
)
_SETTINGS_RE = re.compile(r"var SETTINGS = (\{.*?\});", re.S)


@dataclass
class AccessToken:
    token: str
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at - _EXPIRY_SKEW


@dataclass
class Session:
    """Persistable login state: the rotating refresh token plus cached access tokens."""

    refresh_token: str
    access_tokens: dict[str, AccessToken] = field(default_factory=dict)
    id_token: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "refresh_token": self.refresh_token,
            "id_token": self.id_token,
            "access_tokens": {
                scope: {"token": t.token, "expires_at": t.expires_at}
                for scope, t in self.access_tokens.items()
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        return cls(
            refresh_token=data["refresh_token"],
            id_token=data.get("id_token"),
            access_tokens={
                scope: AccessToken(t["token"], t["expires_at"])
                for scope, t in data.get("access_tokens", {}).items()
            },
        )

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.write_text(json.dumps(self.to_dict()))
        path.chmod(0o600)

    @classmethod
    def load(cls, path: str | Path) -> Session:
        return cls.from_dict(json.loads(Path(path).read_text()))

    @property
    def claims(self) -> dict[str, Any]:
        """Unverified claims of the ID token (name, email, ``extension_SSOID``, ...)."""
        if not self.id_token:
            return {}
        payload = self.id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


@dataclass(frozen=True)
class PendingLogin:
    """State of a browser login started with :meth:`Authenticator.begin_browser_login`."""

    url: str
    state: str
    code_verifier: str


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    return verifier, challenge.decode()


class Authenticator:
    """Obtains and refreshes B2C tokens. Thread-unsafe; one instance per session."""

    def __init__(
        self,
        http: httpx.Client,
        session: Session | None = None,
        on_session_change: Callable[[Session | None], None] | None = None,
    ) -> None:
        self._http = http
        self.session = session
        self._on_session_change = on_session_change

    # -- login ---------------------------------------------------------------

    def login(self, email: str, password: str) -> Session:
        """Headless login by replaying the B2C sign-in page's XHR calls."""
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(16)
        with httpx.Client(headers={"User-Agent": _BROWSER_UA}, timeout=self._http.timeout) as web:
            page = web.get(AUTHORIZE_URL, params=self._authorize_params(challenge, state), follow_redirects=True)
            settings = self._page_settings(page)
            tenant = LOGIN_HOST + settings["hosts"]["tenant"]
            tx = {"tx": settings["transId"], "p": settings["hosts"]["policy"]}

            submitted = web.post(
                f"{tenant}/SelfAsserted",
                params=tx,
                data={"request_type": "RESPONSE", "signInName": email, "password": password},
                headers={
                    "X-CSRF-TOKEN": settings["csrf"],
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": str(page.url),
                },
            )
            result = submitted.json()
            if result.get("status") != "200":
                raise AuthenticationError(f"{result.get('errorCode')}: {result.get('message')}")

            response = web.get(
                f"{tenant}/api/{settings['api']}/confirmed",
                params={"rememberMe": "false", "csrf_token": settings["csrf"], **tx},
            )
            for _ in range(10):
                location = response.headers.get("location")
                if not response.is_redirect or location is None:
                    raise InteractionRequiredError(
                        "B2C did not redirect back to the app after sign-in; "
                        "finish the login in a browser via begin_browser_login()"
                    )
                if location.startswith(REDIRECT_URI):
                    return self._redeem(location, state, verifier)
                response = web.get(urljoin(str(response.url), location))
        raise AuthenticationError("too many redirects during B2C login")

    def begin_browser_login(self) -> PendingLogin:
        """Start a login the user completes in a real browser (needed for MFA or new terms).

        Open ``url``, sign in, then pass the final ``msal…://at.post.app/auth?code=…``
        URL to :meth:`complete_browser_login`. Desktop browsers cannot open that
        scheme; copy it from the address bar/error page or the devtools network tab.
        """
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(16)
        url = httpx.URL(AUTHORIZE_URL, params=self._authorize_params(challenge, state))
        return PendingLogin(str(url), state, verifier)

    def complete_browser_login(self, pending: PendingLogin, redirect_url: str) -> Session:
        return self._redeem(redirect_url, pending.state, pending.code_verifier)

    def logout(self) -> None:
        """Forget the local session. B2C refresh tokens cannot be revoked by a public client."""
        self._set_session(None)

    # -- tokens --------------------------------------------------------------

    def access_token(self, scope: str = SCOPE_APP) -> str:
        """Return a valid access token for ``scope``, refreshing it if necessary."""
        if self.session is None:
            raise NotAuthenticatedError("not logged in")
        cached = self.session.access_tokens.get(scope)
        if cached is None or cached.expired:
            self._token_request(
                {"grant_type": "refresh_token", "refresh_token": self.session.refresh_token},
                scope,
            )
            cached = self.session.access_tokens[scope]
        return cached.token

    def invalidate(self, scope: str) -> None:
        """Drop a cached access token so the next call fetches a fresh one."""
        if self.session is not None:
            self.session.access_tokens.pop(scope, None)

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _authorize_params(challenge: str, state: str) -> dict[str, str]:
        return {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": f"{SCOPE_APP} {_OIDC_SCOPES}",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "nonce": secrets.token_urlsafe(16),
            "prompt": "login",
        }

    @staticmethod
    def _page_settings(page: httpx.Response) -> dict[str, Any]:
        page.raise_for_status()
        match = _SETTINGS_RE.search(page.text)
        if match is None:
            raise AuthenticationError(f"unexpected B2C login page at {page.url}")
        return json.loads(match.group(1))

    def _redeem(self, redirect_url: str, state: str, verifier: str) -> Session:
        query = parse_qs(urlsplit(redirect_url).query)
        if "error" in query:
            raise AuthenticationError(
                f"{query['error'][0]}: {query.get('error_description', [''])[0]}"
            )
        if query.get("state", [None])[0] != state:
            raise AuthenticationError("state mismatch in redirect URL")
        if "code" not in query:
            raise AuthenticationError("redirect URL contains no authorization code")
        self._token_request(
            {
                "grant_type": "authorization_code",
                "code": query["code"][0],
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
            },
            SCOPE_APP,
        )
        assert self.session is not None
        return self.session

    def _token_request(self, grant: dict[str, str], scope: str) -> None:
        response = self._http.post(
            TOKEN_URL,
            data={"client_id": CLIENT_ID, "scope": f"{scope} {_OIDC_SCOPES}", **grant},
        )
        body = response.json()
        if response.status_code != 200 or "access_token" not in body:
            raise AuthenticationError(
                f"token request failed ({response.status_code}): "
                f"{body.get('error')}: {body.get('error_description')}"
            )
        token = AccessToken(body["access_token"], time.time() + int(body["expires_in"]))
        if self.session is None:
            session = Session(body["refresh_token"], {scope: token}, body.get("id_token"))
        else:
            session = self.session
            session.refresh_token = body.get("refresh_token", session.refresh_token)
            session.id_token = body.get("id_token", session.id_token)
            session.access_tokens[scope] = token
        self._set_session(session)

    def _set_session(self, session: Session | None) -> None:
        self.session = session
        if self._on_session_change is not None:
            self._on_session_change(session)
