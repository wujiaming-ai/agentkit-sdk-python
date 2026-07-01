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

"""Offline unit coverage for pure/dispatch helpers on VeAgentkitRuntimeRunner.

These tests exercise the small deterministic pieces of
``agentkit.toolkit.runners.ve_agentkit`` that do not require any real I/O:

- ``_build_authorizer_config_for_create``: JWT vs key_auth branch shape.
- ``_needs_runtime_update``: pure diff over image URL + env vars, including the
  system-env-prefix filtering and the several env-object representations.
- ``get_public_endpoint_of_runtime``: static endpoint selection.
- ``_prepare_runtime_config``: AUTO/empty triggers name generation.
- ``deploy``: config-missing guard and the create-vs-update dispatch.
- ``destroy``: skip / NotFound / other-error branches.
- ``status``: not-deployed short-circuit, status mapping, and health probe.

Network-config building and invoke-time auth inference are covered by sibling
files (test_ve_agentkit_network_config.py / test_ve_agentkit_invoke_auth_infer.py)
and are intentionally not duplicated here.
"""

from __future__ import annotations

import pytest

from agentkit.toolkit.runners import ve_agentkit
from agentkit.toolkit.runners.ve_agentkit import (
    VeAgentkitRuntimeRunner,
    VeAgentkitRunnerConfig,
)
from agentkit.toolkit.config import CommonConfig, AUTO_CREATE_VE
from agentkit.toolkit.errors import ErrorCode


# ---------------------------------------------------------------------------
# Hand-rolled fakes
# ---------------------------------------------------------------------------


class _FakeEnvLower:
    """Env object exposing lowercase key/value attributes."""

    def __init__(self, key, value):
        self.key = key
        self.value = value


class _FakeEnvUpper:
    """Env object exposing uppercase Key/Value attributes (compat path)."""

    def __init__(self, Key, Value):
        self.Key = Key
        self.Value = Value


class _FakeNetworkConfiguration:
    def __init__(self, network_type, endpoint):
        self.network_type = network_type
        self.endpoint = endpoint


class _FakeRuntime:
    """Minimal stand-in for a GetRuntime response object.

    Only the attributes touched by the methods under test are populated;
    callers pass whatever subset they need.
    """

    def __init__(
        self,
        *,
        artifact_url="",
        artifact_type="image",
        envs=None,
        status="Ready",
        name="rt-name",
        network_configurations=None,
        authorizer_configuration=None,
    ):
        self.artifact_url = artifact_url
        self.artifact_type = artifact_type
        self.envs = envs
        self.status = status
        self.name = name
        self.network_configurations = network_configurations or []
        self.authorizer_configuration = authorizer_configuration


class _FakeRuntimeClient:
    """Runtime client returning a canned runtime; records delete calls."""

    def __init__(self, runtime=None, delete_error=None):
        self._runtime = runtime
        self._delete_error = delete_error
        self.deleted = []

    def get_runtime(self, req):
        return self._runtime

    def delete_runtime(self, req):
        self.deleted.append(req)
        if self._delete_error is not None:
            raise self._delete_error
        return object()


class _FakeVeIAM:
    """Fake VeIAM that always reports the role is ensured."""

    ensure_ok = True

    def __init__(self, region=""):
        self.region = region

    def ensure_role_for_agentkit(self, role_name):
        return type(self).ensure_ok


class _FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.fixture(autouse=True)
def _reset_fake_veiam_state():
    # _FakeVeIAM holds a class-level toggle; reset before each test.
    _FakeVeIAM.ensure_ok = True
    yield
    _FakeVeIAM.ensure_ok = True


def _make_config(**kwargs) -> VeAgentkitRunnerConfig:
    kwargs.setdefault("common_config", CommonConfig(agent_name="myagent"))
    return VeAgentkitRunnerConfig(**kwargs)


# ---------------------------------------------------------------------------
# _build_authorizer_config_for_create
# ---------------------------------------------------------------------------


