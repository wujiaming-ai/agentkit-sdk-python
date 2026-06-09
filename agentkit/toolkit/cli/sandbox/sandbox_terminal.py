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

"""Interactive terminal command for sandbox CLI."""

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

from agentkit.toolkit.cli.sandbox.utils import (
    build_terminal_ws_url,
    error,
    get_session_result,
    remove_session_result_key,
    update_session_result,
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


def terminal_command(
    user_session_id: str = typer.Option(
        ...,
        "--user-session-id",
        help="User session ID to connect to.",
    ),
    command: Optional[str] = typer.Option(
        None,
        "--command",
        help=(
            "Initial command to run after the terminal is ready. "
            "Omit this option to connect without an initial command."
        ),
    ),
    shell_id: Optional[str] = typer.Option(
        None,
        "--shell-id",
        help="Existing shell terminal ID to connect to.",
    ),
) -> None:
    """Open a streaming sandbox terminal. Press Ctrl-] or type exit/exit()."""
    session = get_session_result(user_session_id)
    ws_url = build_terminal_ws_url(session.get("endpoint"), shell_id=shell_id)
    initial_command = command

    stored_shell_id = session.get("terminal_shell_id")
    if not isinstance(stored_shell_id, str) or not stored_shell_id:
        stored_shell_id = None
    current_shell_id: dict[str, str | None] = {"value": None}

    def on_shell_id(remote_shell_id: str) -> None:
        current_shell_id["value"] = remote_shell_id
        update_session_result(
            user_session_id,
            {"terminal_shell_id": remote_shell_id},
        )
        typer.echo(f"Shell ID: {remote_shell_id}", err=True)

    try:
        _connect_terminal(
            ws_url,
            initial_command=initial_command,
            on_shell_id=on_shell_id,
        )
    finally:
        cleanup_shell_id = current_shell_id["value"] or shell_id or stored_shell_id
        if cleanup_shell_id:
            remove_session_result_key(
                user_session_id,
                "terminal_shell_id",
                expected_value=cleanup_shell_id,
            )
