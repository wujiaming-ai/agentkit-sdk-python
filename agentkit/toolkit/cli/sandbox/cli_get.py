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

"""Get command for sandbox CLI."""

from __future__ import annotations

from typing import Optional

import typer

from agentkit.sdk.tools.client import AgentkitToolsClient
from agentkit.sdk.tools import types as tools_types
from agentkit.toolkit.cli.sandbox.session_create import SANDBOX_TOOL_ID_ENV
from agentkit.toolkit.cli.sandbox.tool_resolve import (
    SandboxToolType,
    resolve_existing_sandbox_tool_id,
)
from agentkit.toolkit.cli.sandbox.utils import (
    echo_json,
    error,
    find_session_result,
    get_session_result,
    replace_tool_session_results,
)

LIST_SESSIONS_PAGE_SIZE = 100


def _session_info_to_result(
    session: tools_types.SessionInfosForListSessions,
    tool_id: str,
) -> dict[str, object] | None:
    session_id = session.user_session_id
    if not isinstance(session_id, str) or not session_id.strip():
        return None

    return {
        "session_id": session_id.strip(),
        "tool_id": tool_id,
        "instance_id": session.session_id,
        "endpoint": session.endpoint,
    }


def _list_all_session_results(
    client: AgentkitToolsClient,
    tool_id: str,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    next_token: Optional[str] = None
    seen_tokens: set[str] = set()

    while True:
        response = client.list_sessions(
            tools_types.ListSessionsRequest(
                tool_id=tool_id,
                max_results=LIST_SESSIONS_PAGE_SIZE,
                next_token=next_token,
            )
        )
        for session in response.session_infos or []:
            result = _session_info_to_result(session, tool_id)
            if result:
                results.append(result)

        next_token = response.next_token or None
        if not next_token or next_token in seen_tokens:
            break
        seen_tokens.add(next_token)

    return results


def _sync_remote_sessions(
    *,
    session_id: str,
    tool_id: Optional[str],
    tool_type: SandboxToolType,
) -> str | None:
    existing = find_session_result(session_id)
    client = AgentkitToolsClient()
    resolved_tool_id = resolve_existing_sandbox_tool_id(
        tool_id=tool_id,
        tool_type=tool_type,
        default_tool_id=existing.get("tool_id") if existing else None,
        client=client,
        env_var_name=SANDBOX_TOOL_ID_ENV,
    )
    if not resolved_tool_id:
        return None

    results = _list_all_session_results(client, resolved_tool_id)
    replace_tool_session_results(resolved_tool_id, results)
    return resolved_tool_id


def get_command(
    session_id: str = typer.Option(
        ...,
        "--session-id",
        help="Sandbox session ID to look up.",
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
) -> None:
    """Get a sandbox session after syncing remote sessions for the current tool."""
    try:
        resolved_tool_id = _sync_remote_sessions(
            session_id=session_id,
            tool_id=tool_id,
            tool_type=tool_type,
        )
        result = get_session_result(session_id)
    except typer.Exit:
        raise
    except Exception as exc:
        error(str(exc))

    if resolved_tool_id and result.get("tool_id") != resolved_tool_id:
        error(f"Sandbox session not found: {session_id}")

    echo_json(result)
