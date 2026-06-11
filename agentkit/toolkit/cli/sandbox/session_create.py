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

"""Session creation helpers for sandbox CLI."""

from __future__ import annotations

import os
import uuid
from typing import Optional

from agentkit.sdk.tools.client import AgentkitToolsClient
from agentkit.sdk.tools import types as tools_types
from agentkit.toolkit.cli.sandbox.tool_resolve import (
    DEFAULT_SANDBOX_TOOL_TYPE,
    resolve_sandbox_tool_id,
)
from agentkit.toolkit.cli.sandbox.utils import (
    error,
    find_session_result,
    save_session_result,
)

DEFAULT_SANDBOX_TTL = 28800
SANDBOX_TOOL_ID_ENV = "AGENTKIT_SANDBOX_TOOL_ID"
SANDBOX_TTL_ENV = "AGENTKIT_SANDBOX_TTL"
MODEL_NAME_ENV_KEYS = ("OPENCODE_MODEL", "CODEX_MODEL", "ANTHROPIC_MODEL")
MODEL_API_KEY_ENV_KEYS = (
    "OPENCODE_API_KEY",
    "CODEX_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
)


def _append_envs(
    envs: list[tools_types.EnvsItemForCreateSession],
    keys: tuple[str, ...],
    value: Optional[str],
) -> None:
    resolved = (value or "").strip()
    if not resolved:
        return

    envs.extend(
        tools_types.EnvsItemForCreateSession(key=key, value=resolved)
        for key in keys
    )


def build_model_envs(
    *,
    model_name: Optional[str] = None,
    model_api_key: Optional[str] = None,
) -> list[tools_types.EnvsItemForCreateSession] | None:
    envs: list[tools_types.EnvsItemForCreateSession] = []
    _append_envs(envs, MODEL_NAME_ENV_KEYS, model_name)
    _append_envs(envs, MODEL_API_KEY_ENV_KEYS, model_api_key)
    return envs or None


def _resolve_ttl(ttl: Optional[int]) -> int:
    if ttl is not None:
        return ttl

    raw = (os.getenv(SANDBOX_TTL_ENV) or "").strip()
    if not raw:
        return DEFAULT_SANDBOX_TTL

    try:
        return int(raw)
    except ValueError:
        error(f"{SANDBOX_TTL_ENV} must be an integer")


def _is_session_missing_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "not found",
            "not exist",
            "notfound",
            "not_found",
            "不存在",
        )
    )


def _build_result(
    *,
    session_id: str,
    tool_id: str,
    instance_id: object,
    endpoint: object,
) -> dict[str, object]:
    return {
        "session_id": session_id,
        "tool_id": tool_id,
        "instance_id": instance_id,
        "endpoint": endpoint,
    }


def _build_create_result(
    response: tools_types.CreateSessionResponse,
    session_id: str,
    tool_id: str,
) -> dict[str, object]:
    return _build_result(
        session_id=response.user_session_id or session_id,
        tool_id=tool_id,
        instance_id=response.session_id,
        endpoint=response.endpoint,
    )


def _build_get_result(
    response: tools_types.GetSessionResponse,
    existing: dict[str, object],
    session_id: str,
    tool_id: str,
) -> dict[str, object]:
    return _build_result(
        session_id=response.user_session_id or session_id,
        tool_id=tool_id,
        instance_id=response.session_id or existing.get("instance_id"),
        endpoint=response.endpoint or existing.get("endpoint"),
    )


def _get_existing_remote_session(
    client: AgentkitToolsClient,
    existing: dict[str, object],
    session_id: str,
    tool_id: str,
) -> dict[str, object] | None:
    instance_id = existing.get("instance_id")
    if not isinstance(instance_id, str) or not instance_id:
        return None

    try:
        response = client.get_session(
            tools_types.GetSessionRequest(
                tool_id=tool_id,
                session_id=instance_id,
            )
        )
    except Exception as exc:
        if _is_session_missing_error(exc):
            return None
        raise

    return _build_get_result(response, existing, session_id, tool_id)


def _create_session(
    client: AgentkitToolsClient,
    session_id: str,
    tool_id: str,
    ttl: int,
    envs: Optional[list[tools_types.EnvsItemForCreateSession]] = None,
) -> dict[str, object]:
    request = tools_types.CreateSessionRequest(
        tool_id=tool_id,
        ttl=ttl,
        ttl_unit="second",
        user_session_id=session_id,
        envs=envs,
    )
    response = client.create_session(request)
    return _build_create_result(response, session_id, tool_id)


def ensure_sandbox_session(
    session_id: Optional[str] = None,
    tool_id: Optional[str] = None,
    tool_type: str = DEFAULT_SANDBOX_TOOL_TYPE,
    ttl: Optional[int] = None,
    envs: Optional[list[tools_types.EnvsItemForCreateSession]] = None,
) -> dict[str, object]:
    resolved_session_id = session_id or str(uuid.uuid4())
    existing = find_session_result(resolved_session_id) if session_id else None
    client = AgentkitToolsClient()
    resolved_tool_id = resolve_sandbox_tool_id(
        tool_id=tool_id,
        tool_type=tool_type,
        default_tool_id=existing.get("tool_id") if existing else None,
        client=client,
        env_var_name=SANDBOX_TOOL_ID_ENV,
    )

    if existing:
        result = _get_existing_remote_session(
            client,
            existing,
            resolved_session_id,
            resolved_tool_id,
        )
        if result:
            save_session_result(result)
            return result

    result = _create_session(
        client,
        resolved_session_id,
        resolved_tool_id,
        _resolve_ttl(ttl),
        envs=envs,
    )
    save_session_result(result)
    return result
