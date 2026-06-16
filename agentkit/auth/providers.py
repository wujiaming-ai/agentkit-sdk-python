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

"""Pluggable credential providers.

A :class:`CredentialProvider` resolves a ``(access_key, secret_key,
session_token)`` triple from one source. The SDK credential chain (see
``agentkit.platform.configuration``) consults them in order; the first that
returns wins. Two providers ship here:

* :class:`AkSkCredentialProvider` — long-lived AK/SK from env / config.
* :class:`SsoStsCredentialProvider` — STS credentials from a stored SSO session,
  transparently refreshed.
"""

from __future__ import annotations

import os
from typing import NamedTuple, Protocol, runtime_checkable

from agentkit.auth.sso import load_session


class ResolvedCredentials(NamedTuple):
    access_key: str
    secret_key: str
    session_token: str | None
    source: str


@runtime_checkable
class CredentialProvider(Protocol):
    """Resolves credentials from one source, or returns ``None``."""

    def resolve(self) -> ResolvedCredentials | None: ...


class AkSkCredentialProvider:
    """Long-lived AK/SK from environment variables (or explicit values)."""

    def __init__(
        self,
        access_key: str | None = None,
        secret_key: str | None = None,
        session_token: str | None = None,
    ) -> None:
        self._ak = access_key
        self._sk = secret_key
        self._token = session_token

    def resolve(self) -> ResolvedCredentials | None:
        ak = self._ak or os.getenv("VOLCENGINE_ACCESS_KEY") or os.getenv("VOLC_ACCESSKEY")
        sk = self._sk or os.getenv("VOLCENGINE_SECRET_KEY") or os.getenv("VOLC_SECRETKEY")
        token = self._token or os.getenv("VOLCENGINE_SESSION_TOKEN") or os.getenv("VOLC_SESSIONTOKEN")
        if ak and sk:
            return ResolvedCredentials(ak, sk, token or None, "aksk")
        return None


class SsoStsCredentialProvider:
    """STS credentials from a stored SSO session, refreshed on demand."""

    def __init__(self, profile: str | None = None) -> None:
        self._profile = profile

    def resolve(self) -> ResolvedCredentials | None:
        session = load_session(self._profile)
        if session is None:
            return None
        try:
            creds = session.credentials()
        except Exception:
            return None  # expired and unrefreshable — let the chain fall through
        return ResolvedCredentials(
            creds.access_key, creds.secret_key, creds.session_token, "sso-sts"
        )
