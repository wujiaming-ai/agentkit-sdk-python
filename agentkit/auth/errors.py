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

"""Exception hierarchy for :mod:`agentkit.auth`."""

from __future__ import annotations


class AuthError(Exception):
    """Base class for all authentication failures.

    Carries an optional ``hint`` with an actionable next step that CLIs can
    surface to the user.
    """

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint

    def __str__(self) -> str:  # pragma: no cover - trivial
        base = super().__str__()
        return f"{base}\n  hint: {self.hint}" if self.hint else base


class NetworkError(AuthError):
    """A network endpoint (IdP / STS) was unreachable."""


class SsoError(AuthError):
    """The OAuth/OIDC SSO exchange was rejected by the IdP or STS."""
