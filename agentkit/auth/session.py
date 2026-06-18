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

"""AuthSession — a live SSO session that yields STS credentials on demand.

Holds the profile, the cached STS credentials, and the OIDC refresh token. When
the STS credentials are near expiry it transparently renews them: refresh the
OIDC token → ``AssumeRoleWithOIDC`` → new STS credentials. Persisted via
:mod:`agentkit.auth.store`.
"""

from __future__ import annotations

import contextlib
import datetime
import threading
from dataclasses import dataclass

from agentkit.auth import store
from agentkit.auth.errors import AuthError
from agentkit.auth.oauth import OAuthClient
from agentkit.auth.profile import AuthProfile
from agentkit.auth.sts import assume_role_with_oidc

# Renew when fewer than this many seconds of validity remain.
_RENEW_SKEW = datetime.timedelta(seconds=300)


@contextlib.contextmanager
def _profile_file_lock(profile: str):
    """Best-effort cross-process advisory lock on a profile's refresh.

    Uses ``fcntl.flock`` where available; degrades to a no-op (in-process lock
    still applies) on platforms without it, which is fine for single-process CLIs.
    """
    lock_path = store.sessions_dir() / f"{profile}.lock"
    fh = None
    try:
        import fcntl

        fh = open(lock_path, "w")
        fcntl.flock(fh, fcntl.LOCK_EX)
    except Exception:
        fh = None
    try:
        yield
    finally:
        if fh is not None:
            with contextlib.suppress(Exception):
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()


def _effective_expiry(
    expired_at: datetime.datetime | None, duration_seconds: int
) -> datetime.datetime:
    """Always return a concrete expiry.

    STS reliably returns an expiry, but if it is ever missing we derive a
    conservative one from the requested duration so the session still refreshes
    (rather than being treated as valid forever)."""
    if expired_at is not None:
        return expired_at
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=duration_seconds)


def _jwt_expired(token: str, skew_seconds: int) -> bool:
    """True if a JWT is expired, within ``skew_seconds`` of expiry, or unreadable.

    Reads only the ``exp`` claim (no signature check — the harness endpoint verifies
    the signature; we just decide locally whether to refresh). An unparseable token or
    a missing/invalid ``exp`` is treated as expired so we refresh rather than send a
    token that will be rejected."""
    try:
        import jwt as _jwt

        claims = _jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
    except Exception:
        return True
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)):
        return True
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    return now >= (exp - skew_seconds)


@dataclass(frozen=True)
class StsCredentials:
    """Short-lived Volcengine STS credentials."""

    access_key: str
    secret_key: str
    session_token: str
    expires_at: datetime.datetime | None
    account_id: str | None = None

    def is_expired(self, skew: datetime.timedelta = _RENEW_SKEW) -> bool:
        if self.expires_at is None:
            return True  # unknown expiry → refresh rather than risk using a stale token
        now = datetime.datetime.now(datetime.timezone.utc)
        return now >= (self.expires_at - skew)