def test_build_authorizer_config_for_create_uses_jwt_branch_with_discovery_and_clients():
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(
        runtime_auth_type="custom_jwt",
        runtime_jwt_discovery_url="https://issuer/.well-known/openid-configuration",
        runtime_jwt_allowed_clients=["client-a", "client-b"],
    )

    authorizer = runner._build_authorizer_config_for_create(cfg)

    assert authorizer.custom_jwt_authorizer is not None
    assert authorizer.key_auth is None
    assert (
        authorizer.custom_jwt_authorizer.discovery_url
        == "https://issuer/.well-known/openid-configuration"
    )
    assert authorizer.custom_jwt_authorizer.allowed_clients == ["client-a", "client-b"]


def test_build_authorizer_config_for_create_jwt_branch_maps_empty_clients_to_none():
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(
        runtime_auth_type="custom_jwt",
        runtime_jwt_discovery_url="https://issuer/.well-known/openid-configuration",
        runtime_jwt_allowed_clients=[],
    )

    authorizer = runner._build_authorizer_config_for_create(cfg)

    assert authorizer.custom_jwt_authorizer is not None
    assert authorizer.custom_jwt_authorizer.allowed_clients is None


def test_build_authorizer_config_for_create_uses_key_auth_branch_with_name_and_location():
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(
        runtime_auth_type="key_auth",
        runtime_apikey_name="my-key-name",
    )

    authorizer = runner._build_authorizer_config_for_create(cfg)

    assert authorizer.key_auth is not None
    assert authorizer.custom_jwt_authorizer is None
    assert authorizer.key_auth.api_key_name == "my-key-name"
    # API_KEY_LOCATION module constant is "HEADER".
    assert authorizer.key_auth.api_key_location == ve_agentkit.API_KEY_LOCATION == "HEADER"


# ---------------------------------------------------------------------------
# _needs_runtime_update
# ---------------------------------------------------------------------------


def test_needs_runtime_update_detects_image_url_change():
    runner = VeAgentkitRuntimeRunner()
    runtime = _FakeRuntime(artifact_url="repo/img:old", envs=[])
    cfg = _make_config(image_url="repo/img:new", runtime_envs={})

    needs_update, reason = runner._needs_runtime_update(runtime, cfg)

    assert needs_update is True
    assert "Image URL changed" in reason


def test_needs_runtime_update_returns_false_when_image_and_envs_match():
    runner = VeAgentkitRuntimeRunner()
    runtime = _FakeRuntime(
        artifact_url="repo/img:v1",
        envs=[_FakeEnvLower("FOO", "bar")],
    )
    cfg = _make_config(image_url="repo/img:v1", runtime_envs={"FOO": "bar"})

    needs_update, reason = runner._needs_runtime_update(runtime, cfg)

    assert needs_update is False
    assert reason == "No configuration changes"


def test_needs_runtime_update_reports_added_removed_and_modified_env_vars():
    runner = VeAgentkitRuntimeRunner()
    runtime = _FakeRuntime(
        artifact_url="repo/img:v1",
        envs=[_FakeEnvLower("KEEP", "same"), _FakeEnvLower("DROP", "x"), _FakeEnvLower("CHANGE", "old")],
    )
    cfg = _make_config(
        image_url="repo/img:v1",
        runtime_envs={"KEEP": "same", "CHANGE": "new", "ADD": "y"},
    )

    needs_update, reason = runner._needs_runtime_update(runtime, cfg)

    assert needs_update is True
    assert "Added env vars: ADD" in reason
    assert "Removed env vars: DROP" in reason
    assert "Modified env vars: CHANGE" in reason


def test_needs_runtime_update_ignores_system_env_prefixes():
    runner = VeAgentkitRuntimeRunner()
    # Runtime carries only system-injected env vars; config carries none.
    runtime = _FakeRuntime(
        artifact_url="repo/img:v1",
        envs=[
            _FakeEnvLower("OTEL_EXPORTER", "x"),
            _FakeEnvLower("ENABLE_APMPLUS", "true"),
            _FakeEnvLower("APMPLUS_APP_KEY", "k"),
        ],
    )
    cfg = _make_config(image_url="repo/img:v1", runtime_envs={})

    needs_update, reason = runner._needs_runtime_update(runtime, cfg)

    assert needs_update is False
    assert reason == "No configuration changes"


