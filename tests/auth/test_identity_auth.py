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

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

import agentkit.sdk.identity.auth as identity_auth
from agentkit.sdk.identity.auth import requires_api_key
from agentkit.toolkit.errors import ApiError


@dataclass
class _Creds:
    access_key: str = "AK"
    secret_key: str = "SK"
    session_token: str | None = "STS_TOKEN"


@dataclass
class _Endpoint:
    api_version: str = "2025-01-01"
    service: str = "identity"
    host: str = "identity.volcengineapi.com"
    region: str = "cn-beijing"


def _patch_identity_request(monkeypatch, response):
    captured = {}

    def _fake_resolve_credentials(service):
        captured["credential_service"] = service
        return _Creds()

    def _fake_resolve_endpoint(service):
        captured["endpoint_service"] = service
        return _Endpoint()

    def _fake_ve_request(**kwargs):
        captured["request"] = kwargs
        return response

    monkeypatch.setattr(identity_auth, "resolve_credentials", _fake_resolve_credentials)
    monkeypatch.setattr(identity_auth, "resolve_endpoint", _fake_resolve_endpoint)
    monkeypatch.setattr(identity_auth, "ve_request", _fake_ve_request)
    return captured


def test_requires_api_key_passes_session_token(monkeypatch):
    captured = _patch_identity_request(
        monkeypatch, {"ResponseMetadata": {}, "Result": {"ApiKey": "api-key"}}
    )

    @requires_api_key(provider_name="provider")
    def _call(api_key=None):
        return api_key

    assert _call() == "api-key"
    request = captured["request"]
    assert captured["credential_service"] == "identity"
    assert captured["endpoint_service"] == "identity"
    assert request["session_token"] == "STS_TOKEN"
    assert request["ak"] == "AK"
    assert request["sk"] == "SK"


def test_requires_api_key_passes_session_token_for_async_function(monkeypatch):
    captured = _patch_identity_request(
        monkeypatch, {"ResponseMetadata": {}, "Result": {"ApiKey": "api-key"}}
    )

    @requires_api_key(provider_name="provider")
    async def _call(api_key=None):
        return api_key

    assert asyncio.run(_call()) == "api-key"
    assert captured["request"]["session_token"] == "STS_TOKEN"


def test_requires_api_key_raises_domain_error_when_api_key_missing(monkeypatch):
    _patch_identity_request(monkeypatch, {"ResponseMetadata": {}, "Result": {}})

    @requires_api_key(provider_name="provider")
    def _call(api_key=None):
        return api_key

    with pytest.raises(ApiError, match="GetResourceApiKey did not return an ApiKey"):
        _call()
