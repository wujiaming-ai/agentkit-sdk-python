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

"""Secure on-disk store for SSO sessions (refresh token + cached STS creds).

Sessions live one-per-profile under ``~/.agentkit/auth/sessions/<profile>.json``
with ``0600`` permissions on a ``0700`` directory. The store holds the OIDC
refresh token (so an expired STS session can be renewed without a new browser
round-trip) and the most recent STS credentials.

If the OS keyring is available (``keyring`` installed) it is used for the refresh
token, with the file store as fallback — never the other way around.
"""

from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

_KEYRING_SERVICE = "agentkit-auth"
# A profile name becomes a filename, so it must not contain path separators or `..`.
_SAFE_PROFILE = re.compile(r"[A-Za-z0-9_.-]+")


def sessions_dir() -> Path:
    root = Path(os.environ.get("AGENTKIT_HOME", Path.home() / ".agentkit"))
    d = root / "auth" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, stat.S_IRWXU)  # 0700
    except OSError:
        pass
    return d


def _session_path(profile: str) -> Path:
    if profile in (".", "..") or not _SAFE_PROFILE.fullmatch(profile or ""):
        raise ValueError(f"invalid profile name: {profile!r}")
    base = sessions_dir().resolve()
    path = (base / f"{profile}.json").resolve()
    if path.parent != base:  # defence in depth against path traversal
        raise ValueError(f"invalid profile name (path traversal): {profile!r}")
    return path


def _try_keyring():
    try:
        import keyring  # type: ignore

        return keyring
    except Exception:
        return None


def save_session(profile: str, data: dict) -> Path:
    """Persist a session blob. The refresh token is stored in the OS keyring when
    available; the rest (incl. cached STS creds) goes to the 0600 file."""
    data = dict(data)
    kr = _try_keyring()
    if kr and data.get("refresh_token"):
        try:
            kr.set_password(_KEYRING_SERVICE, profile, data["refresh_token"])
            data["refresh_token"] = "__keyring__"
        except Exception:
            pass  # fall back to file storage of the token
    path = _session_path(profile)
    # Write 0600. The mode arg to os.open only applies on *creation*; force 0600
    # explicitly so a pre-existing, looser-permissioned file is tightened too.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
    except (OSError, AttributeError):
        pass
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return path


def load_session(profile: str) -> dict | None:
    path = _session_path(profile)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if data.get("refresh_token") == "__keyring__":
        kr = _try_keyring()
        if kr:
            try:
                data["refresh_token"] = kr.get_password(_KEYRING_SERVICE, profile)
            except Exception:
                data["refresh_token"] = None
    return data


def clear_session(profile: str) -> bool:
    removed = False
    path = _session_path(profile)
    if path.exists():
        try:
            path.unlink()
            removed = True
        except OSError:
            pass
    kr = _try_keyring()
    if kr:
        try:
            kr.delete_password(_KEYRING_SERVICE, profile)
            removed = True
        except Exception:
            pass
    return removed