def test_needs_runtime_update_reads_uppercase_env_attributes():
    runner = VeAgentkitRuntimeRunner()
    runtime = _FakeRuntime(
        artifact_url="repo/img:v1",
        envs=[_FakeEnvUpper(Key="FOO", Value="bar")],
    )
    cfg_match = _make_config(image_url="repo/img:v1", runtime_envs={"FOO": "bar"})
    cfg_diff = _make_config(image_url="repo/img:v1", runtime_envs={"FOO": "other"})

    assert runner._needs_runtime_update(runtime, cfg_match)[0] is False
    assert runner._needs_runtime_update(runtime, cfg_diff)[0] is True


def test_needs_runtime_update_reads_dict_env_entries():
    runner = VeAgentkitRuntimeRunner()
    runtime = _FakeRuntime(
        artifact_url="repo/img:v1",
        envs=[{"key": "FOO", "value": "bar"}, {"Key": "BAZ", "Value": "qux"}],
    )
    cfg = _make_config(
        image_url="repo/img:v1",
        runtime_envs={"FOO": "bar", "BAZ": "qux"},
    )

    needs_update, reason = runner._needs_runtime_update(runtime, cfg)

    assert needs_update is False
    assert reason == "No configuration changes"


# ---------------------------------------------------------------------------
# get_public_endpoint_of_runtime
# ---------------------------------------------------------------------------


def test_get_public_endpoint_returns_public_network_endpoint():
    runtime = _FakeRuntime(
        network_configurations=[
            _FakeNetworkConfiguration("private", "https://private/"),
            _FakeNetworkConfiguration("public", "https://public/"),
        ]
    )

    endpoint = VeAgentkitRuntimeRunner.get_public_endpoint_of_runtime(runtime)

    assert endpoint == "https://public/"


def test_get_public_endpoint_returns_empty_when_no_public_network():
    runtime = _FakeRuntime(
        network_configurations=[
            _FakeNetworkConfiguration("private", "https://private/"),
        ]
    )

    endpoint = VeAgentkitRuntimeRunner.get_public_endpoint_of_runtime(runtime)

    assert endpoint == ""


# ---------------------------------------------------------------------------
# _prepare_runtime_config
# ---------------------------------------------------------------------------


def test_prepare_runtime_config_generates_names_when_auto_and_returns_true(monkeypatch):
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(
        runtime_name=AUTO_CREATE_VE,
        runtime_role_name=AUTO_CREATE_VE,
        runtime_apikey_name=AUTO_CREATE_VE,
    )

    monkeypatch.setattr(
        ve_agentkit, "generate_runtime_name", lambda agent_name: "gen-runtime"
    )
    monkeypatch.setattr(
        ve_agentkit, "generate_runtime_role_name", lambda: "gen-role"
    )
    monkeypatch.setattr(ve_agentkit, "generate_apikey_name", lambda: "gen-apikey")

    result = runner._prepare_runtime_config(cfg)

    assert result is True
    assert cfg.runtime_name == "gen-runtime"
    assert cfg.runtime_role_name == "gen-role"
    assert cfg.runtime_apikey_name == "gen-apikey"


def test_prepare_runtime_config_generates_names_when_empty_strings(monkeypatch):
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(
        runtime_name="",
        runtime_role_name="",
        runtime_apikey_name="",
    )

    monkeypatch.setattr(
        ve_agentkit, "generate_runtime_name", lambda agent_name: "gen-runtime"
    )
    monkeypatch.setattr(ve_agentkit, "generate_runtime_role_name", lambda: "gen-role")
    monkeypatch.setattr(ve_agentkit, "generate_apikey_name", lambda: "gen-apikey")

    result = runner._prepare_runtime_config(cfg)

    assert result is True
    assert cfg.runtime_name == "gen-runtime"
    assert cfg.runtime_role_name == "gen-role"
    assert cfg.runtime_apikey_name == "gen-apikey"


