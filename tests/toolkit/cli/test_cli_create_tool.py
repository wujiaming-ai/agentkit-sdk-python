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

import json

import pytest
from typer.testing import CliRunner

runner = CliRunner()
_PLACEHOLDER_A = "example-value-a"
_PLACEHOLDER_B = "example-value-b"
_PLACEHOLDER_MODEL_VALUE = "example-model-value"


@pytest.fixture
def tool_store_path(monkeypatch, tmp_path):
    import agentkit.toolkit.cli.sandbox.tool_resolve as tool_resolve

    store_path = tmp_path / ".agentkit" / "sandbox" / "tools.json"
    monkeypatch.setattr(tool_resolve, "_get_tool_store_path", lambda: store_path)
    return store_path


@pytest.fixture(autouse=True)
def _use_tool_store_path(tool_store_path):
    pass


class _FakeCreateToolResponse:
    tool_id = "t-created"


class _FakeGetToolResponse:
    def __init__(self, status="Ready"):
        self.status = status
        self.tool_id = "t-created"
        self.name = "demo-tool"
        self.tool_type = "SkillEnv"
        self.tos_mount_config = None

    def model_dump(self, by_alias=False, exclude_none=False):
        payload = {
            "ToolId": self.tool_id,
            "Name": self.name,
            "Status": self.status,
            "ToolType": self.tool_type,
            "TosMountConfig": self.tos_mount_config,
        }
        if exclude_none:
            payload = {
                key: value for key, value in payload.items() if value is not None
            }
        return payload


class _FakeToolsClient:
    instances = []
    last_request = None
    get_statuses = ["Ready"]
    get_call_count = 0

    def __init__(self, **kwargs):
        self.access_key = kwargs.get("access" + "_key", "")
        self.secret_key = kwargs.get("secret" + "_key", "")
        self.region = kwargs.get("region", "")
        self.session_token = kwargs.get("session_token", "")
        _FakeToolsClient.instances.append(self)

    def create_tool(self, request):
        _FakeToolsClient.last_request = request
        return _FakeCreateToolResponse()

    def get_tool(self, request):
        _FakeToolsClient.get_call_count += 1
        if len(_FakeToolsClient.get_statuses) > 1:
            status = _FakeToolsClient.get_statuses.pop(0)
        else:
            status = _FakeToolsClient.get_statuses[0]
        return _FakeGetToolResponse(status=status)


class _FakeTOSService:
    instances = []
    generated_bucket_name = "agentkit-platform-123"

    def __init__(self, config):
        self.config = config
        self.created_directories = []
        self.bucket_path = ""
        self.local_mount_path = ""
        _FakeTOSService.instances.append(self)

    @staticmethod
    def generate_bucket_name():
        return _FakeTOSService.generated_bucket_name

    def build_mount_config(
        self,
        *,
        bucket_path,
        local_mount_path,
        read_only=False,
    ):
        from agentkit.toolkit.volcengine.services.tos_service import (
            TOSMountConfig,
            TOSMountCredentials,
            TOSMountPoint,
        )

        self.bucket_path = bucket_path
        self.local_mount_path = local_mount_path
        self.created_directories = [
            "sandbox-session/",
            "sandbox-session/default/",
            "sandbox-session/default/default/",
        ]
        return TOSMountConfig(
            credentials=TOSMountCredentials(
                **{
                    "access_key_id": _PLACEHOLDER_A,
                    "secret_" + "access_key": _PLACEHOLDER_B,
                }
            ),
            mount_points=[
                TOSMountPoint(
                    bucket_name=self.config.bucket,
                    bucket_path=bucket_path,
                    endpoint="http://tos-cn-beijing.ivolces.com",
                    local_mount_path=local_mount_path,
                    read_only=read_only,
                )
            ],
        )


def _reset_fake_tools_client():
    _FakeToolsClient.instances = []
    _FakeToolsClient.last_request = None
    _FakeToolsClient.get_statuses = ["Ready"]
    _FakeToolsClient.get_call_count = 0
    _FakeTOSService.instances = []


