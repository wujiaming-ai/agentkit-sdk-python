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

from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
from typing import NoReturn
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import typer

try:
    import fcntl
except ImportError:  # pragma: no cover - fcntl is unavailable on Windows.
    fcntl = None  # type: ignore[assignment]

SANDBOX_SESSION_STORE_PATH = Path(".agentkit") / "sandbox" / "sessions.json"
SANDBOX_EXEC_ROUTE = "/v1/shell/exec"
SANDBOX_TERMINAL_ROUTE = "/v1/shell/ws"
SANDBOX_FILE_UPLOAD_ROUTE = "/v1/file/upload"
SANDBOX_FILE_DOWNLOAD_ROUTE = "/v1/file/download"
SANDBOX_FILE_LIST_ROUTE = "/v1/file/list"
SANDBOX_WEB_ROUTE = "/vnc/index.html"
SANDBOX_WEB_QUERY_PARAMS = (
    ("autoconnect", "true"),
    ("resize", "scale"),
    ("reconnect", "1"),
)
SANDBOX_WEB_PATH_QUERY_KEYS = ("faasInstanceName", "Authorization")
SANDBOX_EXEC_TIMEOUT_SECONDS = 300
TERMINAL_SHELL_ID_KEY = "terminal_shell_id"
_SESSION_STORE_THREAD_LOCK = threading.RLock()


def error(message: str) -> NoReturn:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(1)


def echo_json(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))


def _get_session_store_path() -> Path:
    return Path.cwd() / SANDBOX_SESSION_STORE_PATH


@contextmanager
def _locked_session_store(path: Path):
    with _SESSION_STORE_THREAD_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name(f"{path.name}.lock")
        lock_file = lock_path.open("a+", encoding="utf-8")
        try:
            if fcntl:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            if fcntl:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()


def _terminal_shell_ids_from_value(value: object) -> list[str]:
    if isinstance(value, str):
        resolved = value.strip()
        return [resolved] if resolved else []
    if not isinstance(value, list):
        return []

    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        resolved = item.strip()
        if not resolved or resolved in seen:
            continue
        result.append(resolved)
        seen.add(resolved)
    return result


def _normalize_session_record(record: dict[str, object]) -> dict[str, object]:
    result = dict(record)
    shell_ids = _terminal_shell_ids_from_value(result.get(TERMINAL_SHELL_ID_KEY))
    if shell_ids:
        result[TERMINAL_SHELL_ID_KEY] = shell_ids
    else:
        result.pop(TERMINAL_SHELL_ID_KEY, None)
    return result


def _normalize_session_store(data: dict[str, object]) -> dict[str, object]:
    return {
        key: _normalize_session_record(value) if isinstance(value, dict) else value
        for key, value in data.items()
    }


def _write_session_store(path: Path, data: dict[str, object]) -> None:
    normalized = _normalize_session_store(data)
    text = json.dumps(normalized, indent=2, ensure_ascii=False) + "\n"
    tmp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
    )
    try:
        tmp_path.write_text(text, encoding="utf-8")
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


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
    session_id = result.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        error("CreateSession response missing session_id")

    path = _get_session_store_path()
    with _locked_session_store(path):
        if path.exists():
            data = load_session_store(path)
        else:
            data = {}

        existing = data.get(session_id)
        if isinstance(existing, dict):
            data[session_id] = {**existing, **result}
        else:
            data[session_id] = result

        _write_session_store(path, data)


def replace_tool_session_results(
    tool_id: str,
    results: list[dict[str, object]],
) -> None:
    path = _get_session_store_path()
    with _locked_session_store(path):
        if path.exists():
            data = load_session_store(path)
        else:
            data = {}

        old_data = data
        data = {
            key: value
            for key, value in old_data.items()
            if not (isinstance(value, dict) and value.get("tool_id") == tool_id)
        }

        for result in results:
            session_id = result.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                continue
            existing = old_data.get(session_id)
            if isinstance(existing, dict):
                data[session_id] = {**existing, **result}
            else:
                data[session_id] = result

        _write_session_store(path, data)


def update_session_result(
    session_id: str,
    updates: dict[str, object],
) -> dict[str, object]:
    path = _get_session_store_path()
    with _locked_session_store(path):
        data = load_session_store(path)

        result = data.get(session_id)
        if result is None:
            error(f"Sandbox session not found: {session_id}")
        if not isinstance(result, dict):
            error(f"Invalid sandbox session record: {session_id}")

        result.update(updates)
        result = _normalize_session_record(result)
        data[session_id] = result
        _write_session_store(path, data)
        return result


def remove_session_result_key(
    session_id: str,
    key: str,
    expected_value: object | None = None,
) -> dict[str, object]:
    path = _get_session_store_path()
    with _locked_session_store(path):
        data = load_session_store(path)

        result = data.get(session_id)
        if result is None:
            error(f"Sandbox session not found: {session_id}")
        if not isinstance(result, dict):
            error(f"Invalid sandbox session record: {session_id}")

        if expected_value is not None and result.get(key) != expected_value:
            return result
        if key not in result:
            return result

        result.pop(key)
        result = _normalize_session_record(result)
        data[session_id] = result
        _write_session_store(path, data)
        return result


