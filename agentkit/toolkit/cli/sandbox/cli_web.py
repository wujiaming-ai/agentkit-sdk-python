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

"""Web URL command for sandbox CLI."""

from __future__ import annotations

import webbrowser
from typing import Optional

import typer

from agentkit.sdk.tools.client import AgentkitToolsClient
from agentkit.toolkit.cli.sandbox.session_create import SANDBOX_TOOL_ID_ENV
from agentkit.toolkit.cli.sandbox.session_sync import sync_remote_sessions
from agentkit.toolkit.cli.sandbox.tool_resolve import SandboxToolType
from agentkit.toolkit.cli.sandbox.utils import (
    build_web_url,
    echo_json,
    error,
    find_session_result,
)


def _resolve_existing_session(
    *,
    session_id: str,
    tool_id: Optional[str],
) -> dict[str, object]:
    client = AgentkitToolsClient()
    resolved_tool_id = sync_remote_sessions(
        session_id=session_id,
        tool_id=tool_id,
        tool_type=SandboxToolType.CODE_ENV,
        client=client,
        env_var_name=SANDBOX_TOOL_ID_ENV,
    )
    result = find_session_result(session_id)
    if result is None:
        error(f"Sandbox session not found: {session_id}")
    if resolved_tool_id and result.get("tool_id") != resolved_tool_id:
        error(f"Sandbox session not found: {session_id}")
    return result


def web_command(
    session_id: str = typer.Option(
        ...,
        "--session-id",
        help="Sandbox session ID to open in a browser.",
    ),
    tool_id: Optional[str] = typer.Option(
        None,
        "--tool-id",
        "--tool_id",
        help=f"Sandbox tool ID. Defaults to {SANDBOX_TOOL_ID_ENV}.",
    ),
) -> None:
    """Return the browser URL for a sandbox session."""
    try:
        session = _resolve_existing_session(
            session_id=session_id,
            tool_id=tool_id,
        )
        url = build_web_url(session.get("endpoint"))
        if not webbrowser.open(url):
            error("Failed to open browser")
    except typer.Exit:
        raise
    except Exception as exc:
        error(str(exc))

    resolved_session_id = session.get("session_id")
    if not isinstance(resolved_session_id, str) or not resolved_session_id:
        resolved_session_id = session_id

    echo_json(
        {
            "url": url,
            "tool_id": session.get("tool_id"),
            "session_id": resolved_session_id,
        }
    )
