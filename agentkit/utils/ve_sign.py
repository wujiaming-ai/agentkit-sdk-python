# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
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

# Copyright 2025 ByteDance and/or its affiliates.
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

import datetime
import hashlib
import hmac
import os
import platform
import time
import requests
from urllib.parse import quote

from agentkit.utils.http_defaults import http_timeout, http_retries
from agentkit.utils.logging_config import get_logger

logger = get_logger(__name__)


Service = ""
Version = ""
Region = ""
Host = ""
ContentType = ""
Scheme = "https"


MAX_X_CUSTOM_SOURCE_LENGTH = 256


# Transient-failure handling for signed OpenAPI calls. Historically this used a
# single timeout-less ``requests.request`` — a stalled connection could hang
# forever, and a transient overload (429/503) or connection error failed the
# call outright with no retry. Both are now bounded and conservatively retried.
# Tunable via env; AGENTKIT_HTTP_RETRIES=0 disables retries.
_RETRYABLE_STATUS = frozenset({429, 503})


def _backoff_seconds(attempt: int) -> float:
    return min(8.0, 0.5 * (2**attempt))


def _retry_after_seconds(resp: requests.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _signed_request(method, url, headers, params, data) -> requests.Response:
    """Issue a signed HTTP request with a bounded timeout and conservative
    transient-retry.

    Only retries failures that almost certainly mean the request was not
    processed — connection errors (incl. connect timeouts) and HTTP 429/503 —
    so non-idempotent ``Create*`` actions are never double-executed. Read
    timeouts and other 5xx are surfaced, not retried. Honors ``Retry-After``.
    """
    from agentkit.auth.errors import NetworkError

    retries = http_retries()
    timeout = http_timeout()
    resp: requests.Response | None = None
    for attempt in range(retries + 1):
        try:
            resp = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                data=data,
                timeout=timeout,
            )
        except requests.ConnectionError as e:
            # Not delivered (connection failed/reset before completion), so a
            # retry cannot double-execute the request.
            if attempt < retries:
                sleep = _backoff_seconds(attempt)
                logger.debug(
                    "signed request connection error on attempt %d/%d; "
                    "retrying in %.2fs",
                    attempt + 1,
                    retries + 1,
                    sleep,
                )
                time.sleep(sleep)
                continue
            raise NetworkError(
                "signed request failed: connection error after "
                f"{retries + 1} attempt(s)"
            ) from e
        except (requests.Timeout, requests.HTTPError, requests.RequestException) as e:
            # Read timeouts and other transport errors are not retried (a
            # non-idempotent action may already have been processed); surface
            # them as a domain error with the original cause chained.
            raise NetworkError(f"signed request failed: {type(e).__name__}") from e
        if resp.status_code in _RETRYABLE_STATUS and attempt < retries:
            sleep = _retry_after_seconds(resp) or _backoff_seconds(attempt)
            logger.debug(
                "signed request got retryable status %d on attempt %d/%d; "
                "retrying in %.2fs",
                resp.status_code,
                attempt + 1,
                retries + 1,
                sleep,
            )
            time.sleep(sleep)
            continue
        return resp
    assert resp is not None  # loop always returns or raises before here
    return resp


def _get_os_tag() -> str:
    system = platform.system().lower()
    if "linux" in system:
        return "linux"
    if "windows" in system:
        return "windows"
    if "darwin" in system or "mac" in system:
        return "macos"
    return "unknown"


def _get_entry() -> str:
    value = os.getenv("AGENTKIT_CLIENT_TYPE", "").strip().lower()
    if value in ("cli", "sdk"):
        return value
    return "sdk"


def _get_sdk_version() -> str:
    try:
        from agentkit.version import VERSION

        if VERSION:
            return VERSION
    except Exception:
        pass
    return "unknown"


def build_x_custom_source_header() -> str:
    sdk_name = "agentkit-sdk-python"
    version = _get_sdk_version()
    entry = _get_entry()
    os_tag = _get_os_tag()
    product = f"{sdk_name}/{version}"
    parts = ["schema=v1", f"entry={entry}", f"os={os_tag}"]
    inner = "; ".join(parts)
    value = product
    if inner:
        value = f"{product} ({inner})"
    if len(value) <= MAX_X_CUSTOM_SOURCE_LENGTH:
        return value
    if len(product) <= MAX_X_CUSTOM_SOURCE_LENGTH:
        return product
    return value[:MAX_X_CUSTOM_SOURCE_LENGTH]


def ensure_x_custom_source_header(header: dict | None) -> dict:
    base = header.copy() if header is not None else {}
    if "X-Custom-Request-Context" not in base:
        base["X-Custom-Request-Context"] = build_x_custom_source_header()
    return base


def norm_query(params):
    query = ""
    for key in sorted(params.keys()):
        if isinstance(params[key], list):
            for k in params[key]:
                query = (
                    query + quote(key, safe="-_.~") + "=" + quote(k, safe="-_.~") + "&"
                )
        else:
            query = (
                query
                + quote(key, safe="-_.~")
                + "="
                + quote(params[key], safe="-_.~")
                + "&"
            )
    query = query[:-1]
    return query.replace("+", "%20")


def hmac_sha256(key: bytes, content: str):
    return hmac.new(key, content.encode("utf-8"), hashlib.sha256).digest()