def test_create_command_uses_tos_service_and_default_region(
    monkeypatch,
    tool_store_path,
):
    from agentkit.toolkit.cli.cli import app
    from agentkit.toolkit.cli.sandbox import cli_create

    _reset_fake_tools_client()
    monkeypatch.delenv("AGENTKIT_SANDBOX_REGION", raising=False)
    monkeypatch.delenv("AGENTKIT_SANDBOX_TOS_REGION", raising=False)
    monkeypatch.setattr(cli_create, "AgentkitToolsClient", _FakeToolsClient)
    monkeypatch.setattr(cli_create, "TOSService", _FakeTOSService)

    result = runner.invoke(app, ["sandbox", "create", "--tool-name", "demo-tool"])

    assert result.exit_code == 0
    assert "工具创建成功" in result.output
    assert "工具ID：t-created" in result.output
    assert "状态：Ready" in result.output
    assert len(_FakeToolsClient.instances) == 1
    client = _FakeToolsClient.instances[0]
    assert client.access_key == ""
    assert client.secret_key == ""
    assert client.session_token == ""
    assert client.region == "cn-beijing"
    assert len(_FakeTOSService.instances) == 1
    assert _FakeTOSService.instances[0].config.bucket == "agentkit-platform-123"
    assert _FakeTOSService.instances[0].config.region == "cn-beijing"
    assert _FakeToolsClient.last_request.name == "demo-tool"
    assert _FakeToolsClient.last_request.tool_type == "CodeEnv"
    tos_config = _FakeToolsClient.last_request.tos_mount_config
    assert tos_config is not None
    assert tos_config.mount_points[0].bucket_name == "agentkit-platform-123"
    assert (
        tos_config.mount_points[0].bucket_path
        == "/sandbox-session/default/default"
    )
    assert _FakeToolsClient.get_call_count == 1
    assert json.loads(tool_store_path.read_text(encoding="utf-8")) == {
        "SkillEnv": {
            "ToolId": "t-created",
            "Name": "demo-tool",
            "Status": "Ready",
            "ToolType": "SkillEnv",
        }
    }


def test_create_command_uses_region_envs(monkeypatch):
    from agentkit.toolkit.cli.cli import app
    from agentkit.toolkit.cli.sandbox import cli_create

    _reset_fake_tools_client()
    monkeypatch.setenv("AGENTKIT_SANDBOX_REGION", " cn-shanghai ")
    monkeypatch.setenv("AGENTKIT_SANDBOX_TOS_REGION", " cn-guangzhou ")
    monkeypatch.setattr(cli_create, "AgentkitToolsClient", _FakeToolsClient)
    monkeypatch.setattr(cli_create, "TOSService", _FakeTOSService)

    result = runner.invoke(app, ["sandbox", "create", "--tool-name", "demo-tool"])

    assert result.exit_code == 0
    assert _FakeToolsClient.instances[0].region == "cn-shanghai"
    assert _FakeTOSService.instances[0].config.region == "cn-guangzhou"


def test_create_command_rejects_region_option(monkeypatch):
    from agentkit.toolkit.cli.cli import app
    from agentkit.toolkit.cli.sandbox import cli_create

    _reset_fake_tools_client()
    monkeypatch.setattr(cli_create, "AgentkitToolsClient", _FakeToolsClient)
    monkeypatch.setattr(cli_create, "TOSService", _FakeTOSService)

    result = runner.invoke(
        app,
        ["sandbox", "create", "--tool-name", "demo-tool", "--region", "cn-shanghai"],
    )

    assert result.exit_code != 0
    assert "No such option: --region" in result.output
    assert _FakeToolsClient.instances == []
    assert _FakeTOSService.instances == []


def test_create_command_help_omits_model_base_url_option():
    from agentkit.toolkit.cli.cli import app

    result = runner.invoke(app, ["sandbox", "create", "--help"])

    assert result.exit_code == 0
    assert "--model-base-url" not in result.output


def test_create_command_waits_until_tool_ready(monkeypatch):
    from agentkit.toolkit.cli.cli import app
    from agentkit.toolkit.cli.sandbox import cli_create

    _reset_fake_tools_client()
    _FakeToolsClient.get_statuses = ["Creating", "Ready"]
    monkeypatch.setattr(cli_create, "AgentkitToolsClient", _FakeToolsClient)
    monkeypatch.setattr(cli_create, "TOSService", _FakeTOSService)
    monkeypatch.setattr(cli_create.time, "sleep", lambda _seconds: None)

    result = runner.invoke(app, ["sandbox", "create", "--tool-name", "demo-tool"])

    assert result.exit_code == 0
    assert "工具状态：Creating" in result.output
    assert "工具状态：Ready" in result.output
    assert "工具创建成功" in result.output
    assert _FakeToolsClient.get_call_count == 2