def test_prepare_runtime_config_returns_false_when_generation_raises(monkeypatch):
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(runtime_name=AUTO_CREATE_VE)

    def _boom(agent_name):
        raise RuntimeError("cannot generate")

    monkeypatch.setattr(ve_agentkit, "generate_runtime_name", _boom)

    result = runner._prepare_runtime_config(cfg)

    assert result is False


# ---------------------------------------------------------------------------
# deploy: config-missing guard + create-vs-update dispatch
# ---------------------------------------------------------------------------


def test_deploy_without_image_url_returns_config_missing():
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(image_url="")

    result = runner.deploy(cfg)

    assert result.success is False
    assert result.error_code == ErrorCode.CONFIG_MISSING


def test_deploy_dispatches_to_create_when_runtime_id_is_auto(monkeypatch):
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(image_url="repo/img:v1", runtime_id=AUTO_CREATE_VE)

    monkeypatch.setattr(ve_agentkit, "VeIAM", _FakeVeIAM)
    monkeypatch.setattr(runner, "_prepare_runtime_config", lambda c: True)
    monkeypatch.setattr(
        runner, "_get_runtime_client", lambda region="": _FakeRuntimeClient()
    )

    create_sentinel = object()
    monkeypatch.setattr(runner, "_create_new_runtime", lambda c: create_sentinel)
    monkeypatch.setattr(
        runner, "_update_existing_runtime", lambda c: pytest.fail("should not update")
    )

    result = runner.deploy(cfg)

    assert result is create_sentinel


def test_deploy_dispatches_to_update_when_runtime_id_is_valid(monkeypatch):
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(image_url="repo/img:v1", runtime_id="rt-existing")

    monkeypatch.setattr(ve_agentkit, "VeIAM", _FakeVeIAM)
    monkeypatch.setattr(runner, "_prepare_runtime_config", lambda c: True)
    monkeypatch.setattr(
        runner, "_get_runtime_client", lambda region="": _FakeRuntimeClient()
    )

    update_sentinel = object()
    monkeypatch.setattr(
        runner, "_create_new_runtime", lambda c: pytest.fail("should not create")
    )
    monkeypatch.setattr(runner, "_update_existing_runtime", lambda c: update_sentinel)

    result = runner.deploy(cfg)

    assert result is update_sentinel


def test_deploy_returns_permission_denied_when_iam_role_not_ensured(monkeypatch):
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(image_url="repo/img:v1", runtime_id="rt-existing")

    _FakeVeIAM.ensure_ok = False
    monkeypatch.setattr(ve_agentkit, "VeIAM", _FakeVeIAM)
    monkeypatch.setattr(runner, "_prepare_runtime_config", lambda c: True)

    result = runner.deploy(cfg)

    assert result.success is False
    assert result.error_code == ErrorCode.PERMISSION_DENIED


def test_deploy_returns_config_invalid_when_prepare_fails(monkeypatch):
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(image_url="repo/img:v1")

    monkeypatch.setattr(runner, "_prepare_runtime_config", lambda c: False)

    result = runner.deploy(cfg)

    assert result.success is False
    assert result.error_code == ErrorCode.CONFIG_INVALID


# ---------------------------------------------------------------------------
# destroy
# ---------------------------------------------------------------------------


def test_destroy_skips_and_succeeds_when_runtime_id_is_auto():
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(runtime_id=AUTO_CREATE_VE)

    assert runner.destroy(cfg) is True


def test_destroy_skips_and_succeeds_when_runtime_id_is_empty():
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(runtime_id="")

    assert runner.destroy(cfg) is True


def test_destroy_calls_client_and_returns_true_on_success(monkeypatch):
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(runtime_id="rt-1")
    client = _FakeRuntimeClient()

    monkeypatch.setattr(runner, "_get_runtime_client", lambda region="": client)

    assert runner.destroy(cfg) is True
    assert len(client.deleted) == 1


def test_destroy_treats_not_found_error_as_success(monkeypatch):
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(runtime_id="rt-1")
    client = _FakeRuntimeClient(
        delete_error=Exception("InvalidAgentKitRuntime.NotFound: gone")
    )

    monkeypatch.setattr(runner, "_get_runtime_client", lambda region="": client)

    assert runner.destroy(cfg) is True


