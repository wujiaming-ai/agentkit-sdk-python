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

"""SSO profile — the non-secret coordinates of one login target.

A profile names an issuer + public client + the STS role/provider to assume. It
contains **no secrets** (only public identifiers), so it is safe to commit, share,
or publish at a UserPool's ``/.well-known/agentkit-cli`` endpoint.

Profiles live under ``~/.agentkit/auth/profiles/<name>.json``. The active profile
defaults to ``$AGENTKIT_AUTH_PROFILE`` or ``"default"``.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from agentkit.auth.errors import AuthError

_PROFILE_FIELDS = {
    "name", "issuer", "client_id", "role_trn", "provider_trn",
    "region", "scope", "transport", "address",
}


@dataclass
class AuthProfile:
    """Non-secret coordinates for an SSO → STS login."""

    name: str
    issuer: str
    client_id: str
    role_trn: str
    provider_trn: str | None = None
    region: str = "cn-beijing"
    scope: str = "openid profile email offline_access"
    transport: str = "sts"  # "sts" (sandbox) | reserved for future transports
    address: str | None = None  # the login address this profile was resolved from

    def validate(self) -> "AuthProfile":
        missing = [k for k in ("name", "issuer", "client_id", "role_trn") if not getattr(self, k)]
        if missing:
            raise AuthError(f"profile is missing required fields: {', '.join(missing)}")
        # https only, except loopback may use http (matches resolve._normalize).
        loopback = re.match(r"^http://(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:/|$)", self.issuer)
        if not (self.issuer.startswith("https://") or loopback):
            raise AuthError(
                f"profile issuer must be https://, or http:// only for loopback "
                f"(localhost/127.0.0.1/[::1]); got {self.issuer!r}"
            )
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AuthProfile":
        return cls(**{k: v for k, v in data.items() if k in _PROFILE_FIELDS}).validate()


def _auth_dir() -> Path:
    return Path(os.environ.get("AGENTKIT_HOME", Path.home() / ".agentkit")) / "auth"


def profiles_dir() -> Path:
    return _auth_dir() / "profiles"


def active_profile_path() -> Path:
    """The pointer file naming the profile that `agentkit login` last selected."""
    return _auth_dir() / "active_profile"


def set_active_profile(name: str) -> None:
    """Record ``name`` as the active profile so separate CLI invocations agree
    on which session to use — without the user setting any environment variable."""
    path = active_profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(name.strip() + "\n")


def clear_active_profile() -> None:
    with contextlib.suppress(OSError):
        active_profile_path().unlink()


def active_profile_name() -> str:
    """Resolve the active profile: env override > pointer file > 'default'.

    The pointer file is what makes ``agentkit login <address>`` followed by
    ``agentkit sandbox create`` work with zero env vars — both invocations read it.
    """
    env = os.environ.get("AGENTKIT_AUTH_PROFILE")
    if env:
        return env
    path = active_profile_path()
    if path.exists():
        try:
            name = path.read_text(encoding="utf-8").strip()
            if name:
                return name
        except OSError:
            pass
    return "default"


def address_to_profile_name(address: str) -> str:
    """Derive a stable, unique, filesystem-safe profile name from a login address.

    Keyed on the host so two pools never collide and re-login to the same UserPool
    reuses the same profile/session."""
    host = address.strip()
    host = re.sub(r"^[a-z]+://", "", host, flags=re.IGNORECASE)  # drop scheme
    host = host.split("/")[0].split("?")[0]  # host only
    safe = re.sub(r"[^A-Za-z0-9._-]", "-", host).strip("-")
    return safe or "default"


def save_profile(profile: AuthProfile) -> Path:
    profile.validate()
    d = profiles_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{profile.name}.json"
    path.write_text(json.dumps(profile.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_profile(name: str | None = None) -> AuthProfile:
    name = name or active_profile_name()
    path = profiles_dir() / f"{name}.json"
    if not path.exists():
        raise AuthError(
            f"not logged in (no profile {name!r}).",
            hint="run `agentkit login <address>` to log in.",
        )
    return AuthProfile.from_dict(json.loads(path.read_text(encoding="utf-8")))


def list_profiles() -> list[str]:
    d = profiles_dir()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json"))
