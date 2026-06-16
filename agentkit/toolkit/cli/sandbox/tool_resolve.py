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

"""Tool resolution helpers for sandbox CLI commands."""

from __future__ import annotations

import json
import os
from enum import Enum
from pathlib import Path
from typing import Optional

from agentkit.sdk.tools.client import AgentkitToolsClient
from agentkit.sdk.tools import types as tools_types
from agentkit.toolkit.cli.sandbox.utils import error

SANDBOX_TOOL_STORE_PATH = Path(".agentkit") / "sandbox" / "tools.json"
DEFAULT_SANDBOX_TOOL_TYPE = "CodeEnv"
VALID_SANDBOX_TOOL_TYPES = ("CodeEnv", "SkillEnv")
READY_TOOL_STATUS = "Ready"


class SandboxToolType(str, Enum):
    CODE_ENV = "CodeEnv"
    SKILL_ENV = "SkillEnv"


def normalize_tool_type(tool_type: str | SandboxToolType | None) -> str:
    value = tool_type.value if isinstance(tool_type, SandboxToolType) else tool_type
    resolved = (value or DEFAULT_SANDBOX_TOOL_TYPE).strip()
    if resolved not in VALID_SANDBOX_TOOL_TYPES:
        error(
            "--tool-type must be one of: "
            + ", ".join(VALID_SANDBOX_TOOL_TYPES)
        )
    return resolved


def _get_tool_store_path() -> Path:
    return Path.cwd() / SANDBOX_TOOL_STORE_PATH


def _load_tool_store(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        error(f"Invalid sandbox tool store {path}: {exc}")

    if not isinstance(data, dict):
        error(f"Invalid sandbox tool store {path}: expected JSON object")

    return data


def _build_tool_record(tool: object, tool_type: str) -> dict[str, object] | None:
    tool_id = getattr(tool, "tool_id", None)
    if not isinstance(tool_id, str) or not tool_id.strip():
        return None

    return {
        "ToolId": tool_id.strip(),
        "ToolType": getattr(tool, "tool_type", None) or tool_type,
        "Name": getattr(tool, "name", None),
        "Status": getattr(tool, "status", None),
    }


def _get_string_value(result: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _is_ready_tool_record(result: dict[str, object]) -> bool:
    return _get_string_value(result, "Status", "status") == READY_TOOL_STATUS


def _normalize_tool_record(
    tool_type: str,
    result: dict[str, object],
) -> dict[str, object]:
    resolved_tool_type = normalize_tool_type(
        _get_string_value(result, "ToolType", "tool_type") or tool_type
    )
    tool_id = _get_string_value(result, "ToolId", "tool_id")
    if not tool_id:
        error("Tool result missing ToolId")

    return {
        "ToolId": tool_id,
        "Name": _get_string_value(result, "Name", "name") or "",
        "Status": _get_string_value(result, "Status", "status") or "",
        "ToolType": resolved_tool_type,
    }


def save_tool_result(tool_type: str, result: dict[str, object]) -> None:
    stored = _normalize_tool_record(tool_type, result)
    resolved_tool_type = stored["ToolType"]
    path = _get_tool_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = _load_tool_store(path)
    data[resolved_tool_type] = stored
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def find_tool_result(tool_type: str) -> dict[str, object] | None:
    resolved_tool_type = normalize_tool_type(tool_type)
    data = _load_tool_store(_get_tool_store_path())

    result = data.get(resolved_tool_type)
    if result is None:
        return None
    if not isinstance(result, dict):
        error(f"Invalid sandbox tool record: {resolved_tool_type}")

    return result


def _get_cached_tool_id(tool_type: str) -> str | None:
    result = find_tool_result(tool_type)
    if not result:
        return None

    tool_id = _get_string_value(result, "ToolId", "tool_id")
    if tool_id and _is_ready_tool_record(result):
        return tool_id
    return None


def _list_first_tool(
    client: AgentkitToolsClient,
    tool_type: str,
) -> str | None:
    request = tools_types.ListToolsRequest(
        filters=[
            tools_types.FiltersItemForListTools(
                name="ToolType",
                values=[tool_type],
            )
        ]
    )
    response = client.list_tools(request)
    for tool in response.tools or []:
        record = _build_tool_record(tool, tool_type)
        if not record or not _is_ready_tool_record(record):
            continue
        save_tool_result(tool_type, record)
        tool_id = record["ToolId"]
        if isinstance(tool_id, str):
            return tool_id
    return None


def _create_tool(tool_type: str) -> str:
    from agentkit.toolkit.cli.sandbox.cli_create import create_tool

    result = create_tool(tool_type=tool_type)
    save_tool_result(tool_type, result)
    tool_id = _get_string_value(result, "ToolId", "tool_id")
    if not tool_id:
        error("CreateTool response missing ToolId")
    return tool_id


def resolve_sandbox_tool_id(
    *,
    tool_id: Optional[str],
    tool_type: str | SandboxToolType | None,
    default_tool_id: object = None,
    client: AgentkitToolsClient,
    env_var_name: str,
) -> str:
    resolved_tool_id = resolve_existing_sandbox_tool_id(
        tool_id=tool_id,
        tool_type=tool_type,
        default_tool_id=default_tool_id,
        client=client,
        env_var_name=env_var_name,
    )
    if resolved_tool_id:
        return resolved_tool_id

    resolved_tool_type = normalize_tool_type(tool_type)
    return _create_tool(resolved_tool_type)


def resolve_existing_sandbox_tool_id(
    *,
    tool_id: Optional[str],
    tool_type: str | SandboxToolType | None,
    default_tool_id: object = None,
    client: AgentkitToolsClient,
    env_var_name: str,
) -> str | None:
    explicit_tool_id = (tool_id or "").strip()
    if explicit_tool_id:
        return explicit_tool_id

    env_tool_id = (os.getenv(env_var_name) or "").strip()
    if env_tool_id:
        return env_tool_id

    if isinstance(default_tool_id, str) and default_tool_id.strip():
        return default_tool_id.strip()

    resolved_tool_type = normalize_tool_type(tool_type)
    cached_tool_id = _get_cached_tool_id(resolved_tool_type)
    if cached_tool_id:
        return cached_tool_id

    listed_tool_id = _list_first_tool(client, resolved_tool_type)
    if listed_tool_id:
        return listed_tool_id

    return None