def test_destroy_returns_false_on_other_errors(monkeypatch):
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(runtime_id="rt-1")
    client = _FakeRuntimeClient(delete_error=Exception("SomethingElse.Boom"))

    monkeypatch.setattr(runner, "_get_runtime_client", lambda region="": client)

    assert runner.destroy(cfg) is False


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_short_circuits_as_not_deployed_when_runtime_id_is_auto():
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(runtime_id=AUTO_CREATE_VE)

    result = runner.status(cfg)

    assert result.success is True
    assert result.status == "not_deployed"


def test_status_maps_ready_to_running(monkeypatch):
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(
        runtime_id="rt-1",
        runtime_auth_type="custom_jwt",  # skip ping probe
    )
    runtime = _FakeRuntime(status="Ready", network_configurations=[])
    monkeypatch.setattr(
        runner, "_get_runtime_client", lambda region="": _FakeRuntimeClient(runtime)
    )

    result = runner.status(cfg)

    assert result.success is True
    assert result.status == "running"


def test_status_maps_error_status_to_error(monkeypatch):
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(runtime_id="rt-1", runtime_auth_type="custom_jwt")
    runtime = _FakeRuntime(status="Error", network_configurations=[])
    monkeypatch.setattr(
        runner, "_get_runtime_client", lambda region="": _FakeRuntimeClient(runtime)
    )

    result = runner.status(cfg)

    assert result.status == "error"


def test_status_lowercases_other_statuses(monkeypatch):
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(runtime_id="rt-1", runtime_auth_type="custom_jwt")
    runtime = _FakeRuntime(status="Updating", network_configurations=[])
    monkeypatch.setattr(
        runner, "_get_runtime_client", lambda region="": _FakeRuntimeClient(runtime)
    )

    result = runner.status(cfg)

    assert result.status == "updating"


def test_status_health_is_healthy_when_ping_returns_200(monkeypatch):
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(
        runtime_id="rt-1",
        runtime_auth_type="key_auth",
        runtime_apikey="k-1",
    )
    runtime = _FakeRuntime(
        status="Ready",
        network_configurations=[_FakeNetworkConfiguration("public", "https://pub/")],
    )
    monkeypatch.setattr(
        runner, "_get_runtime_client", lambda region="": _FakeRuntimeClient(runtime)
    )
    monkeypatch.setattr(
        ve_agentkit.requests, "get", lambda url, headers=None, timeout=None: _FakeResponse(200)
    )

    result = runner.status(cfg)

    assert result.status == "running"
    assert result.health == "healthy"
    assert result.metadata["ping_status"] is True


def test_status_health_is_unhealthy_when_ping_returns_500(monkeypatch):
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(
        runtime_id="rt-1",
        runtime_auth_type="key_auth",
        runtime_apikey="k-1",
    )
    runtime = _FakeRuntime(
        status="Ready",
        network_configurations=[_FakeNetworkConfiguration("public", "https://pub/")],
    )
    monkeypatch.setattr(
        runner, "_get_runtime_client", lambda region="": _FakeRuntimeClient(runtime)
    )
    monkeypatch.setattr(
        ve_agentkit.requests, "get", lambda url, headers=None, timeout=None: _FakeResponse(500)
    )

    result = runner.status(cfg)

    assert result.health == "unhealthy"
    assert result.metadata["ping_status"] is False


def test_status_returns_not_found_when_client_raises_not_found(monkeypatch):
    runner = VeAgentkitRuntimeRunner()
    cfg = _make_config(runtime_id="rt-1", runtime_auth_type="custom_jwt")

    class _RaisingClient:
        def get_runtime(self, req):
            raise Exception("InvalidAgentKitRuntime.NotFound: gone")

    monkeypatch.setattr(runner, "_get_runtime_client", lambda region="": _RaisingClient())

    result = runner.status(cfg)

    assert result.success is False
    assert result.status == "not_found"
    assert result.error_code == ErrorCode.RESOURCE_NOT_FOUND
