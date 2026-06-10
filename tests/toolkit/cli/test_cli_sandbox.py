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


class _FakeCreateSessionResponse:
    user_session_id = "user-session-from-api"
    session_id = "session-from-api"
    endpoint = "https://sandbox.example.com"


class _FakeGetSessionResponse:
    user_session_id = None
    session_id = None
    endpoint = None


class _FakeToolsClient:
    last_request = None
    last_get_request = None
    response = _FakeCreateSessionResponse()
    get_response = _FakeGetSessionResponse()
    get_error = None
    create_call_count = 0
    get_call_count = 0

    def create_session(self, request):
        _FakeToolsClient.last_request = request
        _FakeToolsClient.create_call_count += 1
        return _FakeToolsClient.response

    def get_session(self, request):
        _FakeToolsClient.last_get_request = request
        _FakeToolsClient.get_call_count += 1
        if _FakeToolsClient.get_error:
            raise _FakeToolsClient.get_error
        return _FakeToolsClient.get_response


@pytest.fixture(autouse=True)
def _reset_fake_client():
    _FakeToolsClient.last_request = None
    _FakeToolsClient.last_get_request = None
    _FakeToolsClient.response = _FakeCreateSessionResponse()
    _FakeToolsClient.get_response = _FakeGetSessionResponse()
    _FakeToolsClient.get_error = None
    _FakeToolsClient.create_call_count = 0
    _FakeToolsClient.get_call_count = 0


def _patch_store_path(monkeypatch, tmp_path):
    import agentkit.toolkit.cli.sandbox.utils as sandbox_utils

    store_path = tmp_path / "sessions.json"
    monkeypatch.setattr(sandbox_utils, "_get_session_store_path", lambda: store_path)
    return store_path


def _patch_exec_session(monkeypatch, cli_exec, session, capture=None):
    def fake_ensure_sandbox_session(session_id=None, tool_id=None, **kwargs):
        if capture is not None:
            capture["session_id"] = session_id
            capture["tool_id"] = tool_id
            capture.update(kwargs)
        return session

    monkeypatch.setattr(
        cli_exec,
        "ensure_sandbox_session",
        fake_ensure_sandbox_session,
    )


def _patch_shell_session(monkeypatch, cli_shell, session):
    def fake_ensure_sandbox_session(session_id=None, tool_id=None, **_kwargs):
        return session

    monkeypatch.setattr(
        cli_shell,
        "ensure_sandbox_session",
        fake_ensure_sandbox_session,
    )


def test_ensure_sandbox_session_uses_env_defaults(monkeypatch, tmp_path) -> None:
    import agentkit.toolkit.cli.sandbox.session_create as session_create

    monkeypatch.setenv("AGENTKIT_SANDBOX_TOOL_ID", "tool-env")
    monkeypatch.setenv("AGENTKIT_SANDBOX_TTL", "60")
    monkeypatch.setattr(
        session_create,
        "AgentkitToolsClient",
        lambda: _FakeToolsClient(),
    )
    store_path = _patch_store_path(monkeypatch, tmp_path)

    result = session_create.ensure_sandbox_session()

    assert result == {
        "session_id": "user-session-from-api",
        "tool_id": "tool-env",
        "instance_id": "session-from-api",
        "endpoint": "https://sandbox.example.com",
    }
    assert json.loads(store_path.read_text(encoding="utf-8")) == {
        "user-session-from-api": result
    }

    request = _FakeToolsClient.last_request
    assert request.tool_id == "tool-env"
    assert request.ttl == 60
    assert request.ttl_unit == "second"
    assert request.user_session_id


def test_ensure_sandbox_session_options_override_env(monkeypatch, tmp_path) -> None:
    import agentkit.toolkit.cli.sandbox.session_create as session_create

    monkeypatch.setenv("AGENTKIT_SANDBOX_TOOL_ID", "tool-env")
    monkeypatch.setenv("AGENTKIT_SANDBOX_TTL", "60")
    monkeypatch.setattr(
        session_create,
        "AgentkitToolsClient",
        lambda: _FakeToolsClient(),
    )
    _patch_store_path(monkeypatch, tmp_path)

    session_create.ensure_sandbox_session(
        session_id="user-cli",
        tool_id="tool-cli",
        ttl=120,
    )

    request = _FakeToolsClient.last_request
    assert request.tool_id == "tool-cli"
    assert request.ttl == 120
    assert request.user_session_id == "user-cli"


