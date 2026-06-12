# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""OAuth 2.0 Authorization-Code + PKCE client for native (CLI) applications.

Implements the RFC 8252 native-app flow: a loopback HTTP server on a random
port, browser redirect, PKCE (S256) code challenge, and code-for-token exchange.
Works against any OIDC-compliant issuer (Volcengine UserPool federating
Feishu / ByteDance-SSO / etc.); endpoints are read from the issuer's
``/.well-known/openid-configuration``.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from typing import Callable

from agentkit.auth._redact import redact
from agentkit.auth.errors import AuthError, NetworkError

DEFAULT_SCOPE = "openid profile email offline_access"
LOGIN_TIMEOUT = 300.0
_DISCOVERY_TIMEOUT = 15
_TOKEN_TIMEOUT = 30


def generate_pkce_pair() -> tuple[str, str]:
    """Return ``(verifier, challenge)`` for PKCE S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def discover(issuer: str, *, timeout: float = _DISCOVERY_TIMEOUT) -> dict:
    """Fetch the issuer's OIDC discovery document."""
    # Restrict the scheme before opening any URL — never let an issuer become
    # file:// / ftp:// / data:// or a schemeless/relative target (local-file SSRF).
    parsed = urllib.parse.urlsplit(issuer)
    if parsed.scheme not in ("https", "http"):
        raise AuthError(f"unsupported issuer URL scheme {parsed.scheme!r}; expected https:// (http only for loopback)")
    if not parsed.netloc:
        raise AuthError(f"issuer must be an absolute http(s) URL with a host, got {issuer!r}")
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        raw = urllib.request.urlopen(url, timeout=timeout).read()
    except urllib.error.URLError as exc:
        raise NetworkError(f"cannot reach issuer discovery ({url}): {exc.reason}") from exc
    doc = json.loads(raw)
    if not isinstance(doc, dict):
        raise AuthError(f"issuer {issuer} returned a non-object discovery document")
    return doc


@dataclass
class OAuthClient:
    """A public OAuth client bound to one issuer + client_id."""

    issuer: str
    client_id: str
    scope: str = DEFAULT_SCOPE
    _discovery: dict = field(default_factory=dict, repr=False)

    def _doc(self) -> dict:
        if not self._discovery:
            self._discovery = discover(self.issuer)
        return self._discovery

    def authorization_url(self, redirect_uri: str, state: str, challenge: str) -> str:
        endpoint = str(self._doc().get("authorization_endpoint") or "")
        if not endpoint:
            raise AuthError(f"issuer {self.issuer} does not advertise an authorization_endpoint")
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return endpoint + "?" + urllib.parse.urlencode(params)

    def _token_post(self, data: dict) -> dict:
        endpoint = str(self._doc().get("token_endpoint") or "")
        if not endpoint:
            raise AuthError(f"issuer {self.issuer} does not advertise a token_endpoint")
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(
            endpoint, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST"
        )
        try:
            raw = urllib.request.urlopen(req, timeout=_TOKEN_TIMEOUT).read()
        except urllib.error.HTTPError as exc:
            detail = redact(exc.read().decode("utf-8", "replace"))[:300]
            raise AuthError(f"token endpoint rejected the request: {detail}") from exc
        except urllib.error.URLError as exc:
            raise NetworkError(f"cannot reach token endpoint: {exc.reason}") from exc
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise AuthError("token endpoint returned a non-object body")
        return decoded

    def exchange_code(self, code: str, verifier: str, redirect_uri: str) -> dict:
        return self._token_post(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": self.client_id,
                "code_verifier": verifier,
            }
        )

    def refresh(self, refresh_token: str) -> dict:
        """RFC 6749 §6. Raises :class:`AuthError` when the grant is no longer valid."""
        return self._token_post(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.client_id,
            }
        )


_SUCCESS_HTML = (
    b"<html><body style='font-family:sans-serif;text-align:center;padding-top:80px'>"
    b"<h2>&#10003; Login complete</h2><p>You can close this tab and return to the terminal.</p>"
    b"</body></html>"
)


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence access logging
        pass

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        self.server.oauth_result = dict(urllib.parse.parse_qsl(parsed.query))  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_SUCCESS_HTML)
        self.server.oauth_done.set()  # type: ignore[attr-defined]


def run_loopback_login(
    client: OAuthClient,
    *,
    open_url: Callable[[str], object] | None = None,
    on_url: Callable[[str], None] | None = None,
    timeout: float = LOGIN_TIMEOUT,
) -> dict:
    """Full interactive login: loopback server → browser → code → token.

    Returns the raw token-endpoint response (``access_token`` / ``id_token`` /
    ``refresh_token`` / ``expires_in`` ...). ``on_url`` receives the authorize URL
    so headless users can open it manually.
    """
    server = http.server.HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    server.oauth_result = {}  # type: ignore[attr-defined]
    server.oauth_done = threading.Event()  # type: ignore[attr-defined]
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(24)
    url = client.authorization_url(redirect_uri, state, challenge)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if on_url:
            on_url(url)
        (open_url or webbrowser.open)(url)
        if not server.oauth_done.wait(timeout):  # type: ignore[attr-defined]
            raise AuthError(
                f"timed out waiting for the browser login ({int(timeout)}s).",
                hint="a proxy may be blocking the 127.0.0.1 callback; try "
                "NO_PROXY=127.0.0.1 or switch networks.",
            )
    finally:
        server.shutdown()
        server.server_close()

    result: dict = server.oauth_result  # type: ignore[attr-defined]
    if "error" in result:
        raise AuthError(
            f"the identity pool rejected the login: {redact(result.get('error', ''))}"
            + (f" — {redact(result.get('error_description', ''))}" if result.get("error_description") else "")
        )
    if result.get("state") != state:
        raise AuthError("OAuth state mismatch (possible CSRF) — aborting.")
    if "code" not in result:
        raise AuthError("the callback did not include an authorization code.")
    return client.exchange_code(result["code"], verifier, redirect_uri)
