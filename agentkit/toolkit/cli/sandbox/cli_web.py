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

from agentkit.toolkit.cli.sandbox.session_create import (
    SANDBOX_TOOL_ID_ENV,
    ensure_sandbox_session_with_status,
)
from agentkit.toolkit.cli.sandbox.tool_resolve import SandboxToolType
from agentkit.toolkit.cli.sandbox.sandbox_client import (
    build_web_url,
    echo_json,
    error,
)


def _resolve_web_session(
    *,
    session_id: str,
    tool_id: Optional[str],
) -> tuple[dict[str, object], bool]:
    return ensure_sandbox_session_with_status(
        session_id=session_id,
        tool_id=tool_id,
        tool_type=SandboxToolType.CODE_ENV.value,
    )


def web_command(
    session_id: str = typer.Option(
        ...,
        "--session-id",
        "--sid",
        "-s",
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
        session, is_new = _resolve_web_session(
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
            "is_new": is_new,
        }
    )
