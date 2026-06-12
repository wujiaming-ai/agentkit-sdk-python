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

from pathlib import Path

import pytest
from typer.testing import CliRunner


class _FakeInvokeExecutor:
    last_kwargs = None

    def __init__(self, reporter=None):
        self.reporter = reporter

    def execute(self, **kwargs):
        from agentkit.toolkit.models import InvokeResult

        _FakeInvokeExecutor.last_kwargs = kwargs
        return InvokeResult(success=True, response={"ok": True}, is_streaming=False)


runner = CliRunner()


def test_invoke_direct_mode_rejects_config_file(monkeypatch) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.executors as executors

    monkeypatch.setattr(executors, "InvokeExecutor", _FakeInvokeExecutor)

    result = runner.invoke(
        app,
        [
            "invoke",
            "--runtime-id",
            "r-123",
            "--config-file",
            str(Path("agentkit.yaml")),
            "hi",
        ],
    )
    assert result.exit_code != 0


def test_invoke_direct_mode_endpoint_requires_auth(monkeypatch) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.executors as executors

    monkeypatch.setattr(executors, "InvokeExecutor", _FakeInvokeExecutor)

    result = runner.invoke(app, ["invoke", "--endpoint", "https://example.com", "hi"])
    assert result.exit_code != 0


def test_invoke_direct_mode_endpoint_with_authorization_header(monkeypatch) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.executors as executors

    monkeypatch.setattr(executors, "InvokeExecutor", _FakeInvokeExecutor)

    result = runner.invoke(
        app,
        [
            "invoke",
            "--endpoint",
            "https://example.com",
            "--headers",
            '{"Authorization":"Bearer token"}',
            "hi",
        ],
    )
    assert result.exit_code == 0

    kwargs = _FakeInvokeExecutor.last_kwargs
    assert kwargs is not None
    assert kwargs.get("config_file") is None
    cfg = kwargs.get("config_dict")
    assert isinstance(cfg, dict)
    cloud = cfg["launch_types"]["cloud"]
    assert cloud["runtime_endpoint"] == "https://example.com"
    assert cloud["runtime_auth_type"] == "custom_jwt"


def test_invoke_direct_mode_runtime_id_with_region(monkeypatch) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.executors as executors

    monkeypatch.setattr(executors, "InvokeExecutor", _FakeInvokeExecutor)

    result = runner.invoke(
        app,
        ["invoke", "--runtime-id", "r-123", "--region", "cn-beijing", "hi"],
    )
    assert result.exit_code == 0

    cfg = _FakeInvokeExecutor.last_kwargs["config_dict"]
    cloud = cfg["launch_types"]["cloud"]
    assert cloud["runtime_id"] == "r-123"
    assert cloud["region"] == "cn-beijing"


def test_invoke_direct_mode_runtime_id_rejects_apikey(monkeypatch) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.executors as executors

    monkeypatch.setattr(executors, "InvokeExecutor", _FakeInvokeExecutor)

    result = runner.invoke(
        app,
        ["invoke", "--runtime-id", "r-123", "--apikey", "x", "hi"],
    )
    assert result.exit_code != 0
    normalized_output = " ".join(result.output.split())
    assert "--apikey cannot be used together with --runtime-id" in normalized_output
    assert (
        "--runtime-id mode resolves the Runtime and infers its auth type"
        in normalized_output
    )
    assert (
        "If you want to pass an API key manually, use --endpoint together with --apikey."
        in normalized_output
    )


def test_invoke_direct_mode_a2a_flag_wraps_message(monkeypatch) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.executors as executors

    monkeypatch.setattr(executors, "InvokeExecutor", _FakeInvokeExecutor)

    result = runner.invoke(
        app,
        [
            "invoke",
            "--endpoint",
            "https://example.com",
            "--apikey",
            "k",
            "--a2a",
            "hi",
        ],
    )
    assert result.exit_code == 0

    cfg = _FakeInvokeExecutor.last_kwargs["config_dict"]
    assert cfg["common"]["agent_type"] == "a2a"
    payload = _FakeInvokeExecutor.last_kwargs["payload"]
    assert isinstance(payload, dict)
    assert payload.get("jsonrpc") == "2.0"


def test_invoke_direct_mode_payload_jsonrpc_sets_a2a_agent_type(monkeypatch) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.executors as executors

    monkeypatch.setattr(executors, "InvokeExecutor", _FakeInvokeExecutor)

    result = runner.invoke(
        app,
        [
            "invoke",
            "--runtime-id",
            "r-123",
            "--payload",
            '{"jsonrpc":"2.0","method":"message/stream","params":{},"id":1}',
        ],
    )
    assert result.exit_code == 0

    cfg = _FakeInvokeExecutor.last_kwargs["config_dict"]
    assert cfg["common"]["agent_type"] == "a2a"