def test_create_command_prints_sanitized_details_on_error(monkeypatch):
    from agentkit.toolkit.cli.cli import app
    from agentkit.toolkit.cli.sandbox import cli_create

    _reset_fake_tools_client()
    _FakeToolsClient.get_statuses = ["Error"]
    monkeypatch.setattr(cli_create, "AgentkitToolsClient", _FakeToolsClient)
    monkeypatch.setattr(cli_create, "TOSService", _FakeTOSService)

    result = runner.invoke(app, ["sandbox", "create", "--tool-name", "demo-tool"])

    assert result.exit_code == 1
    assert "entered terminal status: Error" in result.output
    assert "Summary:" in result.output
    assert "Name: demo-tool" in result.output
    assert _PLACEHOLDER_A not in result.output
    assert _PLACEHOLDER_B not in result.output


def test_build_create_tool_request_adds_tos_mount(monkeypatch):
    from agentkit.toolkit.cli.sandbox import cli_create

    _reset_fake_tools_client()
    monkeypatch.setattr(cli_create, "TOSService", _FakeTOSService)

    request = cli_create._build_create_tool_request(
        tool_type="CodeEnv",
        name="demo-tool",
        tos_bucket="my-bucket",
        tos_region="cn-beijing",
    )

    fake_service = _FakeTOSService.instances[0]
    assert fake_service.config.bucket == "my-bucket"
    assert fake_service.config.region == "cn-beijing"
    tos_config = request.tos_mount_config
    assert tos_config is not None
    assert tos_config.enable_tos is True
    assert tos_config.credentials.access_key_id == _PLACEHOLDER_A
    assert tos_config.credentials.secret_access_key == _PLACEHOLDER_B
    assert len(tos_config.mount_points) == 1
    mount_point = tos_config.mount_points[0]
    assert mount_point.bucket_name == "my-bucket"
    assert mount_point.bucket_path == "/sandbox-session/default/default"
    assert mount_point.endpoint == "http://tos-cn-beijing.ivolces.com"
    assert mount_point.local_mount_path == "/home/gem"
    assert mount_point.read_only is False
    assert fake_service.created_directories == [
        "sandbox-session/",
        "sandbox-session/default/",
        "sandbox-session/default/default/",
    ]
    assert request.authorizer_configuration is not None
    assert request.authorizer_configuration.key_auth is not None
    assert request.authorizer_configuration.key_auth.api_key_name
    assert request.authorizer_configuration.key_auth.api_key_location == "Header"
    assert request.network_configuration is not None
    assert request.network_configuration.enable_public_network is True
    assert request.network_configuration.enable_private_network is False