class AuthSession:
    """A live, refreshable SSO session for one profile."""

    def __init__(
        self,
        profile: AuthProfile,
        *,
        refresh_token: str | None = None,
        sts: StsCredentials | None = None,
        duration_seconds: int = 3600,
        id_token: str | None = None,
        access_token: str | None = None,
    ) -> None:
        self.profile = profile
        self._refresh_token = refresh_token
        self._sts = sts
        self._duration = duration_seconds
        # OIDC tokens kept for data-plane (JWT) auth — the id_token is the inbound
        # credential for `agentkit invoke harness`; access_token is stored for parity.
        self._id_token = id_token
        self._access_token = access_token
        self._lock = threading.Lock()

    # -- persistence ----------------------------------------------------------
    def to_blob(self) -> dict:
        sts = self._sts
        return {
            "profile": self.profile.name,
            "issuer": self.profile.issuer,
            "refresh_token": self._refresh_token,
            "id_token": self._id_token,
            "access_token": self._access_token,
            "duration_seconds": self._duration,
            "sts": None
            if sts is None
            else {
                "access_key": sts.access_key,
                "secret_key": sts.secret_key,
                "session_token": sts.session_token,
                "expires_at": sts.expires_at.isoformat() if sts.expires_at else None,
                "account_id": sts.account_id,
            },
        }

    def save(self) -> None:
        store.save_session(self.profile.name, self.to_blob())

    @classmethod
    def from_blob(cls, profile: AuthProfile, blob: dict) -> "AuthSession":
        sts_blob = blob.get("sts") or None
        sts = None
        if sts_blob:
            exp = sts_blob.get("expires_at")
            sts = StsCredentials(
                access_key=sts_blob["access_key"],
                secret_key=sts_blob["secret_key"],
                session_token=sts_blob["session_token"],
                expires_at=datetime.datetime.fromisoformat(exp) if exp else None,
                account_id=sts_blob.get("account_id"),
            )
        return cls(
            profile,
            refresh_token=blob.get("refresh_token"),
            sts=sts,
            duration_seconds=int(blob.get("duration_seconds") or 3600),
            id_token=blob.get("id_token"),
            access_token=blob.get("access_token"),
        )

    # -- credentials ----------------------------------------------------------
    def credentials(self, *, force_refresh: bool = False) -> StsCredentials:
        """Return valid STS credentials, renewing them if needed.

        Renewal is guarded by both an in-process lock and a cross-process file
        lock, so concurrent SDK clients (CLI subprocesses or server threads) do not
        race on refresh-token rotation. After taking the locks we re-read the store
        in case a sibling already refreshed.
        """
        with self._lock:
            if not force_refresh and self._sts is not None and not self._sts.is_expired():
                return self._sts
            with _profile_file_lock(self.profile.name):
                if not force_refresh:
                    from agentkit.auth import store

                    blob = store.load_session(self.profile.name)
                    if blob:
                        sibling = AuthSession.from_blob(self.profile, blob)
                        if sibling._sts is not None and not sibling._sts.is_expired():
                            self._sts = sibling._sts
                            self._refresh_token = sibling._refresh_token or self._refresh_token
                            return self._sts
                self._renew_locked()
            assert self._sts is not None
            return self._sts

    # -- data-plane JWT (id_token) --------------------------------------------
    def valid_id_token(self, *, skew_seconds: int = 60, force_refresh: bool = False) -> str:
        """Return a currently-valid OIDC ``id_token`` for data-plane (JWT) auth.

        Used by ``agentkit invoke harness`` as the inbound Bearer credential — it never
        touches STS. Refreshes via the ``refresh_token`` (``grant_type=refresh_token`` at
        the UserPool token endpoint) when the cached id_token is missing, within
        ``skew_seconds`` of expiry, or when ``force_refresh`` is set (e.g. the harness
        returned 401). Raises :class:`AuthError` pointing at ``agentkit login`` when no
        refresh is possible.
        """
        with self._lock:
            if not force_refresh and self._id_token and not _jwt_expired(self._id_token, skew_seconds):
                return self._id_token
            with _profile_file_lock(self.profile.name):
                # Re-read under the lock to pick up any sibling's rotated refresh_token /
                # refreshed STS — so we neither refresh with a stale refresh_token nor clobber
                # a sibling's update when we save below.
                blob = store.load_session(self.profile.name)
                if blob:
                    sibling = AuthSession.from_blob(self.profile, blob)
                    self._refresh_token = sibling._refresh_token or self._refresh_token
                    self._sts = sibling._sts or self._sts
                    if not force_refresh and sibling._id_token and not _jwt_expired(sibling._id_token, skew_seconds):
                        self._id_token = sibling._id_token
                        self._access_token = sibling._access_token or self._access_token
                        return self._id_token
                self._refresh_id_token_locked()
            assert self._id_token is not None
            return self._id_token

    def _refresh_id_token_locked(self) -> None:
        if not self._refresh_token:
            raise AuthError(
                "no id_token and no refresh token available; re-login required.",
                hint="run `agentkit login` to start a new browser session.",
            )
        from agentkit.auth.ssl_trust import harden_default_ssl_context

        harden_default_ssl_context()
        client = OAuthClient(self.profile.issuer, self.profile.client_id, scope=self.profile.scope)
        try:
            token = client.refresh(self._refresh_token)
        except AuthError as exc:
            raise AuthError(
                "session refresh failed — the login has expired or was revoked.",
                hint="run `agentkit login` to log in again.",
            ) from exc
        id_token = str(token.get("id_token") or "")
        if not id_token:
            raise AuthError(
                "refresh did not return an id_token; re-login required.",
                hint="ensure the OAuth scope includes 'openid' (OIDC requires an ID Token).",
            )
        self._id_token = id_token
        if token.get("access_token"):
            self._access_token = str(token["access_token"])
        if token.get("refresh_token"):
            self._refresh_token = str(token["refresh_token"])
        self.save()

    def _renew_locked(self) -> None:
        if not self._refresh_token:
            raise AuthError(
                "STS session expired and no refresh token is available.",
                hint="run `agentkit login` to start a new browser session.",
            )
        # Behind a TLS-intercepting corporate proxy the refresh would otherwise
        # fail with CERTIFICATE_VERIFY_FAILED; harden once before any network I/O.
        from agentkit.auth.ssl_trust import harden_default_ssl_context

        harden_default_ssl_context()
        client = OAuthClient(self.profile.issuer, self.profile.client_id, scope=self.profile.scope)
        token = client.refresh(self._refresh_token)
        id_token = str(token.get("id_token") or "")  # OIDC: AssumeRoleWithOIDC needs the ID Token, not an access token
        if not id_token:
            raise AuthError(
                "refresh did not return an id_token; re-login required.",
                hint="ensure the OAuth scope includes 'openid' (OIDC requires an ID Token).",
            )
        # Keep the freshly-issued OIDC tokens for data-plane (JWT) auth, not just STS.
        self._id_token = id_token
        if token.get("access_token"):
            self._access_token = str(token["access_token"])
        # A rotated refresh token, if returned, must replace the old one.
        if token.get("refresh_token"):
            self._refresh_token = str(token["refresh_token"])
        assumed = assume_role_with_oidc(
            id_token,
            self.profile.role_trn,
            self.profile.provider_trn,
            duration_seconds=self._duration,
        )
        self._sts = StsCredentials(
            access_key=assumed.access_key_id,
            secret_key=assumed.secret_access_key,
            session_token=assumed.session_token,
            expires_at=_effective_expiry(assumed.expired_at, self._duration),
            account_id=(self._sts.account_id if self._sts else None),
        )
        self.save()