def test_ensure_sandbox_session_passes_envs_to_create_session(
    monkeypatch,
    tmp_path,
) -> None:
    import agentkit.toolkit.cli.sandbox.session_create as session_create

    monkeypatch.setattr(
        session_create,
        "AgentkitToolsClient",
        lambda: _FakeToolsClient(),
    )
    _patch_store_path(monkeypatch, tmp_path)
    envs = session_create.build_model_envs(
        model_name="claude-sonnet-4",
        model_api_key="secret-key",
    )

    session_create.ensure_sandbox_session(
        session_id="user-cli",
        tool_id="tool-cli",
        envs=envs,
    )

    request_envs = _FakeToolsClient.last_request.envs
    assert [(item.key, item.value) for item in request_envs] == [
        ("OPENCODE_MODEL", "claude-sonnet-4"),
        ("CODEX_MODEL", "claude-sonnet-4"),
        ("ANTHROPIC_MODEL", "claude-sonnet-4"),
        ("OPENCODE_API_KEY", "secret-key"),
        ("CODEX_API_KEY", "secret-key"),
        ("ANTHROPIC_AUTH_TOKEN", "secret-key"),
    ]


def test_session_create_command_is_removed() -> None:
    from agentkit.toolkit.cli.cli import app

    result = runner.invoke(app, ["create"])

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_sandbox_command_group_is_removed() -> None:
    from agentkit.toolkit.cli.cli import app

    result = runner.invoke(app, ["sandbox"])

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_ensure_sandbox_session_reuses_existing_remote_session(
    monkeypatch,
    tmp_path,
) -> None:
    import agentkit.toolkit.cli.sandbox.session_create as session_create

    class ExistingResponse:
        user_session_id = "same-user-session"
        session_id = "session-existing"
        endpoint = "https://remote.example.com"

    monkeypatch.setattr(
        session_create,
        "AgentkitToolsClient",
        lambda: _FakeToolsClient(),
    )
    monkeypatch.delenv("AGENTKIT_SANDBOX_TOOL_ID", raising=False)
    store_path = _patch_store_path(monkeypatch, tmp_path)
    store_path.write_text(
        json.dumps(
            {
                "same-user-session": {
                    "session_id": "same-user-session",
                    "tool_id": "tool-stored",
                    "instance_id": "session-existing",
                    "endpoint": "https://local.example.com",
                }
            }
        ),
        encoding="utf-8",
    )
    _FakeToolsClient.get_response = ExistingResponse()

    result = session_create.ensure_sandbox_session(
        session_id="same-user-session",
    )

    assert _FakeToolsClient.create_call_count == 0
    assert _FakeToolsClient.get_call_count == 1
    assert _FakeToolsClient.last_get_request.tool_id == "tool-stored"
    assert _FakeToolsClient.last_get_request.session_id == "session-existing"
    assert result == {
        "session_id": "same-user-session",
        "tool_id": "tool-stored",
        "instance_id": "session-existing",
        "endpoint": "https://remote.example.com",
    }

    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert stored["same-user-session"] == result


