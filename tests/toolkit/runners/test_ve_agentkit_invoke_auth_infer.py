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

import pytest


class _FakeRuntimeClient:
    def __init__(self, runtime):
        self._runtime = runtime

    def get_runtime(self, req):
        return self._runtime


class _FakeKeyAuth:
    def __init__(self, api_key: str):
        self.api_key = api_key


class _FakeAuthorizer:
    def __init__(self, key_auth=None, custom_jwt_authorizer=None):
        self.key_auth = key_auth
        self.custom_jwt_authorizer = custom_jwt_authorizer


class _FakeRuntime:
    def __init__(self, authorizer_configuration):
        self.authorizer_configuration = authorizer_configuration


def test_invoke_infers_custom_jwt_auth_and_requires_authorization(monkeypatch) -> None:
    from agentkit.toolkit.runners.ve_agentkit import (
        VeAgentkitRuntimeRunner,
        VeAgentkitRunnerConfig,
    )
    from agentkit.toolkit.errors import ErrorCode

    runtime = _FakeRuntime(_FakeAuthorizer(custom_jwt_authorizer=object()))
    runner = VeAgentkitRuntimeRunner()

    monkeypatch.setattr(
        runner, "_get_runtime_client", lambda region="": _FakeRuntimeClient(runtime)
    )
    monkeypatch.setattr(
        runner, "get_public_endpoint_of_runtime", lambda rt: "https://x/"
    )
    monkeypatch.setattr(
        runner,
        "_invoke_with_adk_compat",
        lambda ctx, payload, policy: (True, {"ok": True}, False),
    )

    cfg = VeAgentkitRunnerConfig(runtime_id="r-123", region="cn-beijing")
    result = runner.invoke(cfg, payload={"prompt": "hi"}, headers={})
    assert result.success is False
    assert result.error_code == ErrorCode.AUTH_FAILED


def test_invoke_infers_key_auth_and_injects_api_key(monkeypatch) -> None:
    from agentkit.toolkit.runners.ve_agentkit import (
        VeAgentkitRuntimeRunner,
        VeAgentkitRunnerConfig,
    )

    runtime = _FakeRuntime(_FakeAuthorizer(key_auth=_FakeKeyAuth(api_key="k-1")))
    runner = VeAgentkitRuntimeRunner()

    monkeypatch.setattr(
        runner, "_get_runtime_client", lambda region="": _FakeRuntimeClient(runtime)
    )
    monkeypatch.setattr(
        runner, "get_public_endpoint_of_runtime", lambda rt: "https://x/"
    )

    seen = {}

    def _fake_invoke(ctx, payload, policy):
        seen["auth"] = ctx.headers.get("Authorization")
        return True, {"ok": True}, False

    monkeypatch.setattr(runner, "_invoke_with_adk_compat", _fake_invoke)

    cfg = VeAgentkitRunnerConfig(runtime_id="r-123", region="cn-beijing")
    result = runner.invoke(cfg, payload={"prompt": "hi"}, headers={})
    assert result.success is True
    assert seen["auth"] == "Bearer k-1"
