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

"""Offline unit tests for :class:`HybridStrategy` orchestration.

These tests exercise HybridStrategy as a pure orchestration layer: its CR
predicate helpers (:func:`_should_push_to_cr`, :func:`_prepare_cr_config`,
:func:`_validate_cr_image_url`, :func:`_report_cr_skip_reason`) and its
build/deploy/invoke/status/destroy delegation. The lazy ``builder``/``runner``
properties are bypassed by directly setting ``strategy._builder`` /
``strategy._runner`` to hand-rolled fakes, so no Docker, network, or VE Runtime
calls ever happen.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from agentkit.toolkit.config import AUTO_CREATE_VE
from agentkit.toolkit.config.config import CommonConfig
from agentkit.toolkit.config.strategy_configs import HybridStrategyConfig
from agentkit.toolkit.errors import ErrorCode
from agentkit.toolkit.models import (
    BuildResult,
    DeployResult,
    ImageInfo,
    InvokeResult,
    StatusResult,
)
from agentkit.toolkit.strategies.hybrid_strategy import HybridStrategy


# ---------------------------------------------------------------------------
# Hand-rolled fakes
# ---------------------------------------------------------------------------


class _SpyReporter:
    """Records every reporter call so tests can assert which branch fired."""

    def __init__(self) -> None:
        self.infos: list[str] = []
        self.successes: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def info(self, message: str, **kwargs) -> None:
        self.infos.append(message)

    def success(self, message: str, **kwargs) -> None:
        self.successes.append(message)

    def warning(self, message: str, **kwargs) -> None:
        self.warnings.append(message)

    def error(self, message: str, **kwargs) -> None:
        self.errors.append(message)


class _FakeConfigManager:
    """Minimal config manager returning a controlled project dir.

    Pinning ``get_project_dir`` to an empty tmp dir keeps ``merge_runtime_envs``
    deterministic (it otherwise reads .env / config.yaml from cwd).
    """

    def __init__(self, project_dir) -> None:
        self._project_dir = project_dir

    def get_project_dir(self):
        return self._project_dir


class _FakeBuilder:
    """Fake builder capturing the config it was called with."""

    def __init__(self, result: BuildResult) -> None:
        self._result = result
        self.calls: list = []

    def build(self, builder_config) -> BuildResult:
        self.calls.append(builder_config)
        return self._result


class _FakeRunner:
    """Fake runner recording delegation and returning canned results verbatim."""

    def __init__(
        self,
        deploy_result: DeployResult = None,
        invoke_result: InvokeResult = None,
        status_result: StatusResult = None,
        destroy_result: bool = True,
    ) -> None:
        self._deploy_result = deploy_result
        self._invoke_result = invoke_result
        self._status_result = status_result
        self._destroy_result = destroy_result
        self.deploy_calls: list = []
        self.invoke_calls: list = []
        self.status_calls: list = []
        self.destroy_calls: list = []

    def deploy(self, runner_config) -> DeployResult:
        self.deploy_calls.append(runner_config)
        return self._deploy_result

    def invoke(self, runner_config, payload, headers, stream) -> InvokeResult:
        self.invoke_calls.append((runner_config, payload, headers, stream))
        return self._invoke_result

    def status(self, runner_config) -> StatusResult:
        self.status_calls.append(runner_config)
        return self._status_result

    def destroy(self, runner_config) -> bool:
        self.destroy_calls.append(runner_config)
        return self._destroy_result


# ---------------------------------------------------------------------------
# Local fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def common_config() -> CommonConfig:
    return CommonConfig(agent_name="my-agent")


@pytest.fixture
def strategy(tmp_path) -> HybridStrategy:
    """A HybridStrategy wired with a spy reporter and a pinned project dir."""
    reporter = _SpyReporter()
    cm = _FakeConfigManager(project_dir=tmp_path)
    strat = HybridStrategy(config_manager=cm, reporter=reporter)
    return strat


def _valid_hybrid_config(**overrides) -> HybridStrategyConfig:
    """A HybridStrategyConfig with a fully valid, rendered CR configuration."""
    cfg = HybridStrategyConfig(
        cr_instance_name="my-cr-instance",
        cr_namespace_name="my-namespace",
        cr_repo_name="my-repo",
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


# ---------------------------------------------------------------------------
# _should_push_to_cr  (:267)
# ---------------------------------------------------------------------------


def test_should_push_to_cr_returns_false_when_instance_name_is_empty(strategy):
    cfg = _valid_hybrid_config(cr_instance_name="")
    should_push, reason = strategy._should_push_to_cr(cfg, "my-repo")
    assert should_push is False
    assert reason == "CR instance name is empty"


def test_should_push_to_cr_returns_false_when_instance_name_is_auto(strategy):
    cfg = _valid_hybrid_config(cr_instance_name=AUTO_CREATE_VE)
    should_push, reason = strategy._should_push_to_cr(cfg, "my-repo")
    assert should_push is False
    assert reason == "CR instance name is 'Auto'"


def test_should_push_to_cr_returns_false_when_instance_name_has_unrendered_template(
    strategy,
):
    cfg = _valid_hybrid_config(cr_instance_name="cr-{{ account_id }}")
    should_push, reason = strategy._should_push_to_cr(cfg, "my-repo")
    assert should_push is False
    assert reason == "CR instance name contains unrendered template variables"


def test_should_push_to_cr_returns_false_when_namespace_name_is_empty(strategy):
    cfg = _valid_hybrid_config(cr_namespace_name="")
    should_push, reason = strategy._should_push_to_cr(cfg, "my-repo")
    assert should_push is False
    assert reason == "CR namespace name is empty"


def test_should_push_to_cr_returns_false_when_namespace_name_is_auto(strategy):
    cfg = _valid_hybrid_config(cr_namespace_name=AUTO_CREATE_VE)
    should_push, reason = strategy._should_push_to_cr(cfg, "my-repo")
    assert should_push is False
    assert reason == "CR namespace name is 'Auto'"


def test_should_push_to_cr_returns_false_when_namespace_name_has_unrendered_template(
    strategy,
):
    cfg = _valid_hybrid_config(cr_namespace_name="ns-{{ foo }}")
    should_push, reason = strategy._should_push_to_cr(cfg, "my-repo")
    assert should_push is False
    assert reason == "CR namespace name contains unrendered template variables"


def test_should_push_to_cr_returns_false_when_repo_name_is_empty(strategy):
    cfg = _valid_hybrid_config()
    should_push, reason = strategy._should_push_to_cr(cfg, "")
    assert should_push is False
    assert reason == "CR repository name is empty"


def test_should_push_to_cr_returns_true_for_fully_valid_configuration(strategy):
    cfg = _valid_hybrid_config()
    should_push, reason = strategy._should_push_to_cr(cfg, "my-repo")
    assert should_push is True
    assert reason == ""


# ---------------------------------------------------------------------------
# _prepare_cr_config  (:252)
# ---------------------------------------------------------------------------


def test_prepare_cr_config_falls_back_to_agent_name_when_repo_empty(strategy):
    result = strategy._prepare_cr_config("", "my-agent")
    assert result == "my-agent"


def test_prepare_cr_config_falls_back_to_default_when_repo_and_agent_empty(strategy):
    result = strategy._prepare_cr_config("", "")
    assert result == "agentkit-app"


def test_prepare_cr_config_passes_through_non_empty_repo_name(strategy):
    result = strategy._prepare_cr_config("explicit-repo", "my-agent")
    assert result == "explicit-repo"


# ---------------------------------------------------------------------------
# _validate_cr_image_url  (:427)
# ---------------------------------------------------------------------------


def test_validate_cr_image_url_succeeds_when_image_url_present(strategy):
    cfg = _valid_hybrid_config(cr_image_full_url="registry.example.com/ns/app:v1")
    result = strategy._validate_cr_image_url(cfg)
    assert isinstance(result, DeployResult)
    assert result.success is True
    assert result.error_code is None


def test_validate_cr_image_url_reports_config_invalid_for_unrendered_template(strategy):
    cfg = _valid_hybrid_config(
        cr_image_full_url="",
        cr_instance_name="cr-{{ account_id }}",
    )
    result = strategy._validate_cr_image_url(cfg)
    assert result.success is False
    assert result.error_code == ErrorCode.CONFIG_INVALID


def test_validate_cr_image_url_reports_config_invalid_for_auto_instance(strategy):
    cfg = _valid_hybrid_config(cr_image_full_url="", cr_instance_name=AUTO_CREATE_VE)
    result = strategy._validate_cr_image_url(cfg)
    assert result.success is False
    assert result.error_code == ErrorCode.CONFIG_INVALID
    assert "requires valid CR configuration" in result.error


def test_validate_cr_image_url_reports_config_invalid_for_empty_instance(strategy):
    cfg = _valid_hybrid_config(cr_image_full_url="", cr_instance_name="")
    result = strategy._validate_cr_image_url(cfg)
    assert result.success is False
    assert result.error_code == ErrorCode.CONFIG_INVALID
    assert "requires valid CR configuration" in result.error


def test_validate_cr_image_url_reports_resource_not_found_when_config_valid_but_no_url(
    strategy,
):
    # Valid instance name, no unrendered template, but no built image URL yet.
    cfg = _valid_hybrid_config(cr_image_full_url="")
    result = strategy._validate_cr_image_url(cfg)
    assert result.success is False
    assert result.error_code == ErrorCode.RESOURCE_NOT_FOUND


# ---------------------------------------------------------------------------
# _report_cr_skip_reason  (:406)
# ---------------------------------------------------------------------------


def test_report_cr_skip_reason_warns_about_unrendered_template(strategy):
    cfg = _valid_hybrid_config(cr_instance_name="cr-{{ account_id }}")
    strategy._report_cr_skip_reason("some reason", cfg)
    warnings = strategy.reporter.warnings
    assert any("unrendered template variables" in w for w in warnings)
    assert any("STS can fetch account_id" in w for w in warnings)


def test_report_cr_skip_reason_warns_about_auto_instance(strategy):
    cfg = _valid_hybrid_config(cr_instance_name=AUTO_CREATE_VE)
    strategy._report_cr_skip_reason("CR instance name is 'Auto'", cfg)
    warnings = strategy.reporter.warnings
    assert any("'Auto'" in w for w in warnings)
    assert any("configure a valid CR instance name" in w for w in warnings)


def test_report_cr_skip_reason_warns_generic_for_other_reasons(strategy):
    cfg = _valid_hybrid_config()
    strategy._report_cr_skip_reason("CR repository name is empty", cfg)
    warnings = strategy.reporter.warnings
    assert len(warnings) == 1
    assert "Invalid CR configuration, skipping push to CR" in warnings[0]
    assert "CR repository name is empty" in warnings[0]


# ---------------------------------------------------------------------------
# build  (:108)
# ---------------------------------------------------------------------------


def test_build_happy_path_populates_config_updates_when_push_skipped(
    strategy, common_config, monkeypatch
):
    ts = datetime(2026, 6, 30, 12, 0, 0)
    image = ImageInfo(
        repository="registry.example.com/ns/app", tag="v1", digest="sha256:deadbeef"
    )
    build_result = BuildResult(success=True, image=image, build_timestamp=ts)
    strategy._builder = _FakeBuilder(build_result)

    # Force the push decision so we never touch CR services.
    monkeypatch.setattr(
        strategy,
        "_should_push_to_cr",
        lambda strategy_config, cr_repo_name: (False, "skipped for test"),
    )

    # Empty repo name so _prepare_cr_config auto-fills from agent_name and the
    # change gets recorded into config_updates.
    cfg = _valid_hybrid_config(cr_repo_name="")
    result = strategy.build(common_config, cfg)

    assert result is build_result
    assert result.config_updates is not None
    updates = result.config_updates.to_dict()
    # cr_repo_name was empty in the config -> auto-filled from agent_name.
    assert updates["cr_repo_name"] == "my-agent"
    assert updates["full_image_name"] == "registry.example.com/ns/app:v1"
    assert updates["image_id"] == "sha256:deadbeef"
    assert updates["build_timestamp"] == ts.isoformat()


def test_build_reports_skip_reason_when_push_not_allowed(
    strategy, common_config, monkeypatch
):
    image = ImageInfo(repository="registry.example.com/ns/app", tag="v1")
    build_result = BuildResult(success=True, image=image)
    strategy._builder = _FakeBuilder(build_result)

    monkeypatch.setattr(
        strategy,
        "_should_push_to_cr",
        lambda strategy_config, cr_repo_name: (False, "CR repository name is empty"),
    )

    cfg = _valid_hybrid_config()
    strategy.build(common_config, cfg)

    # The generic skip-reason warning branch should have fired.
    assert any(
        "Invalid CR configuration, skipping push to CR" in w
        for w in strategy.reporter.warnings
    )


def test_build_short_circuits_and_returns_failed_result_without_config_updates(
    strategy, common_config
):
    failed = BuildResult(
        success=False, error="docker exploded", error_code=ErrorCode.BUILD_FAILED
    )
    strategy._builder = _FakeBuilder(failed)

    cfg = _valid_hybrid_config()
    result = strategy.build(common_config, cfg)

    assert result is failed
    assert result.success is False
    assert result.config_updates is None


# ---------------------------------------------------------------------------
# deploy  (:155)
# ---------------------------------------------------------------------------


def test_deploy_maps_runner_metadata_into_config_updates(strategy, common_config):
    deploy_result = DeployResult(
        success=True,
        endpoint_url="https://runtime.example.com/app",
        service_id="rt-123",
        metadata={
            "runtime_apikey": "sk-secret",
            "runtime_name": "auto-runtime",
            "runtime_apikey_name": "auto-key-name",
            "runtime_role_name": "auto-role",
        },
    )
    strategy._runner = _FakeRunner(deploy_result=deploy_result)

    # cr_image_full_url present -> _validate_cr_image_url passes.
    cfg = _valid_hybrid_config(cr_image_full_url="registry.example.com/ns/app:v1")
    result = strategy.deploy(common_config, cfg)

    assert result is deploy_result
    updates = result.config_updates.to_dict()
    assert updates["runtime_id"] == "rt-123"
    assert updates["runtime_endpoint"] == "https://runtime.example.com/app"
    assert updates["runtime_apikey"] == "sk-secret"
    assert updates["runtime_name"] == "auto-runtime"
    assert updates["runtime_apikey_name"] == "auto-key-name"
    assert updates["runtime_role_name"] == "auto-role"


def test_deploy_short_circuits_on_invalid_cr_image_url(strategy, common_config):
    # Runner must never be reached when validation fails.
    strategy._runner = _FakeRunner(deploy_result=DeployResult(success=True))

    cfg = _valid_hybrid_config(cr_image_full_url="", cr_instance_name=AUTO_CREATE_VE)
    result = strategy.deploy(common_config, cfg)

    assert result.success is False
    assert result.error_code == ErrorCode.CONFIG_INVALID
    assert strategy._runner.deploy_calls == []


# ---------------------------------------------------------------------------
# invoke / status / destroy  (:202 / :221 / :235)
# ---------------------------------------------------------------------------


def test_invoke_delegates_to_runner_and_returns_result_verbatim(
    strategy, common_config
):
    invoke_result = InvokeResult(success=True)
    runner = _FakeRunner(invoke_result=invoke_result)
    strategy._runner = runner

    cfg = _valid_hybrid_config()
    payload = {"prompt": "hello"}
    headers = {"X-Test": "1"}
    result = strategy.invoke(common_config, cfg, payload, headers=headers, stream=True)

    assert result is invoke_result
    assert len(runner.invoke_calls) == 1
    _, sent_payload, sent_headers, sent_stream = runner.invoke_calls[0]
    assert sent_payload == payload
    assert sent_headers == headers
    assert sent_stream is True


def test_status_delegates_to_runner_and_returns_result_verbatim(
    strategy, common_config
):
    status_result = StatusResult(success=True)
    runner = _FakeRunner(status_result=status_result)
    strategy._runner = runner

    cfg = _valid_hybrid_config()
    result = strategy.status(common_config, cfg)

    assert result is status_result
    assert len(runner.status_calls) == 1


def test_destroy_delegates_to_runner_and_returns_boolean_verbatim(
    strategy, common_config
):
    runner = _FakeRunner(destroy_result=True)
    strategy._runner = runner

    cfg = _valid_hybrid_config()
    result = strategy.destroy(common_config, cfg)

    assert result is True
    assert len(runner.destroy_calls) == 1