def test_ensure_sandbox_session_recreates_when_remote_session_missing(
    monkeypatch,
    tmp_path,
) -> None:
    import agentkit.toolkit.cli.sandbox.session_create as session_create

    class NewResponse:
        user_session_id = "same-user-session"
        session_id = "session-new"
        endpoint = "https://new.example.com"

    monkeypatch.setattr(
        session_create,
        "AgentkitToolsClient",
        lambda: _FakeToolsClient(),
    )
    store_path = _patch_store_path(monkeypatch, tmp_path)
    store_path.write_text(
        json.dumps(
            {
                "same-user-session": {
                    "session_id": "same-user-session",
                    "tool_id": "tool-stored",
                    "instance_id": "session-old",
                    "endpoint": "https://old.example.com",
                }
            }
        ),
        encoding="utf-8",
    )
    _FakeToolsClient.get_error = Exception("Session not found")
    _FakeToolsClient.response = NewResponse()

    result = session_create.ensure_sandbox_session(
        session_id="same-user-session",
        tool_id="tool-new",
    )

    assert _FakeToolsClient.get_call_count == 1
    assert _FakeToolsClient.create_call_count == 1
    assert _FakeToolsClient.last_get_request.tool_id == "tool-new"
    assert _FakeToolsClient.last_get_request.session_id == "session-old"
    assert _FakeToolsClient.last_request.tool_id == "tool-new"

    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert list(stored) == ["same-user-session"]
    assert stored["same-user-session"] == {
        "session_id": "same-user-session",
        "tool_id": "tool-new",
        "instance_id": "session-new",
        "endpoint": "https://new.example.com",
    }
    assert result == stored["same-user-session"]


