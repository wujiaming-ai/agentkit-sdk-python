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


class _FakeToolsClient:
    last_request = None
    response = _FakeCreateSessionResponse()

    def create_session(self, request):
        _FakeToolsClient.last_request = request
        return _FakeToolsClient.response


@pytest.fixture(autouse=True)
def _reset_fake_client():
    _FakeToolsClient.last_request = None
    _FakeToolsClient.response = _FakeCreateSessionResponse()


def _patch_store_path(monkeypatch, tmp_path):
    import agentkit.toolkit.cli.sandbox.utils as sandbox_utils

    store_path = tmp_path / "sessions.json"
    monkeypatch.setattr(sandbox_utils, "_get_session_store_path", lambda: store_path)
    return store_path


def test_sandbox_create_uses_env_defaults(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.sandbox_create as sandbox_create

    monkeypatch.setenv("AGENTKIT_SANDBOX_TOOL_ID", "tool-env")
    monkeypatch.setenv("AGENTKIT_SANDBOX_TTL", "60")
    monkeypatch.setattr(
        sandbox_create,
        "AgentkitToolsClient",
        lambda: _FakeToolsClient(),
    )
    store_path = _patch_store_path(monkeypatch, tmp_path)

    result = runner.invoke(app, ["sandbox", "create"])

    assert result.exit_code == 0
    create_result = {
        "user_session_id": "user-session-from-api",
        "tool_id": "tool-env",
        "session_id": "session-from-api",
        "endpoint": "https://sandbox.example.com",
    }
    assert json.loads(result.output) == create_result
    assert json.loads(store_path.read_text(encoding="utf-8")) == {
        "user-session-from-api": create_result
    }

    request = _FakeToolsClient.last_request
    assert request.tool_id == "tool-env"
    assert request.ttl == 60
    assert request.ttl_unit == "second"
    assert request.user_session_id


def test_sandbox_create_cli_options_override_env(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.sandbox_create as sandbox_create

    monkeypatch.setenv("AGENTKIT_SANDBOX_TOOL_ID", "tool-env")
    monkeypatch.setenv("AGENTKIT_SANDBOX_TTL", "60")
    monkeypatch.setattr(
        sandbox_create,
        "AgentkitToolsClient",
        lambda: _FakeToolsClient(),
    )
    _patch_store_path(monkeypatch, tmp_path)

    result = runner.invoke(
        app,
        [
            "sandbox",
            "create",
            "--tool-id",
            "tool-cli",
            "--ttl",
            "120",
            "--user-session-id",
            "user-cli",
        ],
    )

    assert result.exit_code == 0
    request = _FakeToolsClient.last_request
    assert request.tool_id == "tool-cli"
    assert request.ttl == 120
    assert request.user_session_id == "user-cli"


def test_sandbox_create_requires_tool_id(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app

    monkeypatch.delenv("AGENTKIT_SANDBOX_TOOL_ID", raising=False)
    _patch_store_path(monkeypatch, tmp_path)

    result = runner.invoke(app, ["sandbox", "create"])

    assert result.exit_code == 1
    assert "--tool-id or AGENTKIT_SANDBOX_TOOL_ID is required" in result.output


def test_sandbox_create_upserts_by_user_session_id(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.sandbox_create as sandbox_create

    class FirstResponse:
        user_session_id = "same-user-session"
        session_id = "session-old"
        endpoint = "https://old.example.com"

    class SecondResponse:
        user_session_id = "same-user-session"
        session_id = "session-new"
        endpoint = "https://new.example.com"

    monkeypatch.setattr(
        sandbox_create,
        "AgentkitToolsClient",
        lambda: _FakeToolsClient(),
    )
    store_path = _patch_store_path(monkeypatch, tmp_path)

    _FakeToolsClient.response = FirstResponse()
    first = runner.invoke(
        app,
        [
            "sandbox",
            "create",
            "--tool-id",
            "tool-old",
            "--user-session-id",
            "same-user-session",
        ],
    )
    assert first.exit_code == 0

    _FakeToolsClient.response = SecondResponse()
    second = runner.invoke(
        app,
        [
            "sandbox",
            "create",
            "--tool-id",
            "tool-new",
            "--user-session-id",
            "same-user-session",
        ],
    )
    assert second.exit_code == 0

    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert list(stored) == ["same-user-session"]
    assert stored["same-user-session"] == {
        "user_session_id": "same-user-session",
        "tool_id": "tool-new",
        "session_id": "session-new",
        "endpoint": "https://new.example.com",
    }


def test_sandbox_get_returns_stored_session(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app

    store_path = _patch_store_path(monkeypatch, tmp_path)
    stored_result = {
        "user_session_id": "user-1",
        "tool_id": "tool-1",
        "session_id": "session-1",
        "endpoint": "https://sandbox.example.com",
    }
    store_path.write_text(
        json.dumps({"user-1": stored_result}, indent=2),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["sandbox", "get", "--user-session-id", "user-1"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == stored_result


def test_sandbox_get_requires_user_session_id() -> None:
    from agentkit.toolkit.cli.cli import app

    result = runner.invoke(app, ["sandbox", "get"])

    assert result.exit_code != 0
    assert "--user-session-id" in result.output


def test_sandbox_get_reports_missing_session(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app

    store_path = _patch_store_path(monkeypatch, tmp_path)
    store_path.write_text("{}", encoding="utf-8")

    result = runner.invoke(
        app,
        ["sandbox", "get", "--user-session-id", "missing-user"],
    )

    assert result.exit_code == 1
    assert "Sandbox session not found: missing-user" in result.output


def test_sandbox_exec_posts_to_session_endpoint(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.sandbox_exec as sandbox_exec

    store_path = _patch_store_path(monkeypatch, tmp_path)
    store_path.write_text(
        json.dumps(
            {
                "user-1": {
                    "user_session_id": "user-1",
                    "tool_id": "tool-1",
                    "session_id": "session-1",
                    "endpoint": "https://sandbox.example.com/?token=abc",
                }
            }
        ),
        encoding="utf-8",
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

    monkeypatch.setattr(sandbox_exec.requests, "post", fake_post)

    result = runner.invoke(
        app,
        [
            "sandbox",
            "exec",
            "--user-session-id",
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


def test_sandbox_exec_requires_command() -> None:
    from agentkit.toolkit.cli.cli import app

    result = runner.invoke(
        app,
        ["sandbox", "exec", "--user-session-id", "user-1"],
    )

    assert result.exit_code != 0
    assert "--command" in result.output


def test_sandbox_terminal_connects_to_ws_endpoint(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.sandbox_terminal as sandbox_terminal

    store_path = _patch_store_path(monkeypatch, tmp_path)
    store_path.write_text(
        json.dumps(
            {
                "user-1": {
                    "user_session_id": "user-1",
                    "tool_id": "tool-1",
                    "session_id": "session-1",
                    "endpoint": "https://sandbox.example.com/?token=abc",
                }
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_connect(ws_url, initial_command, on_shell_id=None):
        captured["ws_url"] = ws_url
        captured["initial_command"] = initial_command
        captured["on_shell_id"] = on_shell_id

    monkeypatch.setattr(sandbox_terminal, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        ["sandbox", "terminal", "--user-session-id", "user-1"],
    )

    assert result.exit_code == 0
    assert captured["ws_url"] == "ws://sandbox.example.com/v1/shell/ws?token=abc"
    assert captured["initial_command"] is None
    assert captured["on_shell_id"] is not None


def test_sandbox_terminal_runs_command_option(monkeypatch, tmp_path) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.sandbox_terminal as sandbox_terminal

    store_path = _patch_store_path(monkeypatch, tmp_path)
    store_path.write_text(
        json.dumps(
            {
                "user-1": {
                    "user_session_id": "user-1",
                    "tool_id": "tool-1",
                    "session_id": "session-1",
                    "endpoint": "https://sandbox.example.com/?token=abc",
                }
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_connect(ws_url, initial_command, on_shell_id=None):
        captured["ws_url"] = ws_url
        captured["initial_command"] = initial_command

    monkeypatch.setattr(sandbox_terminal, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        [
            "sandbox",
            "terminal",
            "--user-session-id",
            "user-1",
            "--command",
            "codex",
        ],
    )

    assert result.exit_code == 0
    assert captured["ws_url"] == "ws://sandbox.example.com/v1/shell/ws?token=abc"
    assert captured["initial_command"] == "codex"


def test_sandbox_terminal_supports_shell_id_and_empty_command(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.sandbox_terminal as sandbox_terminal

    store_path = _patch_store_path(monkeypatch, tmp_path)
    store_path.write_text(
        json.dumps(
            {
                "user-1": {
                    "user_session_id": "user-1",
                    "tool_id": "tool-1",
                    "session_id": "session-1",
                    "endpoint": "http://sandbox.example.com/base?token=abc",
                }
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_connect(ws_url, initial_command, on_shell_id=None):
        captured["ws_url"] = ws_url
        captured["initial_command"] = initial_command

    monkeypatch.setattr(sandbox_terminal, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        [
            "sandbox",
            "terminal",
            "--user-session-id",
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


def test_sandbox_terminal_does_not_restart_codex_for_shell_id(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.sandbox_terminal as sandbox_terminal

    store_path = _patch_store_path(monkeypatch, tmp_path)
    store_path.write_text(
        json.dumps(
            {
                "user-1": {
                    "user_session_id": "user-1",
                    "tool_id": "tool-1",
                    "session_id": "session-1",
                    "endpoint": "https://sandbox.example.com/?token=abc",
                }
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_connect(ws_url, initial_command, on_shell_id=None):
        captured["ws_url"] = ws_url
        captured["initial_command"] = initial_command

    monkeypatch.setattr(sandbox_terminal, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        [
            "sandbox",
            "terminal",
            "--user-session-id",
            "user-1",
            "--shell-id",
            "shell-1",
        ],
    )

    assert result.exit_code == 0
    assert captured["initial_command"] is None


def test_sandbox_terminal_clears_remote_shell_id_on_disconnect(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.sandbox_terminal as sandbox_terminal

    store_path = _patch_store_path(monkeypatch, tmp_path)
    stored_session = {
        "user_session_id": "user-1",
        "tool_id": "tool-1",
        "session_id": "session-1",
        "endpoint": "https://sandbox.example.com/?token=abc",
    }
    store_path.write_text(
        json.dumps({"user-1": stored_session}, indent=2),
        encoding="utf-8",
    )

    def fake_connect(_ws_url, initial_command=None, on_shell_id=None):
        assert on_shell_id is not None
        on_shell_id("shell-from-ws")
        stored = json.loads(store_path.read_text(encoding="utf-8"))
        assert stored["user-1"]["terminal_shell_id"] == "shell-from-ws"

    monkeypatch.setattr(sandbox_terminal, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        ["sandbox", "terminal", "--user-session-id", "user-1"],
    )

    assert result.exit_code == 0
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert "terminal_shell_id" not in stored["user-1"]
    assert "Shell ID: shell-from-ws" in result.output


def test_sandbox_terminal_does_not_clear_newer_shell_id(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.sandbox_terminal as sandbox_terminal

    store_path = _patch_store_path(monkeypatch, tmp_path)
    stored_session = {
        "user_session_id": "user-1",
        "tool_id": "tool-1",
        "session_id": "session-1",
        "endpoint": "https://sandbox.example.com/?token=abc",
    }
    store_path.write_text(
        json.dumps({"user-1": stored_session}, indent=2),
        encoding="utf-8",
    )

    def fake_connect(_ws_url, initial_command=None, on_shell_id=None):
        assert on_shell_id is not None
        on_shell_id("shell-from-ws")
        stored = json.loads(store_path.read_text(encoding="utf-8"))
        stored["user-1"]["terminal_shell_id"] = "shell-from-newer-terminal"
        store_path.write_text(json.dumps(stored), encoding="utf-8")

    monkeypatch.setattr(sandbox_terminal, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        ["sandbox", "terminal", "--user-session-id", "user-1"],
    )

    assert result.exit_code == 0
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert stored["user-1"]["terminal_shell_id"] == "shell-from-newer-terminal"


def test_sandbox_terminal_clears_shell_id_option_on_disconnect(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.sandbox_terminal as sandbox_terminal

    store_path = _patch_store_path(monkeypatch, tmp_path)
    stored_session = {
        "user_session_id": "user-1",
        "tool_id": "tool-1",
        "session_id": "session-1",
        "endpoint": "https://sandbox.example.com/?token=abc",
        "terminal_shell_id": "shell-from-cli",
    }
    store_path.write_text(
        json.dumps({"user-1": stored_session}, indent=2),
        encoding="utf-8",
    )

    def fake_connect(_ws_url, initial_command=None, on_shell_id=None):
        assert on_shell_id is not None

    monkeypatch.setattr(sandbox_terminal, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        [
            "sandbox",
            "terminal",
            "--user-session-id",
            "user-1",
            "--shell-id",
            "shell-from-cli",
        ],
    )

    assert result.exit_code == 0
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert "terminal_shell_id" not in stored["user-1"]


def test_sandbox_terminal_clears_stored_shell_id_on_disconnect(
    monkeypatch,
    tmp_path,
) -> None:
    from agentkit.toolkit.cli.cli import app
    import agentkit.toolkit.cli.sandbox.sandbox_terminal as sandbox_terminal

    store_path = _patch_store_path(monkeypatch, tmp_path)
    stored_session = {
        "user_session_id": "user-1",
        "tool_id": "tool-1",
        "session_id": "session-1",
        "endpoint": "https://sandbox.example.com/?token=abc",
        "terminal_shell_id": "shell-from-store",
    }
    store_path.write_text(
        json.dumps({"user-1": stored_session}, indent=2),
        encoding="utf-8",
    )

    def fake_connect(_ws_url, initial_command=None, on_shell_id=None):
        assert on_shell_id is not None

    monkeypatch.setattr(sandbox_terminal, "_connect_terminal", fake_connect)

    result = runner.invoke(
        app,
        ["sandbox", "terminal", "--user-session-id", "user-1"],
    )

    assert result.exit_code == 0
    stored = json.loads(store_path.read_text(encoding="utf-8"))
    assert "terminal_shell_id" not in stored["user-1"]


def test_sandbox_terminal_requires_user_session_id() -> None:
    from agentkit.toolkit.cli.cli import app

    result = runner.invoke(app, ["sandbox", "terminal"])

    assert result.exit_code != 0
    assert "--user-session-id" in result.output


def test_sandbox_terminal_detach_sequence_closes_websocket(monkeypatch) -> None:
    import json as json_module
    import threading
    import agentkit.toolkit.cli.sandbox.sandbox_terminal as sandbox_terminal

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

    monkeypatch.setattr(sandbox_terminal.sys, "stdin", FakeStdin())
    monkeypatch.setattr(
        sandbox_terminal.select,
        "select",
        lambda _r, _w, _x, _timeout: ([0], [], []),
    )
    monkeypatch.setattr(sandbox_terminal.os, "read", lambda _fd, _size: b"pwd\x1d")

    sandbox_terminal._stream_stdin(ws, stop_event)

    assert ws.messages == [{"type": "input", "data": "pwd"}]
    assert ws.closed is True
    assert stop_event.is_set()


@pytest.mark.parametrize("exit_command", [b"exit\n", b"exit()\n"])
def test_sandbox_terminal_exit_command_closes_websocket(
    monkeypatch,
    exit_command,
) -> None:
    import threading
    import agentkit.toolkit.cli.sandbox.sandbox_terminal as sandbox_terminal

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

    monkeypatch.setattr(sandbox_terminal.sys, "stdin", FakeStdin())
    monkeypatch.setattr(
        sandbox_terminal.select,
        "select",
        lambda _r, _w, _x, _timeout: ([0], [], []),
    )
    monkeypatch.setattr(sandbox_terminal.os, "read", lambda _fd, _size: exit_command)

    sandbox_terminal._stream_stdin(ws, stop_event)

    assert ws.messages == []
    assert ws.closed is True
    assert stop_event.is_set()


@pytest.mark.parametrize("exit_command", [b"previous input exit\r", b"x exit()\r"])
def test_sandbox_terminal_exit_command_allows_prefix_buffer(
    monkeypatch,
    exit_command,
) -> None:
    import threading
    import agentkit.toolkit.cli.sandbox.sandbox_terminal as sandbox_terminal

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

    monkeypatch.setattr(sandbox_terminal.sys, "stdin", FakeStdin())
    monkeypatch.setattr(
        sandbox_terminal.select,
        "select",
        lambda _r, _w, _x, _timeout: ([0], [], []),
    )
    monkeypatch.setattr(sandbox_terminal.os, "read", lambda _fd, _size: exit_command)

    sandbox_terminal._stream_stdin(ws, stop_event)

    assert ws.closed is True
    assert stop_event.is_set()
