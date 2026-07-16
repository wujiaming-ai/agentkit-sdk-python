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

"""Mount command for sandbox CLI."""

from __future__ import annotations

import re
import subprocess
from typing import Optional

import typer

from agentkit.auth.errors import AuthError
from agentkit.auth.profile import address_to_profile_name, load_profile
from agentkit.sdk.tools import types as tools_types
from agentkit.toolkit.cli.sandbox.agentkit_client import AgentkitToolsClient
from agentkit.toolkit.cli.sandbox.config_store import (
    SandboxConfigError,
    config_default_if_unprovided,
    config_default_str,
    config_tool_identifier_defaults_if_unprovided,
    configured_sandbox_config,
)
from agentkit.toolkit.cli.sandbox.session_create import SANDBOX_TOOL_ID_ENV
from agentkit.toolkit.cli.sandbox.sandbox_client import (
    echo_json,
    error,
)
from agentkit.toolkit.cli.sandbox.tool_resolve import (
    SandboxToolType,
    resolve_existing_sandbox_tool_id,
)

TOS_BROWSER_DOWNLOAD_URL = (
    "https://lf3-cdn-tos.bytegoofy.com/obj/tron-demo/7605960479860594954/"
    "releases/328661904/1.12.7/darwin-arm64/"
    "TOS_Browser_Public-v1.12.7-darwin-arm64.dmg"
)
TOS_BROWSER_INSTALL_HINT = "请安装 TosBrowser 应用"
USER_POOL_PATTERN = re.compile(r"userpool-([^.]+)\.userpool")


class TosBrowserNotFoundError(Exception):
    """Raised when the OS has no handler for the tosbrowser URL scheme."""


def _resolve_required(value: str, option_name: str) -> str:
    resolved = (value or "").strip()
    if not resolved:
        error(f"{option_name} must not be empty")
    return resolved


def _field_value(source: object, *keys: str) -> object:
    for key in keys:
        if isinstance(source, dict):
            value = source.get(key)
        else:
            value = getattr(source, key, None)
        if value is not None:
            return value
    return None


def _tool_payload(response: object) -> object:
    if isinstance(response, dict) and isinstance(response.get("Result"), dict):
        return response["Result"]
    return response


def _extract_tos_bucket_from_tool(tool: object) -> str | None:
    payload = _tool_payload(tool)
    tos_mount_config = _field_value(
        payload,
        "TosMountConfig",
        "tos_mount_config",
    )
    if not tos_mount_config:
        return None

    mount_points = _field_value(tos_mount_config, "MountPoints", "mount_points")
    if not isinstance(mount_points, list):
        return None

    for mount_point in mount_points:
        bucket_name = _field_value(mount_point, "BucketName", "bucket_name")
        if isinstance(bucket_name, str) and bucket_name.strip():
            return bucket_name.strip()
    return None


def _resolve_tos_bucket(*, tool_id: str) -> str:
    try:
        tool = AgentkitToolsClient().get_tool(
            tools_types.GetToolRequest(tool_id=tool_id)
        )
    except Exception as exc:
        error(f"Failed to get sandbox tool {tool_id}: {exc}")

    resolved_tos_bucket = _extract_tos_bucket_from_tool(tool)
    if not resolved_tos_bucket:
        error(f"当前工具未挂载 Tos: {tool_id}")
    return resolved_tos_bucket


def _resolve_mount_tool_id(
    *,
    tool_id: Optional[str],
    tool_name: Optional[str],
    tool_type: str,
) -> str:
    resolved_tool_id = resolve_existing_sandbox_tool_id(
        tool_id=tool_id,
        tool_name=tool_name,
        tool_type=tool_type,
        client=AgentkitToolsClient(),
        env_var_name=SANDBOX_TOOL_ID_ENV,
    )
    if not resolved_tool_id:
        error("Sandbox tool ID is required")
    return resolved_tool_id


def _load_profile_discovery(oauth_url: Optional[str]) -> dict[str, object]:
    profile_name = None
    if oauth_url is not None:
        profile_name = address_to_profile_name(
            _resolve_required(oauth_url, "--oauth-url")
        )
    try:
        return load_profile(profile_name).to_dict()
    except AuthError as exc:
        error(str(exc).split("\n")[0])


def _required_discovery_field(
    discovery: dict[str, object],
    field_name: str,
) -> str:
    value = discovery.get(field_name)
    if not isinstance(value, str) or not value.strip():
        error(f"Sandbox mount discovery missing {field_name}")
    return value.strip()