def hash_sha256(content: str):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _header_value(headers: dict, name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower() and value:
            return str(value)
    return None


def request(method, date, query, header, ak, sk, action, body, session_token=None):
    # 第三步：创建身份证明。其中的 Service 和 Region 字段是固定的。ak 和 sk 分别代表
    # AccessKeyID 和 SecretAccessKey。同时需要初始化签名结构体。一些签名计算时需要的属性也在这里处理。
    # 初始化身份证明结构体
    credential = {
        "access_key_id": ak,
        "secret_access_key": sk,
        "service": Service,
        "region": Region,
    }
    # 初始化签名结构体
    request_param = {
        "body": body,
        "host": Host,
        "path": "/",
        "method": method,
        "content_type": ContentType,
        "date": date,
        "query": {"Action": action, "Version": Version, **query},
    }
    if body is None:
        request_param["body"] = ""
    # 第四步：接下来开始计算签名。在计算签名前，先准备好用于接收签算结果的 signResult 变量，并设置一些参数。
    # 初始化签名结果的结构体
    x_date = request_param["date"].strftime("%Y%m%dT%H%M%SZ")
    short_x_date = x_date[:8]
    x_content_sha256 = hash_sha256(request_param["body"])
    sign_result = {
        "Host": request_param["host"],
        "X-Content-Sha256": x_content_sha256,
        "X-Date": x_date,
        "Content-Type": request_param["content_type"],
    }
    token = session_token or _header_value(header, "X-Security-Token")
    if token:
        sign_result["X-Security-Token"] = token

    # 第五步：计算 Signature 签名。
    signed_header_names = ["content-type", "host", "x-content-sha256", "x-date"]
    canonical_header_lines = [
        "content-type:" + request_param["content_type"],
        "host:" + request_param["host"],
        "x-content-sha256:" + x_content_sha256,
        "x-date:" + x_date,
    ]
    if token:
        signed_header_names.append("x-security-token")
        canonical_header_lines.append("x-security-token:" + token)
    signed_headers_str = ";".join(signed_header_names)
    canonical_request_str = "\n".join(
        [
            request_param["method"].upper(),
            request_param["path"],
            norm_query(request_param["query"]),
            "\n".join(canonical_header_lines),
            "",
            signed_headers_str,
            x_content_sha256,
        ]
    )

    # 打印正规化的请求用于调试比对
    # print(canonical_request_str)
    hashed_canonical_request = hash_sha256(canonical_request_str)

    # 打印hash值用于调试比对
    # print(hashed_canonical_request)
    credential_scope = "/".join(
        [short_x_date, credential["region"], credential["service"], "request"]
    )
    string_to_sign = "\n".join(
        ["HMAC-SHA256", x_date, credential_scope, hashed_canonical_request]
    )

    # 打印最终计算的签名字符串用于调试比对
    # print(string_to_sign)
    k_date = hmac_sha256(credential["secret_access_key"].encode("utf-8"), short_x_date)
    k_region = hmac_sha256(k_date, credential["region"])
    k_service = hmac_sha256(k_region, credential["service"])
    k_signing = hmac_sha256(k_service, "request")
    signature = hmac_sha256(k_signing, string_to_sign).hex()

    sign_result["Authorization"] = (
        "HMAC-SHA256 Credential={}, SignedHeaders={}, Signature={}".format(
            credential["access_key_id"] + "/" + credential_scope,
            signed_headers_str,
            signature,
        )
    )
    if token:
        header = {
            key: value
            for key, value in header.items()
            if key.lower() != "x-security-token"
        }
    header = ensure_x_custom_source_header(header)
    header = {**header, **sign_result}
    # 第六步：将 Signature 签名写入 HTTP Header 中，并发送 HTTP 请求。
    r = _signed_request(
        method=method,
        url=f"{Scheme}://{request_param['host']}{request_param['path']}",
        headers=header,
        params=request_param["query"],
        data=request_param["body"],
    )
    return r.json()


def ve_request(
    request_body: dict,
    action: str,
    ak: str,
    sk: str,
    service: str,
    version: str,
    region: str,
    host: str,
    header: dict | None = None,
    content_type: str = "application/json",
    scheme: str = "https",
    session_token: str | None = None,
):
    # response_body = request("Get", datetime.datetime.utcnow(), {}, {}, AK, SK, "ListUsers", None)
    # print(response_body)
    # 以下参数视服务不同而不同，一个服务内通常是一致的
    global Service
    Service = service
    global Version
    Version = version
    global Region
    Region = region
    global Host
    Host = host
    global ContentType
    ContentType = content_type
    global Scheme
    Scheme = scheme or "https"

    AK = ak
    SK = sk

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)

    # Body的格式需要配合Content-Type，API使用的类型请阅读具体的官方文档，如:json格式需要json.dumps(obj)
    # response_body = request("GET", now, {"Limit": "2"}, {}, AK, SK, "ListUsers", None)
    import json

    response_body = request(
        "POST",
        now,
        {},
        header or {},
        AK,
        SK,
        action,
        json.dumps(request_body),
        session_token=session_token,
    )
    check_error(response_body)
    return response_body


def check_error(response: dict) -> None:
    if "Error" in response:
        raise ValueError(f"Error in response: {response['Error']}")
    if "Error" in response["ResponseMetadata"]:
        error_code = response["ResponseMetadata"]["Error"]["Code"]
        error_message = response["ResponseMetadata"]["Error"]["Message"]
        action = response["ResponseMetadata"]["Action"]
        raise ValueError(
            f"Error when ve_request {action}: {error_code} {error_message}"
        )