def test_cli_get_returns_stored_session(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app

    store_path = _patch_store_path(monkeypatch, tmp_path)
    stored_result = {
        "session_id": "user-1",
        "tool_id": "tool-1",
        "instance_id": "session-1",
        "endpoint": "https://sandbox.example.com",
    }
    store_path.write_text(
        json.dumps({"user-1": stored_result}, indent=2),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["get", "--session-id", "user-1"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == stored_result


def test_cli_get_requires_session_id() -> None:
    from agentkit.toolkit.cli.cli import app

    result = runner.invoke(app, ["get"])

    assert result.exit_code != 0
    assert "--session-id" in result.output


def test_cli_get_reports_missing_session(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app

    store_path = _patch_store_path(monkeypatch, tmp_path)
    store_path.write_text("{}", encoding="utf-8")

    result = runner.invoke(
        app,
        ["get", "--session-id", "missing-user"],
    )

    assert result.exit_code == 1
    assert "Sandbox session not found: missing-user" in result.output


def test_cli_shell_posts_to_session_endpoint(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_shell as cli_shell

    store_path = _patch_store_path(monkeypatch, tmp_path)
    stored_session = {
        "session_id": "user-1",
        "tool_id": "tool-1",
        "instance_id": "session-1",
        "endpoint": "https://sandbox.example.com/?token=abc",
    }
    store_path.write_text(
        json.dumps({"user-1": stored_session}),
        encoding="utf-8",
    )
    _patch_shell_session(monkeypatch, cli_shell, stored_session)

    captured = {}

    class FakeResponse:
        text = '{"success": true}'

        def json(self):
            return {
                "success": True,
                "message": "Command executed",
                "data": {
                    "session_id": "shell-1",
                    "command": "echo 123",
                    "status": "completed",
                    "output": "123",
                    "exit_code": 0,
                },
                "hint": None,
            }

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(cli_shell.requests, "post", fake_post)

    result = runner.invoke(
        app,
        [
            "shell",
            "--session-id",
            "user-1",
            "--command",
            "echo 123",
            "--exec-dir",
            "/workspace",
            "--shell-id",
            "shell-1",
        ],
    )

    assert result.exit_code == 0
    assert captured["url"] == "https://sandbox.example.com/v1/shell/exec?token=abc"
    assert captured["json"] == {
        "id": "shell-1",
        "exec_dir": "/workspace",
        "command": "echo 123",
    }

    payload = json.loads(result.output)
    assert payload["data"]["shell_id"] == "shell-1"
    assert "session_id" not in payload["data"]


def test_cli_shell_requires_command() -> None:
    from agentkit.toolkit.cli.cli import app

    result = runner.invoke(
        app,
        ["shell", "--session-id", "user-1"],
    )

    assert result.exit_code != 0
    assert "--command" in result.output


def test_cli_shell_creates_session_when_session_id_omitted(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.session_create as session_create
    import agentkit.toolkit.cli.sandbox.cli_shell as cli_shell

    store_path = _patch_store_path(monkeypatch, tmp_path)
    monkeypatch.setattr(
        session_create,
        "AgentkitToolsClient",
        lambda: _FakeToolsClient(),
    )
    captured = {}

    class FakeResponse:
        text = '{"success": true}'

        def json(self):
            return {
                "success": True,
                "message": "Command executed",
                "data": {
                    "session_id": "shell-1",
                    "command": "echo 123",
                    "status": "completed",
                    "output": "123",
                    "exit_code": 0,
                },
                "hint": None,
            }

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(cli_shell.requests, "post", fake_post)

    result = runner.invoke(
        app,
        [
            "shell",
            "--tool-id",
            "tool-cli",
            "--command",
            "echo 123",
        ],
    )

    assert result.exit_code == 0
    assert _FakeToolsClient.create_call_count == 1
    assert _FakeToolsClient.get_call_count == 0
    assert _FakeToolsClient.last_request.tool_id == "tool-cli"
    assert _FakeToolsClient.last_request.user_session_id
    assert captured["url"] == "https://sandbox.example.com/v1/shell/exec"

    payload = json.loads(result.output)
    assert payload["data"]["shell_id"] == "shell-1"
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert stored["user-session-from-api"]["tool_id"] == "tool-cli"


def test_cli_exec_connects_to_ws_endpoint(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_exec as cli_exec

    store_path = _patch_store_path(monkeypatch, tmp_path)
    stored_session = {
        "session_id": "user-1",
        "tool_id": "tool-1",
        "instance_id": "session-1",
        "endpoint": "https://sandbox.example.com/?token=abc",
    }
    store_path.write_text(
        json.dumps({"user-1": stored_session}),
        encoding="utf-8",
    )
    _patch_exec_session(monkeypatch, cli_exec, stored_session)
    captured = {}

    def fake_connect(ws_url, initial_command, on_shell_id=None):
        captured["ws_url"] = ws_url
        captured["initial_command"] = initial_command
        captured["on_shell_id"] = on_shell_id

    monkeypatch.setattr(cli_exec, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        ["exec", "--session-id", "user-1"],
    )

    assert result.exit_code == 0
    assert captured["ws_url"] == "ws://sandbox.example.com/v1/shell/ws?token=abc"
    assert captured["initial_command"] is None
    assert captured["on_shell_id"] is not None


def test_cli_exec_runs_command_option(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_exec as cli_exec

    store_path = _patch_store_path(monkeypatch, tmp_path)
    stored_session = {
        "session_id": "user-1",
        "tool_id": "tool-1",
        "instance_id": "session-1",
        "endpoint": "https://sandbox.example.com/?token=abc",
    }
    store_path.write_text(
        json.dumps({"user-1": stored_session}),
        encoding="utf-8",
    )
    _patch_exec_session(monkeypatch, cli_exec, stored_session)
    captured = {}

    def fake_connect(ws_url, initial_command, on_shell_id=None):
        captured["ws_url"] = ws_url
        captured["initial_command"] = initial_command

    monkeypatch.setattr(cli_exec, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        [
            "exec",
            "--session-id",
            "user-1",
            "--command",
            "codex",
        ],
    )

    assert result.exit_code == 0
    assert captured["ws_url"] == "ws://sandbox.example.com/v1/shell/ws?token=abc"
    assert captured["initial_command"] == "codex"


def test_cli_exec_passes_model_options_to_session_create(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_exec as cli_exec

    store_path = _patch_store_path(monkeypatch, tmp_path)
    stored_session = {
        "session_id": "user-1",
        "tool_id": "tool-1",
        "instance_id": "session-1",
        "endpoint": "https://sandbox.example.com/?token=abc",
    }
    store_path.write_text(
        json.dumps({"user-1": stored_session}),
        encoding="utf-8",
    )
    captured_session = {}
    _patch_exec_session(
        monkeypatch,
        cli_exec,
        stored_session,
        capture=captured_session,
    )

    def fake_connect(_ws_url, initial_command=None, on_shell_id=None):
        assert initial_command is None
        assert on_shell_id is not None

    monkeypatch.setattr(cli_exec, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        [
            "exec",
            "--session-id",
            "user-1",
            "--model-name",
            "claude-sonnet-4",
            "--model-api-key",
            "secret-key",
        ],
    )

    assert result.exit_code == 0
    assert captured_session["session_id"] == "user-1"
    assert [(item.key, item.value) for item in captured_session["envs"]] == [
        ("OPENCODE_MODEL", "claude-sonnet-4"),
        ("CODEX_MODEL", "claude-sonnet-4"),
        ("ANTHROPIC_MODEL", "claude-sonnet-4"),
        ("OPENCODE_API_KEY", "secret-key"),
        ("CODEX_API_KEY", "secret-key"),
        ("ANTHROPIC_AUTH_TOKEN", "secret-key"),
    ]


def test_cli_exec_rejects_model_base_url_option() -> None:
    from agentkit.toolkit.cli.cli import app

    result = runner.invoke(
        app,
        [
            "exec",
            "--model-base-url",
            "https://models.example.com",
        ],
    )

    assert result.exit_code != 0
    assert "No such option" in result.output


def test_cli_exec_supports_shell_id_and_empty_command(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_exec as cli_exec

    store_path = _patch_store_path(monkeypatch, tmp_path)
    stored_session = {
        "session_id": "user-1",
        "tool_id": "tool-1",
        "instance_id": "session-1",
        "endpoint": "http://sandbox.example.com/base?token=abc",
    }
    store_path.write_text(
        json.dumps({"user-1": stored_session}),
        encoding="utf-8",
    )
    _patch_exec_session(monkeypatch, cli_exec, stored_session)
    captured = {}

    def fake_connect(ws_url, initial_command, on_shell_id=None):
        captured["ws_url"] = ws_url
        captured["initial_command"] = initial_command

    monkeypatch.setattr(cli_exec, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        [
            "exec",
            "--session-id",
            "user-1",
            "--shell-id",
            "shell-1",
            "--command",
            "",
        ],
    )

    assert result.exit_code == 0
    assert (
        captured["ws_url"]
        == "ws://sandbox.example.com/base/v1/shell/ws?token=abc&session_id=shell-1"
    )
    assert captured["initial_command"] == ""


def test_cli_exec_does_not_restart_codex_for_shell_id(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_exec as cli_exec

    store_path = _patch_store_path(monkeypatch, tmp_path)
    stored_session = {
        "session_id": "user-1",
        "tool_id": "tool-1",
        "instance_id": "session-1",
        "endpoint": "https://sandbox.example.com/?token=abc",
    }
    store_path.write_text(
        json.dumps({"user-1": stored_session}),
        encoding="utf-8",
    )
    _patch_exec_session(monkeypatch, cli_exec, stored_session)
    captured = {}

    def fake_connect(ws_url, initial_command, on_shell_id=None):
        captured["ws_url"] = ws_url
        captured["initial_command"] = initial_command

    monkeypatch.setattr(cli_exec, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        [
            "exec",
            "--session-id",
            "user-1",
            "--shell-id",
            "shell-1",
        ],
    )

    assert result.exit_code == 0
    assert captured["initial_command"] is None


def test_cli_exec_clears_remote_shell_id_on_disconnect(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_exec as cli_exec

    store_path = _patch_store_path(monkeypatch, tmp_path)
    stored_session = {
        "session_id": "user-1",
        "tool_id": "tool-1",
        "instance_id": "session-1",
        "endpoint": "https://sandbox.example.com/?token=abc",
    }
    store_path.write_text(
        json.dumps({"user-1": stored_session}, indent=2),
        encoding="utf-8",
    )
    _patch_exec_session(monkeypatch, cli_exec, stored_session)

    def fake_connect(_ws_url, initial_command=None, on_shell_id=None):
        assert on_shell_id is not None
        on_shell_id("shell-from-ws")
        stored = json.loads(store_path.read_text(encoding="utf-8"))
        assert stored["user-1"]["terminal_shell_id"] == "shell-from-ws"

    monkeypatch.setattr(cli_exec, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        ["exec", "--session-id", "user-1"],
    )

    assert result.exit_code == 0
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert "terminal_shell_id" not in stored["user-1"]
    assert "Shell ID: shell-from-ws" in result.output


def test_cli_exec_does_not_clear_newer_shell_id(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_exec as cli_exec

    store_path = _patch_store_path(monkeypatch, tmp_path)
    stored_session = {
        "session_id": "user-1",
        "tool_id": "tool-1",
        "instance_id": "session-1",
        "endpoint": "https://sandbox.example.com/?token=abc",
    }
    store_path.write_text(
        json.dumps({"user-1": stored_session}, indent=2),
        encoding="utf-8",
    )
    _patch_exec_session(monkeypatch, cli_exec, stored_session)

    def fake_connect(_ws_url, initial_command=None, on_shell_id=None):
        assert on_shell_id is not None
        on_shell_id("shell-from-ws")
        stored = json.loads(store_path.read_text(encoding="utf-8"))
        stored["user-1"]["terminal_shell_id"] = "shell-from-newer-terminal"
        store_path.write_text(json.dumps(stored), encoding="utf-8")

    monkeypatch.setattr(cli_exec, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        ["exec", "--session-id", "user-1"],
    )

    assert result.exit_code == 0
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert stored["user-1"]["terminal_shell_id"] == "shell-from-newer-terminal"


def test_cli_exec_clears_shell_id_option_on_disconnect(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_exec as cli_exec

    store_path = _patch_store_path(monkeypatch, tmp_path)
    stored_session = {
        "session_id": "user-1",
        "tool_id": "tool-1",
        "instance_id": "session-1",
        "endpoint": "https://sandbox.example.com/?token=abc",
        "terminal_shell_id": "shell-from-cli",
    }
    store_path.write_text(
        json.dumps({"user-1": stored_session}, indent=2),
        encoding="utf-8",
    )
    _patch_exec_session(monkeypatch, cli_exec, stored_session)

    def fake_connect(_ws_url, initial_command=None, on_shell_id=None):
        assert on_shell_id is not None

    monkeypatch.setattr(cli_exec, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        [
            "exec",
            "--session-id",
            "user-1",
            "--shell-id",
            "shell-from-cli",
        ],
    )

    assert result.exit_code == 0
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert "terminal_shell_id" not in stored["user-1"]


def test_cli_exec_clears_stored_shell_id_on_disconnect(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.cli_exec as cli_exec

    store_path = _patch_store_path(monkeypatch, tmp_path)
    stored_session = {
        "session_id": "user-1",
        "tool_id": "tool-1",
        "instance_id": "session-1",
        "endpoint": "https://sandbox.example.com/?token=abc",
        "terminal_shell_id": "shell-from-store",
    }
    store_path.write_text(
        json.dumps({"user-1": stored_session}, indent=2),
        encoding="utf-8",
    )
    _patch_exec_session(monkeypatch, cli_exec, stored_session)

    def fake_connect(_ws_url, initial_command=None, on_shell_id=None):
        assert on_shell_id is not None

    monkeypatch.setattr(cli_exec, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        ["exec", "--session-id", "user-1"],
    )

    assert result.exit_code == 0
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert "terminal_shell_id" not in stored["user-1"]


def test_cli_exec_creates_session_when_session_id_omitted(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.session_create as session_create
    import agentkit.toolkit.cli.sandbox.cli_exec as cli_exec

    store_path = _patch_store_path(monkeypatch, tmp_path)
    monkeypatch.setattr(
        session_create,
        "AgentkitToolsClient",
        lambda: _FakeToolsClient(),
    )
    captured = {}

    def fake_connect(ws_url, initial_command, on_shell_id=None):
        captured["ws_url"] = ws_url
        captured["initial_command"] = initial_command
        captured["on_shell_id"] = on_shell_id

    monkeypatch.setattr(cli_exec, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        ["exec", "--tool-id", "tool-cli"],
    )

    assert result.exit_code == 0
    assert _FakeToolsClient.create_call_count == 1
    assert _FakeToolsClient.get_call_count == 0
    assert _FakeToolsClient.last_request.tool_id == "tool-cli"
    assert _FakeToolsClient.last_request.user_session_id
    assert captured["ws_url"] == "ws://sandbox.example.com/v1/shell/ws"
    assert captured["initial_command"] is None
    assert captured["on_shell_id"] is not None

    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert stored["user-session-from-api"]["tool_id"] == "tool-cli"


def test_cli_exec_requires_tool_id_for_new_session(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app

    monkeypatch.delenv("AGENTKIT_SANDBOX_TOOL_ID", raising=False)
    _patch_store_path(monkeypatch, tmp_path)

    result = runner.invoke(app, ["exec"])

    assert result.exit_code == 1
    assert "--tool-id or AGENTKIT_SANDBOX_TOOL_ID is required" in result.output


def test_cli_exec_detach_sequence_closes_websocket(monkeypatch) -> None:
    import json as json_module
    import threading
    import agentkit.toolkit.cli.sandbox.cli_exec as cli_exec

    class FakeStdin:
        def fileno(self):
            return 0

    class FakeWs:
        def __init__(self):
            self.messages = []
            self.closed = False

        def send(self, message):
            self.messages.append(json_module.loads(message))

        def close(self):
            self.closed = True

    ws = FakeWs()
    stop_event = threading.Event()

    monkeypatch.setattr(cli_exec.sys, "stdin", FakeStdin())
    monkeypatch.setattr(
        cli_exec.select,
        "select",
        lambda _r, _w, _x, _timeout: ([0], [], []),
    )
    monkeypatch.setattr(cli_exec.os, "read", lambda _fd, _size: b"pwd\x1d")

    cli_exec._stream_stdin(ws, stop_event)

    assert ws.messages == [{"type": "input", "data": "pwd"}]
    assert ws.closed is True
    assert stop_event.is_set()


@pytest.mark.parametrize("exit_command", [b"exit\n", b"exit()\n"])
def test_cli_exec_exit_command_closes_websocket(
    monkeypatch,
    exit_command,
) -> None:
    import threading
    import agentkit.toolkit.cli.sandbox.cli_exec as cli_exec

    class FakeStdin:
        def fileno(self):
            return 0

    class FakeWs:
        def __init__(self):
            self.messages = []
            self.closed = False

        def send(self, message):
            self.messages.append(message)

        def close(self):
            self.closed = True

    ws = FakeWs()
    stop_event = threading.Event()

    monkeypatch.setattr(cli_exec.sys, "stdin", FakeStdin())
    monkeypatch.setattr(
        cli_exec.select,
        "select",
        lambda _r, _w, _x, _timeout: ([0], [], []),
    )
    monkeypatch.setattr(cli_exec.os, "read", lambda _fd, _size: exit_command)

    cli_exec._stream_stdin(ws, stop_event)

    assert ws.messages == []
    assert ws.closed is True
    assert stop_event.is_set()


@pytest.mark.parametrize("exit_command", [b"previous input exit\r", b"x exit()\r"])
def test_cli_exec_exit_command_allows_prefix_buffer(
    monkeypatch,
    exit_command,
) -> None:
    import threading
    import agentkit.toolkit.cli.sandbox.cli_exec as cli_exec

    class FakeStdin:
        def fileno(self):
            return 0

    class FakeWs:
        def __init__(self):
            self.closed = False

        def send(self, _message):
            pass

        def close(self):
            self.closed = True

    ws = FakeWs()
    stop_event = threading.Event()

    monkeypatch.setattr(cli_exec.sys, "stdin", FakeStdin())
    monkeypatch.setattr(
        cli_exec.select,
        "select",
        lambda _r, _w, _x, _timeout: ([0], [], []),
    )
    monkeypatch.setattr(cli_exec.os, "read", lambda _fd, _size: exit_command)

    cli_exec._stream_stdin(ws, stop_event)

    assert ws.closed is True
    assert stop_event.is_set()