def _extract_user_pool_id(issuer: str) -> str:
    match = USER_POOL_PATTERN.search(issuer)
    if not match:
        error("Sandbox mount discovery issuer missing user pool ID")
    return match.group(1)


def _build_tosbrowser_command(
    *,
    tos_bucket: str,
    tool_id: str,
    session_id: str,
    role_trn: str,
    user_pool_id: str,
    client_id: str,
) -> str:
    return (
        "tosbrowser://open?"
        f"path=tos://{tos_bucket}/sandbox-session/tool-{tool_id}/"
        f"session-{session_id}/"
        f"&type=oAuthLogin"
        f"&role={role_trn}"
        f"&userPool={user_pool_id}"
        f"&clientId={client_id}"
    )


def _called_process_error_output(exc: subprocess.CalledProcessError) -> str:
    parts = [
        str(item).strip()
        for item in (exc.stderr, exc.stdout)
        if isinstance(item, str) and item.strip()
    ]
    if parts:
        return "\n".join(parts)
    return str(exc)


def _is_tosbrowser_not_found_error(message: str) -> bool:
    return (
        "No application knows how to open URL" in message
        or "kLSApplicationNotFoundErr" in message
        or "Code=-10814" in message
    )


def _open_tosbrowser(command: str) -> None:
    try:
        subprocess.run(
            ["open", command],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        output = _called_process_error_output(exc)
        if _is_tosbrowser_not_found_error(output):
            raise TosBrowserNotFoundError(output) from exc
        raise


def mount_command(
    ctx: typer.Context,
    session_id: Optional[str] = typer.Option(
        None,
        "--session-id",
        "--sid",
        "-s",
        help="Sandbox session ID to mount.",
    ),
    oauth_url: Optional[str] = typer.Option(
        None,
        "--oauth-url",
        help=(
            "OAuth profile URL. Defaults to ~/.agentkit/auth/active_profile. "
            "When provided, reads the matching local profile without network discovery."
        ),
    ),
    tool_id: Optional[str] = typer.Option(
        None,
        "--tool-id",
        help="Sandbox tool ID. Defaults to sandbox config, env, or cached tool.",
    ),
    tool_name: Optional[str] = typer.Option(
        None,
        "--tool-name",
        help="Sandbox tool name. Used only when --tool-id is omitted.",
    ),
) -> None:
    """Open the sandbox session TOS path in TOS Browser."""
    try:
        config_defaults = configured_sandbox_config()
        session_id = config_default_if_unprovided(
            ctx, "session_id", "session-id", session_id, data=config_defaults
        )
        if not session_id:
            error("--session-id is required")
        resolved_session_id = _resolve_required(session_id, "--session-id")
        tool_id, tool_name = config_tool_identifier_defaults_if_unprovided(
            ctx, tool_id=tool_id, tool_name=tool_name, data=config_defaults
        )
        tool_type = (
            config_default_str("tool-type", data=config_defaults)
            or SandboxToolType.CODE_ENV.value
        )
        resolved_tool_id = _resolve_mount_tool_id(
            tool_id=tool_id,
            tool_name=tool_name,
            tool_type=tool_type,
        )
        resolved_tos_bucket = _resolve_tos_bucket(tool_id=resolved_tool_id)
        discovery = _load_profile_discovery(oauth_url)
        issuer = _required_discovery_field(discovery, "issuer")
        role_trn = _required_discovery_field(discovery, "role_trn")
        client_id = _required_discovery_field(discovery, "client_id")
        user_pool_id = _extract_user_pool_id(issuer)
        command = _build_tosbrowser_command(
            tos_bucket=resolved_tos_bucket,
            tool_id=resolved_tool_id,
            session_id=resolved_session_id,
            role_trn=role_trn,
            user_pool_id=user_pool_id,
            client_id=client_id,
        )
        _open_tosbrowser(command)
    except TosBrowserNotFoundError as exc:
        echo_json(
            {
                "tool_id": resolved_tool_id,
                "session_id": resolved_session_id,
                "command": command,
                "error_msg": "Failed to open TosBrowser",
                "original_error": str(exc),
                "install_hint": TOS_BROWSER_INSTALL_HINT,
                "download_url": TOS_BROWSER_DOWNLOAD_URL,
            }
        )
        raise typer.Exit(1)
    except typer.Exit:
        raise
    except SandboxConfigError as exc:
        error(str(exc))
    except Exception as exc:
        error(str(exc))

    echo_json(
        {
            "tool_id": resolved_tool_id,
            "session_id": resolved_session_id,
            "command": command,
        }
    )
