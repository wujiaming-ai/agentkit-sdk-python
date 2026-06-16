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

"""Volcengine STS operations used by the SSO → STS bridge.

* :func:`assume_role_with_oidc` — exchange a UserPool ``id_token`` for short-lived
  STS credentials. This call is **anonymous** (no AK/SK): the OIDC token *is* the
  credential, sent in the POST body.
* :func:`get_caller_identity` — resolve the identity behind a set of credentials
  (used by ``agentkit whoami`` and by identity-bound data-plane verification).
"""

from __future__ import annotations

import datetime
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from agentkit.auth._redact import redact
from agentkit.auth._sigv4 import sign_headers
from agentkit.auth.errors import NetworkError, SsoError

STS_HOST = "sts.volcengineapi.com"
STS_VERSION = "2018-01-01"
_TIMEOUT = 20


@dataclass(frozen=True)
class AssumedRole:
    """Result of :func:`assume_role_with_oidc`."""

    access_key_id: str
    secret_access_key: str
    session_token: str
    expired_at: datetime.datetime | None

    @classmethod
    def _from_api(cls, creds: dict) -> "AssumedRole":
        expired = None
        raw = creds.get("ExpiredTime") or creds.get("Expiration")
        if isinstance(raw, str) and raw:
            try:
                expired = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                expired = None
        return cls(
            access_key_id=str(creds["AccessKeyId"]),
            secret_access_key=str(creds["SecretAccessKey"]),
            session_token=str(creds["SessionToken"]),
            expired_at=expired,
        )


def assume_role_with_oidc(
    id_token: str,
    role_trn: str,
    provider_trn: str | None = None,
    *,
    role_session_name: str = "agentkit-cli",
    duration_seconds: int = 3600,
    timeout: float = _TIMEOUT,
) -> AssumedRole:
    """Exchange an OIDC ``id_token`` for temporary STS credentials.

    Anonymous call — the token is the credential. ``host`` MUST be
    ``sts.volcengineapi.com``; the token is too long for the query string so it
    travels in the POST body.
    """
    params = {
        "RoleTrn": role_trn,
        "OIDCToken": id_token,
        "RoleSessionName": role_session_name,
        "DurationSeconds": str(duration_seconds),
    }
    if provider_trn:
        params["OIDCProviderTrn"] = provider_trn
    body = urllib.parse.urlencode(params).encode("utf-8")
    url = f"https://{STS_HOST}/?Action=AssumeRoleWithOIDC&Version={STS_VERSION}"
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST"
    )
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read()
    except urllib.error.HTTPError as exc:
        detail = redact(exc.read().decode("utf-8", "replace"))[:300]
        raise SsoError(
            f"AssumeRoleWithOIDC rejected: {detail}",
            hint="check the role trust policy trusts the OIDC provider and the "
            "id_token's aud is in the provider's client-id allow-list.",
        ) from exc
    except urllib.error.URLError as exc:
        raise NetworkError(f"cannot reach STS endpoint ({STS_HOST}): {exc.reason}") from exc

    try:
        creds = json.loads(raw)["Result"]["Credentials"]
        return AssumedRole._from_api(creds)
    except (ValueError, KeyError, TypeError) as exc:
        raise SsoError(
            f"STS returned an unexpected response: {redact(raw.decode('utf-8', 'replace'))[:200]}"
        ) from exc


def get_caller_identity(
    access_key: str,
    secret_key: str,
    session_token: str | None = None,
    *,
    region: str = "cn-beijing",
    timeout: float = _TIMEOUT,
) -> dict:
    """Return the verified identity (``AccountId`` / ``IdentityType`` / ``UserId``)."""
    query = {"Action": "GetCallerIdentity", "Version": STS_VERSION}
    headers = sign_headers(
        "POST", STS_HOST, query, b"",
        access_key=access_key, secret_key=secret_key, service="sts", region=region,
        session_token=session_token,
    )
    url = f"https://{STS_HOST}/?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, data=b"", headers=headers, method="POST")
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read()
    except urllib.error.HTTPError as exc:
        detail = redact(exc.read().decode("utf-8", "replace"))[:200]
        raise SsoError(f"GetCallerIdentity failed: {detail}") from exc
    except urllib.error.URLError as exc:
        raise NetworkError(f"cannot reach STS endpoint ({STS_HOST}): {exc.reason}") from exc
    return json.loads(raw).get("Result") or {}
