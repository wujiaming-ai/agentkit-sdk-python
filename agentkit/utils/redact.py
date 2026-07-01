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

"""Secret redaction for log/error output.

Auth flows shuttle tokens and STS secrets around; any of them can end up in an
exception body or a debug print. :func:`redact` scrubs the high-entropy strings
(JWTs, bearer/STS tokens, ``ak/sk`` style secrets) before they are surfaced.
"""

from __future__ import annotations

import re

# JWTs: three base64url segments separated by dots.
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
# Volcengine STS / access tokens and long opaque secrets (>= 16 url-safe chars).
_OPAQUE = re.compile(r"\b[A-Za-z0-9/+_-]{16,}={0,2}\b")
# Explicit secret-bearing query/JSON/header fields.
_FIELD = re.compile(
    r"(?i)(\"?(?:access_token|refresh_token|id_token|client_secret|secret_access_key"
    r"|secretkey|accesskeyid|accesskey|sessiontoken|session_token|authorization"
    r"|apikey|api_key|token|password)\"?\s*[:=]\s*\"?(?:bearer\s+)?)"
    r"([^\"&\s,}]+)"
)


def redact(text: str) -> str:
    """Return ``text`` with credential-looking substrings replaced by ``***``."""
    if not text:
        return text
    text = _FIELD.sub(lambda m: m.group(1) + "***", text)
    text = _JWT.sub("***", text)
    text = _OPAQUE.sub("***", text)
    return text


def mask(secret: str | None, *, keep: int = 4) -> str:
    """Mask a secret, keeping only the last ``keep`` characters for recognition."""
    if not secret:
        return "<none>"
    if len(secret) <= keep:
        return "*" * len(secret)
    return "*" * (len(secret) - keep) + secret[-keep:]
