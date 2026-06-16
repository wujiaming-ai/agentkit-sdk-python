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

"""Interactive exec command for sandbox CLI."""

from __future__ import annotations

import json
import os
import select
import shutil
import signal
import sys
import termios
import threading
import tty
from contextlib import contextmanager
from typing import Iterator, Optional

import typer

from agentkit.toolkit.cli.sandbox.session_create import (
    SANDBOX_TOOL_ID_ENV,
    build_model_envs,
    ensure_sandbox_session,
)
from agentkit.toolkit.cli.sandbox.tool_resolve import SandboxToolType
from agentkit.toolkit.cli.sandbox.utils import (
    add_session_terminal_shell_id,
    build_terminal_ws_url,
    error,
    remove_session_terminal_shell_id,
)

DETACH_SEQUENCE = b"\x1d"
DETACH_HINT = "Ctrl-]"
LOCAL_EXIT_COMMANDS = {"exit", "exit()"}


def _terminal_size() -> dict[str, int]:
    size = shutil.get_terminal_size(fallback=(120, 40))
    return {"cols": size.columns, "rows": size.lines}


@contextmanager
def _raw_terminal_mode() -> Iterator[None]:
    if not sys.stdin.isatty():
        yield
        return

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _send_json(ws, payload: dict[str, object]) -> None:
    ws.send(json.dumps(payload, ensure_ascii=False))


def _send_resize(ws) -> None:
    _send_json(ws, {"type": "resize", "data": _terminal_size()})


def _stream_stdin(ws, stop_event: threading.Event) -> None:
    fd = sys.stdin.fileno()
    line_buffer = b""
    while not stop_event.is_set():
        readable, _, _ = select.select([fd], [], [], 0.1)
        if not readable:
            continue

        data = os.read(fd, 4096)
        if not data:
            break

        if DETACH_SEQUENCE in data:
            before_detach = data.split(DETACH_SEQUENCE, 1)[0]
            if before_detach:
                _send_json(
                    ws,
                    {
                        "type": "input",
                        "data": before_detach.decode("utf-8", errors="ignore"),
                    },
                )
            stop_event.set()
            ws.close()
            break

        normalized_data = data.replace(b"\r", b"\n")
        line_buffer += normalized_data
        has_newline = b"\n" in normalized_data
        line_parts = line_buffer.split(b"\n")
        complete_lines = line_parts[:-1]
        line_buffer = line_parts[-1]
        if has_newline and any(_is_local_exit_line(line) for line in complete_lines):
            stop_event.set()
            ws.close()
            break

        _send_json(
            ws,
            {
                "type": "input",
                "data": data.decode("utf-8", errors="ignore"),
            },
        )


def _is_local_exit_line(line: bytes) -> bool:
    text = line.decode("utf-8", errors="ignore").strip()
    if text in LOCAL_EXIT_COMMANDS:
        return True
    return any(text.endswith(f" {command}") for command in LOCAL_EXIT_COMMANDS)


def _write_output(data: object) -> None:
    if data is None:
        return
    sys.stdout.write(str(data))
    sys.stdout.flush()


def _connect_terminal(
    ws_url: str,
    initial_command: Optional[str],
    on_shell_id=None,
) -> None:
    try:
        import websocket
    except ImportError:
        error(
            "websocket-client is required. "
            "Install with: pip install websocket-client"
        )

    stop_event = threading.Event()
    initial_command_sent = {"value": False}
    websocket_app = None

    def send_initial_command(ws) -> None:
        if initial_command_sent["value"] or not initial_command:
            return
        initial_command_sent["value"] = True
        _send_json(ws, {"type": "input", "data": f"{initial_command}\n"})

    def on_open(ws) -> None:
        _send_resize(ws)
        thread = threading.Thread(
            target=_stream_stdin,
            args=(ws, stop_event),
            daemon=True,
        )
        thread.start()

    def on_message(ws, message: str) -> None:
        try:
            payload = json.loads(message)
        except json.JSONDecodeError:
            _write_output(message)
            return

        message_type = payload.get("type")
        if message_type == "session_id":
            shell_id = payload.get("data")
            if isinstance(shell_id, str) and on_shell_id:
                on_shell_id(shell_id)
            return
        if message_type == "output":
            _write_output(payload.get("data"))
            return
        if message_type == "ready":
            send_initial_command(ws)
            return
        if message_type == "ping":
            timestamp = payload.get("timestamp", payload.get("data"))
            _send_json(ws, {"type": "pong", "data": {"timestamp": timestamp}})
            return
        if message_type == "error":
            _write_output(f"\r\n{payload.get('data')}\r\n")

    def on_close(_ws, _status_code, _message) -> None:
        stop_event.set()

    def on_error(_ws, exc: Exception) -> None:
        stop_event.set()
        error(str(exc))

    def on_resize(_signum, _frame) -> None:
        if websocket_app and websocket_app.sock and websocket_app.sock.connected:
            _send_resize(websocket_app)

    websocket_app = websocket.WebSocketApp(
        ws_url,
        on_open=on_open,
        on_message=on_message,
        on_close=on_close,
        on_error=on_error,
    )

    sigwinch = getattr(signal, "SIGWINCH", None)
    previous_sigwinch = signal.getsignal(sigwinch) if sigwinch else None
    if sigwinch:
        signal.signal(sigwinch, on_resize)
    try:
        typer.echo(
            f"Press {DETACH_HINT} or type exit/exit() to detach.",
            err=True,
        )
        with _raw_terminal_mode():
            websocket_app.run_forever()
    except KeyboardInterrupt:
        websocket_app.close()
    finally:
        stop_event.set()
        if sigwinch:
            signal.signal(sigwinch, previous_sigwinch)


