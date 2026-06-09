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

"""Shared helpers for sandbox CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit, urlunsplit

import typer

SANDBOX_SESSION_STORE_PATH = Path(".agentkit") / "sandbox" / "sessions.json"
SANDBOX_EXEC_ROUTE = "/v1/shell/exec"
SANDBOX_TERMINAL_ROUTE = "/v1/shell/ws"
SANDBOX_EXEC_TIMEOUT_SECONDS = 300


def error(message: str) -> NoReturn:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(1)


def echo_json(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


def _get_session_store_path() -> Path:
    return Path.cwd() / SANDBOX_SESSION_STORE_PATH


def load_session_store(path: Path) -> dict[str, object]:
    if not path.exists():
        error(f"Sandbox session store not found: {path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        error(f"Invalid sandbox session store {path}: {exc}")

    if not isinstance(data, dict):
        error(f"Invalid sandbox session store {path}: expected JSON object")

    return data


def save_session_result(result: dict[str, object]) -> None:
    user_session_id = result.get("user_session_id")
    if not isinstance(user_session_id, str) or not user_session_id:
        error("CreateSession response missing user_session_id")

    path = _get_session_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        data = load_session_store(path)
    else:
        data = {}

    data[user_session_id] = result
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def update_session_result(
    user_session_id: str,
    updates: dict[str, object],
) -> dict[str, object]:
    path = _get_session_store_path()
    data = load_session_store(path)

    result = data.get(user_session_id)
    if result is None:
        error(f"Sandbox session not found: {user_session_id}")
    if not isinstance(result, dict):
        error(f"Invalid sandbox session record: {user_session_id}")

    result.update(updates)
    data[user_session_id] = result
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result


def remove_session_result_key(
    user_session_id: str,
    key: str,
    expected_value: object | None = None,
) -> dict[str, object]:
    path = _get_session_store_path()
    data = load_session_store(path)

    result = data.get(user_session_id)
    if result is None:
        error(f"Sandbox session not found: {user_session_id}")
    if not isinstance(result, dict):
        error(f"Invalid sandbox session record: {user_session_id}")

    if expected_value is not None and result.get(key) != expected_value:
        return result
    if key not in result:
        return result

    result.pop(key)
    data[user_session_id] = result
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result


def get_session_result(user_session_id: str) -> dict[str, object]:
    path = _get_session_store_path()
    data = load_session_store(path)

    result = data.get(user_session_id)
    if result is None:
        error(f"Sandbox session not found: {user_session_id}")
    if not isinstance(result, dict):
        error(f"Invalid sandbox session record: {user_session_id}")

    return result


def build_exec_url(endpoint: object) -> str:
    if not isinstance(endpoint, str) or not endpoint.strip():
        error("Sandbox session endpoint is missing")

    parts = urlsplit(endpoint.strip())
    path = parts.path.rstrip("/")
    exec_path = f"{path}{SANDBOX_EXEC_ROUTE}" if path else SANDBOX_EXEC_ROUTE
    return urlunsplit(
        (parts.scheme, parts.netloc, exec_path, parts.query, parts.fragment)
    )


def build_terminal_ws_url(endpoint: object, shell_id: str | None = None) -> str:
    if not isinstance(endpoint, str) or not endpoint.strip():
        error("Sandbox session endpoint is missing")

    parts = urlsplit(endpoint.strip())
    if parts.scheme in {"http", "https"}:
        scheme = "ws"
    else:
        scheme = parts.scheme

    path = parts.path.rstrip("/")
    ws_path = f"{path}{SANDBOX_TERMINAL_ROUTE}" if path else SANDBOX_TERMINAL_ROUTE
    query = parts.query
    if shell_id:
        separator = "&" if query else ""
        query = f"{query}{separator}session_id={shell_id}"

    return urlunsplit((scheme, parts.netloc, ws_path, query, parts.fragment))


def rename_exec_session_id(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload

    data = payload.get("data")
    if isinstance(data, dict) and "session_id" in data:
        data["shell_id"] = data.pop("session_id")

    return payload
