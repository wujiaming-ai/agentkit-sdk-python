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

"""High-level SSO orchestration: ``login`` / ``load_session`` / ``logout`` / ``whoami``.

This is the public entry point for both the CLI and any developer program.
"""

from __future__ import annotations

from typing import Callable

from agentkit.auth import store
from agentkit.auth.errors import AuthError
from agentkit.auth.oauth import OAuthClient, run_loopback_login
from agentkit.auth.profile import (
    AuthProfile,
    active_profile_name,
    clear_active_profile,
    load_profile,
    save_profile,
    set_active_profile,
)
from agentkit.auth.session import AuthSession, StsCredentials
from agentkit.auth.ssl_trust import harden_default_ssl_context
from agentkit.auth.sts import assume_role_with_oidc, get_caller_identity


def login(
    profile: AuthProfile | str | None = None,
    *,
    address: str | None = None,
    duration_seconds: int = 3600,
    open_url: Callable[[str], object] | None = None,
    on_url: Callable[[str], None] | None = None,
    harden_ssl: bool = True,
) -> AuthSession:
    """Run an interactive browser SSO login and return a persisted AuthSession.

    Resolution of the login target:

    * ``address`` set — discover the profile from the UserPool's published
      ``/.well-known/agentkit-cli`` (the end-user types only the address);
    * else ``profile`` — an :class:`AuthProfile` or a named profile;
    * else — the active profile (the last ``agentkit login``), or ``"default"``.

    On success the resolved profile is saved and marked **active** (a pointer file
    every later command reads), and the session (refresh token + STS credentials)
    is written to the secure store — so subsequent commands need no env vars.
    """
    if harden_ssl:
        harden_default_ssl_context()
    if address:
        from agentkit.auth.resolve import resolve_profile

        prof = resolve_profile(address, harden_ssl=False)
        save_profile(prof)
    elif isinstance(profile, AuthProfile):
        prof = profile
    else:
        prof = load_profile(profile)
    prof.validate()

    client = OAuthClient(prof.issuer, prof.client_id, scope=prof.scope)
    token = run_loopback_login(client, open_url=open_url, on_url=on_url)
    id_token = str(token.get("id_token") or token.get("access_token") or "")
    if not id_token:
        raise AuthError("login did not return an id_token from the IdP.")
    refresh_token = token.get("refresh_token")

    assumed = assume_role_with_oidc(
        id_token, prof.role_trn, prof.provider_trn, duration_seconds=duration_seconds
    )
    account = None
    try:
        ident = get_caller_identity(
            assumed.access_key_id, assumed.secret_access_key, assumed.session_token,
            region=prof.region,
        )
        account = ident.get("AccountId")
    except Exception:
        pass  # non-fatal — identity lookup is informational

    from agentkit.auth.session import _effective_expiry

    sts = StsCredentials(
        access_key=assumed.access_key_id,
        secret_key=assumed.secret_access_key,
        session_token=assumed.session_token,
        expires_at=_effective_expiry(assumed.expired_at, duration_seconds),
        account_id=account,
    )
    session = AuthSession(
        prof,
        refresh_token=refresh_token,
        sts=sts,
        duration_seconds=duration_seconds,
        # Persist the OIDC tokens so the data plane (`agentkit invoke harness`) can use
        # the id_token as its inbound Bearer credential. Store the real id_token (not the
        # access_token fallback used above for AssumeRoleWithOIDC).
        id_token=token.get("id_token"),
        access_token=token.get("access_token"),
    )
    session.save()
    # Mark this profile active so separate CLI invocations (e.g. `sandbox create`)
    # resolve the same session with no env var and no user action.
    set_active_profile(prof.name)
    return session


def load_session(profile: str | None = None) -> AuthSession | None:
    """Load a persisted session for a profile, or ``None`` if not logged in."""
    try:
        prof = load_profile(profile)
    except AuthError:
        return None
    blob = store.load_session(prof.name)
    if not blob:
        return None
    return AuthSession.from_blob(prof, blob)


def logout(profile: str | None = None) -> bool:
    """Clear the persisted session for a profile. Returns True if anything was removed.

    Also clears the active-profile pointer when the logged-out profile is the
    active one, so the credential chain falls back to env/config rather than a
    now-empty session.
    """
    name = profile or active_profile_name()
    removed = store.clear_session(name)
    if name == active_profile_name():
        clear_active_profile()
    return removed


def whoami(profile: str | None = None, *, harden_ssl: bool = True) -> dict:
    """Return the verified identity for the current session's STS credentials."""
    if harden_ssl:
        harden_default_ssl_context()
    session = load_session(profile)
    if session is None:
        raise AuthError("not logged in.", hint="run `agentkit login` first.")
    creds = session.credentials()
    ident = get_caller_identity(
        creds.access_key, creds.secret_key, creds.session_token, region=session.profile.region
    )
    ident["_profile"] = session.profile.name
    ident["_expires_at"] = creds.expires_at.isoformat() if creds.expires_at else None
    return ident
