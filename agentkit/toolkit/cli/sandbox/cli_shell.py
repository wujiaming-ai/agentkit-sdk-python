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

"""Shell command for sandbox CLI."""

from __future__ import annotations

from typing import Optional

import requests
import typer

from agentkit.toolkit.cli.sandbox.session_create import (
    SANDBOX_TOOL_ID_ENV,
    ensure_sandbox_session,
)
from agentkit.toolkit.cli.sandbox.tool_resolve import SandboxToolType
from agentkit.toolkit.cli.sandbox.utils import (
    SANDBOX_EXEC_TIMEOUT_SECONDS,
    build_exec_url,
    echo_json,
    error,
    rename_exec_session_id,
)


def shell_command(
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
    command: str = typer.Option(
        ...,
        "--command",
        help="Command to execute.",
    ),
    exec_dir: Optional[str] = typer.Option(
        None,
        "--exec-dir",
        help="Execution directory.",
    ),
    shell_id: Optional[str] = typer.Option(
        None,
        "--shell-id",
        help="Shell terminal ID for re-entering an existing shell.",
    ),
) -> None:
    """Execute a command in a sandbox shell."""
    try:
        session = ensure_sandbox_session(
            session_id=session_id,
            tool_id=tool_id,
            tool_type=tool_type.value,
        )
    except typer.Exit:
        raise
    except Exception as exc:
        error(str(exc))

    url = build_exec_url(session.get("endpoint"))
    body = {
        "id": shell_id or "",
        "exec_dir": exec_dir or "",
        "command": command,
    }

    try:
        response = requests.post(
            url,
            json=body,
            timeout=SANDBOX_EXEC_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        error(str(exc))

    try:
        payload = response.json()
    except ValueError:
        error(f"Invalid sandbox exec response: {response.text}")

    echo_json(rename_exec_session_id(payload))