def test_build_create_tool_request_adds_model_envs(monkeypatch):
    from agentkit.toolkit.cli.sandbox import cli_create

    _reset_fake_tools_client()
    monkeypatch.setattr(cli_create, "TOSService", _FakeTOSService)

    request = cli_create._build_create_tool_request(
        tool_type="SkillEnv",
        name="demo-tool",
        tos_bucket="my-bucket",
        tos_region="cn-beijing",
        model_name="claude-sonnet-4",
        **{"model_" + "api_key": _PLACEHOLDER_MODEL_VALUE},
    )

    assert [(item.key, item.value) for item in request.envs] == [
        ("OPENCODE_MODEL", "claude-sonnet-4"),
        ("CODEX_MODEL", "claude-sonnet-4"),
        ("ANTHROPIC_MODEL", "claude-sonnet-4"),
        ("OPENCODE_API_KEY", _PLACEHOLDER_MODEL_VALUE),
        ("CODEX_API_KEY", _PLACEHOLDER_MODEL_VALUE),
        ("ANTHROPIC_AUTH_TOKEN", _PLACEHOLDER_MODEL_VALUE),
        ("OPENCODE_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        ("CODEX_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        ("MODEL_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        (
            "ANTHROPIC_BASE_URL",
            "https://ark.cn-beijing.volces.com/api/compatible",
        ),
        ("DISABLE_JUPYTER", "true"),
        ("DISABLE_CODE_SERVER", "true"),
        ("DISABLE_NODEJS_REPL", "true"),
    ]


def test_build_create_tool_request_adds_code_env_config_envs(monkeypatch):
    from agentkit.toolkit.cli.sandbox import cli_create

    _reset_fake_tools_client()
    monkeypatch.setattr(cli_create, "TOSService", _FakeTOSService)

    request = cli_create._build_create_tool_request(
        tool_type="CodeEnv",
        name="demo-tool",
        tos_bucket="my-bucket",
        tos_region="cn-beijing",
        model_name="claude-sonnet-4",
    )

    envs = {item.key: item.value for item in request.envs}
    assert envs["OPENCODE_DISABLE_AUTOUPDATE"] == "1"
    assert envs["HOME"] == "/home/gem"
    assert envs["CODEX_HOME"] == "/home/gem/.codex"
    config_toml = envs["CODEX_CONFIG_TOML"]
    assert 'model = "claude-sonnet-4"' in config_toml
    assert 'review_model = "claude-sonnet-4"' in config_toml
    assert 'model = "deepseek-v4-flash-260425"' not in config_toml
    assert (
        'model_catalog_json = "/home/gem/.codex/model-catalog.json"'
        in config_toml
    )
    assert "model_availability_nux" not in config_toml
    assert "gpt-5.5" not in config_toml
    assert 'web_search = "disabled"' in config_toml
    assert "model_context_window" not in config_toml
    assert "model_auto_compact_token_limit" not in config_toml
    assert "model_supports_reasoning_summaries" not in config_toml
    assert "model_reasoning_summary" not in config_toml
    assert "[tui]" in config_toml
    assert "show_tooltips = false" in config_toml
    assert '[projects."/home/gem"]' in config_toml
    assert 'trust_level = "trusted"' in config_toml
    assert "check_for_update_on_startup = false" in config_toml

    catalog_json = envs["CODEX_MODEL_CATALOG_JSON"]
    assert "\n  " in catalog_json
    catalog = json.loads(catalog_json)
    models = catalog["models"]
    assert models[0]["slug"] == "claude-sonnet-4"
    assert models[0]["display_name"] == "claude-sonnet-4"
    assert "deepseek-v4-flash-260425" in [model["slug"] for model in models]
    assert "doubao-seed-2-0-pro-260215" in [model["slug"] for model in models]
    assert models[0]["truncation_policy"] == {"mode": "tokens", "limit": 10000}


def test_build_create_tool_request_uses_model_api_key_env(monkeypatch):
    from agentkit.toolkit.cli.sandbox import cli_create

    _reset_fake_tools_client()
    monkeypatch.setenv("MODEL_API_KEY", _PLACEHOLDER_MODEL_VALUE)
    monkeypatch.setattr(cli_create, "TOSService", _FakeTOSService)

    request = cli_create._build_create_tool_request(
        tool_type="SkillEnv",
        name="demo-tool",
        tos_bucket="my-bucket",
        tos_region="cn-beijing",
    )

    assert ("OPENCODE_API_KEY", _PLACEHOLDER_MODEL_VALUE) in [
        (item.key, item.value) for item in request.envs
    ]
    assert ("CODEX_API_KEY", _PLACEHOLDER_MODEL_VALUE) in [
        (item.key, item.value) for item in request.envs
    ]
    assert ("ANTHROPIC_AUTH_TOKEN", _PLACEHOLDER_MODEL_VALUE) in [
        (item.key, item.value) for item in request.envs
    ]


def test_build_create_tool_request_model_api_key_option_overrides_env(
    monkeypatch,
):
    from agentkit.toolkit.cli.sandbox import cli_create

    _reset_fake_tools_client()
    monkeypatch.setenv("MODEL_API_KEY", "env-model-value")
    monkeypatch.setattr(cli_create, "TOSService", _FakeTOSService)

    request = cli_create._build_create_tool_request(
        tool_type="SkillEnv",
        name="demo-tool",
        tos_bucket="my-bucket",
        tos_region="cn-beijing",
        **{"model_" + "api_key": _PLACEHOLDER_MODEL_VALUE},
    )

    assert ("OPENCODE_API_KEY", _PLACEHOLDER_MODEL_VALUE) in [
        (item.key, item.value) for item in request.envs
    ]
    assert ("OPENCODE_API_KEY", "env-model-value") not in [
        (item.key, item.value) for item in request.envs
    ]


def test_create_command_rejects_model_base_url_option(monkeypatch):
    from agentkit.toolkit.cli.cli import app
    from agentkit.toolkit.cli.sandbox import cli_create

    _reset_fake_tools_client()
    monkeypatch.setattr(cli_create, "AgentkitToolsClient", _FakeToolsClient)
    monkeypatch.setattr(cli_create, "TOSService", _FakeTOSService)

    result = runner.invoke(
        app,
        [
            "sandbox",
            "create",
            "--model-base-url",
            "https://models.example.com",
        ],
    )

    assert result.exit_code != 0
    assert "No such option: --model-base-url" in result.output
    assert _FakeToolsClient.instances == []
    assert _FakeTOSService.instances == []


def test_build_create_tool_request_adds_default_model_base_url(monkeypatch):
    from agentkit.toolkit.cli.sandbox import cli_create

    _reset_fake_tools_client()
    monkeypatch.setattr(cli_create, "TOSService", _FakeTOSService)

    request = cli_create._build_create_tool_request(
        tool_type="SkillEnv",
        name="demo-tool",
        tos_bucket="my-bucket",
        tos_region="cn-beijing",
    )

    assert [(item.key, item.value) for item in request.envs] == [
        ("OPENCODE_MODEL", "deepseek-v4-flash-260425"),
        ("CODEX_MODEL", "deepseek-v4-flash-260425"),
        ("ANTHROPIC_MODEL", "deepseek-v4-flash-260425"),
        ("OPENCODE_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        ("CODEX_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        ("MODEL_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        (
            "ANTHROPIC_BASE_URL",
            "https://ark.cn-beijing.volces.com/api/compatible",
        ),
        ("DISABLE_JUPYTER", "true"),
        ("DISABLE_CODE_SERVER", "true"),
        ("DISABLE_NODEJS_REPL", "true"),
    ]


def test_tos_service_build_directory_keys():
    from agentkit.toolkit.volcengine.services.tos_service import TOSService

    assert TOSService.build_directory_keys("/sandbox-session/default/default") == [
        "sandbox-session/",
        "sandbox-session/default/",
        "sandbox-session/default/default/",
    ]


def test_tos_service_build_mount_endpoint_uses_default_private_endpoint():
    from agentkit.toolkit.volcengine.services.tos_service import TOSService

    assert (
        TOSService.build_mount_endpoint("cn-beijing")
        == "http://tos-cn-beijing.ivolces.com"
    )


def test_tos_service_build_mount_config_prepares_bucket_path():
    from types import SimpleNamespace

    from agentkit.toolkit.volcengine.services.tos_service import (
        TOSService,
        TOSServiceConfig,
    )

    service = object.__new__(TOSService)
    service.config = TOSServiceConfig(bucket="my-bucket", region="cn-beijing")
    service.credentials = SimpleNamespace(
        **{
            "access_key": _PLACEHOLDER_A,
            "secret_" + "key": _PLACEHOLDER_B,
        }
    )
    service.actual_region = "cn-beijing"
    created_buckets = []
    created_directories = []
    existing_keys = {"sandbox-session/"}
    service.bucket_exists = lambda: False
    service.create_bucket = lambda: created_buckets.append(service.config.bucket)
    service.object_exists = lambda key: key in existing_keys
    service.create_directory_marker = lambda key: created_directories.append(key)

    mount_config = service.build_mount_config(
        bucket_path="/sandbox-session/default/default",
        local_mount_path="/home/gem",
    )

    assert created_buckets == ["my-bucket"]
    assert created_directories == [
        "sandbox-session/default/",
        "sandbox-session/default/default/",
    ]
    assert mount_config.credentials.access_key_id == _PLACEHOLDER_A
    assert mount_config.credentials.secret_access_key == _PLACEHOLDER_B
    assert mount_config.mount_points[0].bucket_name == "my-bucket"
    assert mount_config.mount_points[0].endpoint == "http://tos-cn-beijing.ivolces.com"
