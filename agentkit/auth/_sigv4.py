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

"""Volcengine SigV4 (HMAC-SHA256) request signer — stdlib only.

A minimal, self-contained signer so :mod:`agentkit.auth` has no dependency on the
``volcengine`` base SDK. Critically it supports an **STS session token**
(``X-Security-Token``), which the base SDK's helpers do not always expose, so STS
short-lived credentials can sign control-plane calls.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import urllib.parse


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sign_headers(
    method: str,
    host: str,
    query: dict[str, str],
    body: bytes,
    *,
    access_key: str,
    secret_key: str,
    service: str,
    region: str,
    session_token: str | None = None,
    content_type: str = "application/json",
    path: str = "/",
) -> dict[str, str]:
    """Sign a Volcengine OpenAPI request and return the full set of HTTP headers.

    The returned dict can be replayed verbatim by any HTTP client; the signature
    commits to ``content_type`` exactly, so callers must send that content type.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    xdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = xdate[:8]
    payload_hash = hashlib.sha256(body).hexdigest()

    signed: dict[str, str] = {
        "content-type": content_type,
        "host": host,
        "x-content-sha256": payload_hash,
        "x-date": xdate,
    }
    if session_token:
        signed["x-security-token"] = session_token

    signed_headers = ";".join(sorted(signed))
    canon_headers = "".join(f"{k}:{signed[k]}\n" for k in sorted(signed))
    canon_query = "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(str(v), safe='-_.~')}"
        for k, v in sorted(query.items())
    )
    canon_req = f"{method}\n{path}\n{canon_query}\n{canon_headers}\n{signed_headers}\n{payload_hash}"

    scope = f"{datestamp}/{region}/{service}/request"
    string_to_sign = "\n".join(
        ["HMAC-SHA256", xdate, scope, hashlib.sha256(canon_req.encode("utf-8")).hexdigest()]
    )
    k_signing = _hmac(_hmac(_hmac(_hmac(secret_key.encode("utf-8"), datestamp), region), service), "request")
    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    headers = {
        "Content-Type": content_type,
        "Host": host,
        "X-Date": xdate,
        "X-Content-Sha256": payload_hash,
        "Authorization": (
            f"HMAC-SHA256 Credential={access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }
    if session_token:
        headers["X-Security-Token"] = session_token
    return headers