def exec_command(
    session_id: Optional[str] = typer.Option(
        None,
        "--session-id",
        help=(
            "Sandbox session ID. Defaults to a generated UUID and creates "
            "a sandbox session when needed."
        ),
    ),
    tool_id: Optional[str] = typer.Option(
        None,
        "--tool-id",
        help=f"Sandbox tool ID. Defaults to {SANDBOX_TOOL_ID_ENV}.",
    ),
    tool_type: SandboxToolType = typer.Option(
        SandboxToolType.CODE_ENV,
        "--tool-type",
        help="Sandbox tool type to resolve when --tool-id is omitted.",
    ),
    command: Optional[str] = typer.Option(
        None,
        "--command",
        help=(
            "Initial command to run after the exec session is ready. "
            "Omit this option to connect without an initial command."
        ),
    ),
    shell_id: Optional[str] = typer.Option(
        None,
        "--shell-id",
        help="Existing shell terminal ID to connect to.",
    ),
    model_name: Optional[str] = typer.Option(
        None,
        "--model-name",
        help=(
            "Model name to inject into OPENCODE_MODEL, CODEX_MODEL, "
            "and ANTHROPIC_MODEL when creating a sandbox session."
        ),
    ),
    model_api_key: Optional[str] = typer.Option(
        None,
        "--model-api-key",
        help=(
            "Model API key to inject into OPENCODE_API_KEY, CODEX_API_KEY, "
            "and ANTHROPIC_AUTH_TOKEN when creating a sandbox session."
        ),
    ),
) -> None:
    """Open a streaming sandbox exec session. Press Ctrl-] or type exit/exit()."""
    try:
        session = ensure_sandbox_session(
            session_id=session_id,
            tool_id=tool_id,
            tool_type=tool_type.value,
            envs=build_model_envs(
                model_name=model_name,
                model_api_key=model_api_key,
            ),
        )
    except typer.Exit:
        raise
    except Exception as exc:
        error(str(exc))

    session_id = session.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        error("Sandbox session missing session_id")

    ws_url = build_terminal_ws_url(session.get("endpoint"), shell_id=shell_id)
    initial_command = command

    cleanup_shell_ids: list[str] = []
    cleanup_shell_ids_lock = threading.Lock()

    def remember_cleanup_shell_id(remote_shell_id: str) -> None:
        with cleanup_shell_ids_lock:
            if remote_shell_id not in cleanup_shell_ids:
                cleanup_shell_ids.append(remote_shell_id)

    if shell_id:
        add_session_terminal_shell_id(session_id, shell_id)
        remember_cleanup_shell_id(shell_id)

    def on_shell_id(remote_shell_id: str) -> None:
        add_session_terminal_shell_id(session_id, remote_shell_id)
        remember_cleanup_shell_id(remote_shell_id)
        typer.echo(f"Shell ID: {remote_shell_id}", err=True)

    try:
        _connect_terminal(
            ws_url,
            initial_command=initial_command,
            on_shell_id=on_shell_id,
        )
    finally:
        with cleanup_shell_ids_lock:
            shell_ids_to_cleanup = list(cleanup_shell_ids)
        for cleanup_shell_id in shell_ids_to_cleanup:
            remove_session_terminal_shell_id(session_id, cleanup_shell_id)