def add_session_terminal_shell_id(
    session_id: str,
    shell_id: str,
) -> dict[str, object]:
    resolved_shell_id = shell_id.strip()
    if not resolved_shell_id:
        error("Sandbox terminal shell_id is missing")

    path = _get_session_store_path()
    with _locked_session_store(path):
        data = load_session_store(path)
        result = data.get(session_id)
        if result is None:
            error(f"Sandbox session not found: {session_id}")
        if not isinstance(result, dict):
            error(f"Invalid sandbox session record: {session_id}")

        shell_ids = _terminal_shell_ids_from_value(
            result.get(TERMINAL_SHELL_ID_KEY)
        )
        if resolved_shell_id not in shell_ids:
            shell_ids.append(resolved_shell_id)
        result[TERMINAL_SHELL_ID_KEY] = shell_ids
        result = _normalize_session_record(result)
        data[session_id] = result
        _write_session_store(path, data)
        return result


def remove_session_terminal_shell_id(
    session_id: str,
    shell_id: str,
) -> dict[str, object]:
    resolved_shell_id = shell_id.strip()
    if not resolved_shell_id:
        error("Sandbox terminal shell_id is missing")

    path = _get_session_store_path()
    with _locked_session_store(path):
        data = load_session_store(path)
        result = data.get(session_id)
        if result is None:
            error(f"Sandbox session not found: {session_id}")
        if not isinstance(result, dict):
            error(f"Invalid sandbox session record: {session_id}")

        shell_ids = [
            item
            for item in _terminal_shell_ids_from_value(
                result.get(TERMINAL_SHELL_ID_KEY)
            )
            if item != resolved_shell_id
        ]
        if shell_ids:
            result[TERMINAL_SHELL_ID_KEY] = shell_ids
        else:
            result.pop(TERMINAL_SHELL_ID_KEY, None)
        result = _normalize_session_record(result)
        data[session_id] = result
        _write_session_store(path, data)
        return result


def get_session_result(session_id: str) -> dict[str, object]:
    path = _get_session_store_path()
    with _locked_session_store(path):
        data = load_session_store(path)

        result = data.get(session_id)
        if result is None:
            error(f"Sandbox session not found: {session_id}")
        if not isinstance(result, dict):
            error(f"Invalid sandbox session record: {session_id}")

        return _normalize_session_record(result)


def get_all_session_results() -> dict[str, object]:
    path = _get_session_store_path()
    with _locked_session_store(path):
        if not path.exists():
            return {}
        data = load_session_store(path)
        return _normalize_session_store(data)


def find_session_result(session_id: str) -> dict[str, object] | None:
    path = _get_session_store_path()
    if not path.exists():
        return None

    with _locked_session_store(path):
        if not path.exists():
            return None
        data = load_session_store(path)
        result = data.get(session_id)
        if result is None:
            return None
        if not isinstance(result, dict):
            error(f"Invalid sandbox session record: {session_id}")

        return _normalize_session_record(result)


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


def build_file_url(endpoint: object, route: str) -> str:
    if not isinstance(endpoint, str) or not endpoint.strip():
        error("Sandbox session endpoint is missing")
    if not route.startswith("/"):
        error("Sandbox file route must start with /")

    parts = urlsplit(endpoint.strip())
    path = parts.path.rstrip("/")
    file_path = f"{path}{route}" if path else route
    return urlunsplit(
        (parts.scheme, parts.netloc, file_path, parts.query, parts.fragment)
    )


def build_web_url(endpoint: object) -> str:
    if not isinstance(endpoint, str) or not endpoint.strip():
        error("Sandbox session endpoint is missing")

    parts = urlsplit(endpoint.strip())
    path = parts.path.rstrip("/")
    web_path = f"{path}{SANDBOX_WEB_ROUTE}" if path else SANDBOX_WEB_ROUTE
    original_query_items = parse_qsl(parts.query, keep_blank_values=True)
    web_query_keys = {key for key, _value in SANDBOX_WEB_QUERY_PARAMS}
    web_query_keys.add("path")
    query_items = [
        (key, value)
        for key, value in original_query_items
        if key not in web_query_keys
    ]
    websockify_items = [
        (expected_key, value)
        for expected_key in SANDBOX_WEB_PATH_QUERY_KEYS
        for key, value in original_query_items
        if key == expected_key
    ]
    path_items = []
    if websockify_items:
        path_items = [("path", f"websockify?{urlencode(websockify_items)}")]

    query = urlencode([*SANDBOX_WEB_QUERY_PARAMS, *query_items, *path_items])
    return urlunsplit(
        (parts.scheme, parts.netloc, web_path, query, parts.fragment)
    )


def rename_exec_session_id(payload: object) -> object:
    if not isinstance(payload, dict):
        return payload

    data = payload.get("data")
    if isinstance(data, dict) and "session_id" in data:
        data["shell_id"] = data.pop("session_id")

    return payload
