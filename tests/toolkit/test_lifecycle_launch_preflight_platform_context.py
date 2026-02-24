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

from dataclasses import dataclass


@dataclass
class _FakeCommonConfig:
    launch_type: str = "cloud"


class _FakeConfigManager:
    def get_common_config(self):
        return _FakeCommonConfig()

    def get_resolved_cloud_provider(self):
        from agentkit.toolkit.config.cloud_provider import ResolvedCloudProvider
        from agentkit.platform.provider import CloudProvider

        return ResolvedCloudProvider(provider=CloudProvider.BYTEPLUS, source="project")


class _FakeBuildResult:
    success = True
    error = ""
    error_code = ""
    image = None


class _FakeDeployResult:
    success = True
    error = ""
    error_code = ""
    endpoint_url = "https://example.com"
    container_id = ""
    service_id = ""


def test_lifecycle_launch_preflight_uses_platform_context(monkeypatch) -> None:
    from agentkit.platform.provider import ENV_CLOUD_PROVIDER
    from agentkit.platform import VolcConfiguration
    from agentkit.toolkit.executors.lifecycle_executor import LifecycleExecutor
    from agentkit.toolkit.reporter import SilentReporter
    from agentkit.toolkit.models import PreflightResult, PreflightMode

    monkeypatch.setenv(ENV_CLOUD_PROVIDER, "volcengine")

    before = VolcConfiguration()
    assert before.provider.value == "volcengine"

    ex = LifecycleExecutor(reporter=SilentReporter())
    monkeypatch.setattr(
        ex, "_load_config", lambda *_args, **_kwargs: _FakeConfigManager()
    )
    monkeypatch.setattr(
        ex, "_resolve_account_region", lambda *_args, **_kwargs: "ap-southeast-1"
    )

    def _fake_combined_preflight_check(*_args, **_kwargs):
        cfg = VolcConfiguration()
        assert cfg.provider.value == "byteplus"
        return PreflightResult(passed=True, missing_services=[])

    monkeypatch.setattr(ex, "_combined_preflight_check", _fake_combined_preflight_check)
    monkeypatch.setattr(ex, "_handle_preflight_result", lambda *_args, **_kwargs: True)

    monkeypatch.setattr(
        ex.build_executor, "execute", lambda *_args, **_kwargs: _FakeBuildResult()
    )
    monkeypatch.setattr(
        ex.deploy_executor, "execute", lambda *_args, **_kwargs: _FakeDeployResult()
    )

    res = ex.launch(config_file="agentkit.yaml", preflight_mode=PreflightMode.PROMPT)
    assert res.success is True

    after = VolcConfiguration()
    assert after.provider.value == "volcengine"


def test_agentkit_enable_services_url_byteplus_region(monkeypatch) -> None:
    from agentkit.platform.context import default_cloud_provider
    from agentkit.platform import agentkit_enable_services_url

    monkeypatch.delenv("AGENTKIT_CONSOLE_PROJECT_NAME", raising=False)

    with default_cloud_provider("byteplus"):
        url = agentkit_enable_services_url(region="ap-southeast-1")

    assert (
        url
        == "https://console.byteplus.com/agentkit/region:agentkit+ap-southeast-1/auth?projectName=default"
    )


def test_preflight_sets_auth_url_by_provider_and_region(monkeypatch) -> None:
    import agentkit.sdk.account.client as account_client_mod
    from agentkit.platform.context import default_cloud_provider
    from agentkit.toolkit.executors.build_executor import BuildExecutor
    from agentkit.toolkit.reporter import SilentReporter

    class _FakeAccountClient:
        def __init__(self, region=None):
            self.region = region

        def get_services_status(self, service_names):
            return {name: "Disabled" for name in service_names}

    monkeypatch.setattr(account_client_mod, "AgentkitAccountClient", _FakeAccountClient)
    monkeypatch.delenv("AGENTKIT_CONSOLE_PROJECT_NAME", raising=False)

    ex = BuildExecutor(reporter=SilentReporter())
    with default_cloud_provider("byteplus"):
        result = ex._preflight_check("build", "cloud", region="ap-southeast-1")

    assert result.passed is False
    assert (
        result.auth_url
        == "https://console.byteplus.com/agentkit/region:agentkit+ap-southeast-1/auth?projectName=default"
    )
