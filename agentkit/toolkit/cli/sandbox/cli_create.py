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

"""Create Tool command for sandbox CLI."""

from __future__ import annotations

import os
import re
import time
from typing import Optional

import typer

from agentkit.sdk.tools.client import AgentkitToolsClient
from agentkit.sdk.tools import types as tools_types
from agentkit.toolkit.cli.sandbox.model_config import (
    ANTHROPIC_BASE_URL_ENV_KEYS,
    CODE_ENV_CODEX_HOME,
    CODE_ENV_HOME,
    CODEX_CONFIG_TOML_ENV,
    CODEX_MODEL_CATALOG_JSON_ENV,
    DEFAULT_ANTHROPIC_BASE_URL,
    DEFAULT_MODEL_BASE_URL,
    DEFAULT_MODEL_NAME,
    MODEL_API_KEY_ENV,
    MODEL_API_KEY_ENV_KEYS,
    MODEL_BASE_URL_ENV_KEYS,
    MODEL_NAME_ENV_KEYS,
    build_codex_config_toml as _shared_build_codex_config_toml,
    build_codex_model_catalog_json as _shared_build_codex_model_catalog_json,
)
from agentkit.toolkit.cli.sandbox.tool_resolve import save_tool_result
from agentkit.toolkit.cli.sandbox.utils import error
from agentkit.toolkit.volcengine.services.tos_service import (
    TOSMountConfig,
    TOSService,
    TOSServiceConfig,
)
from agentkit.utils.misc import generate_apikey_name, generate_random_id

DEFAULT_CREATE_TOOL_REGION = "cn-beijing"
SANDBOX_REGION_ENV = "AGENTKIT_SANDBOX_REGION"
SANDBOX_TOS_REGION_ENV = "AGENTKIT_SANDBOX_TOS_REGION"
DEFAULT_CREATE_TOOL_TYPE = "CodeEnv"
DEFAULT_CPU = 4
VALID_CPU_VALUES = (2, 4, 8, 16)
MEMORY_MB_PER_CPU = 2048
DEFAULT_TOS_BUCKET_PATH = "/sandbox-session/default/default"
DEFAULT_TOS_LOCAL_PATH = "/home/gem"
DISABLED_SERVICE_ENV_KEYS = (
    "DISABLE_JUPYTER",
    "DISABLE_CODE_SERVER",
    "DISABLE_NODEJS_REPL",
)
TOOL_READY_STATUS = "Ready"
TOOL_FAILED_STATUSES = {"Error", "Failed", "CreateFailed", "Deleting", "Deleted"}
TOOL_WAIT_INTERVAL_SECONDS = 5
TOOL_WAIT_TIMEOUT_SECONDS = 600


def _resolve_region(env_var_name: str) -> str:
    env_region = (os.getenv(env_var_name) or "").strip()
    if env_region:
        return env_region
    return DEFAULT_CREATE_TOOL_REGION


def _generate_tool_name(tool_type: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", tool_type.lower()).strip("-")
    if not normalized:
        normalized = "tool"
    return f"agentkit-{normalized}-{generate_random_id(8)}"


def _resolve_tos_bucket(tos_bucket: Optional[str]) -> str:
    resolved_bucket = (tos_bucket or "").strip()
    if not resolved_bucket:
        error("--tos-bucket must not be empty")
    return resolved_bucket


def _validate_cpu(value: int) -> int:
    if value not in VALID_CPU_VALUES:
        allowed = ", ".join(str(item) for item in VALID_CPU_VALUES)
        raise typer.BadParameter(f"--cpu must be one of: {allowed}")
    return value


def _cpu_to_resource_shape(cpu: int) -> tuple[int, int]:
    resolved_cpu = _validate_cpu(cpu)
    return resolved_cpu * 1000, resolved_cpu * MEMORY_MB_PER_CPU


def _append_tool_envs(
    envs: list[tools_types.EnvsItemForCreateTool],
    keys: tuple[str, ...],
    value: Optional[str],
) -> None:
    resolved = (value or "").strip()
    if not resolved:
        return

    envs.extend(
        tools_types.EnvsItemForCreateTool(Key=key, Value=resolved)
        for key in keys
    )


def _build_codex_config_toml(model_name: str) -> str:
    return _shared_build_codex_config_toml(model_name)


def _build_codex_model_catalog_json(model_name: str) -> str:
    return _shared_build_codex_model_catalog_json(model_name)


def _append_code_env_tool_envs(
    envs: list[tools_types.EnvsItemForCreateTool],
    model_name: str,
) -> None:
    envs.extend(
        [
            tools_types.EnvsItemForCreateTool(
                Key="OPENCODE_DISABLE_AUTOUPDATE",
                Value="1",
            ),
            tools_types.EnvsItemForCreateTool(
                Key="HOME",
                Value=CODE_ENV_HOME,
            ),
            tools_types.EnvsItemForCreateTool(
                Key="CODEX_HOME",
                Value=CODE_ENV_CODEX_HOME,
            ),
            tools_types.EnvsItemForCreateTool(
                Key=CODEX_CONFIG_TOML_ENV,
                Value=_build_codex_config_toml(model_name),
            ),
            tools_types.EnvsItemForCreateTool(
                Key=CODEX_MODEL_CATALOG_JSON_ENV,
                Value=_build_codex_model_catalog_json(model_name),
            ),
        ]
    )


def _build_tool_model_envs(
    *,
    tool_type: str,
    model_name: Optional[str] = None,
    model_api_key: Optional[str] = None,
) -> list[tools_types.EnvsItemForCreateTool] | None:
    envs: list[tools_types.EnvsItemForCreateTool] = []
    resolved_model_name = (model_name or "").strip() or DEFAULT_MODEL_NAME
    resolved_model_api_key = model_api_key or os.getenv(MODEL_API_KEY_ENV)
    _append_tool_envs(envs, MODEL_NAME_ENV_KEYS, resolved_model_name)
    _append_tool_envs(envs, MODEL_API_KEY_ENV_KEYS, resolved_model_api_key)
    _append_tool_envs(
        envs,
        MODEL_BASE_URL_ENV_KEYS,
        DEFAULT_MODEL_BASE_URL,
    )
    _append_tool_envs(
        envs,
        ANTHROPIC_BASE_URL_ENV_KEYS,
        DEFAULT_ANTHROPIC_BASE_URL,
    )
    _append_tool_envs(envs, DISABLED_SERVICE_ENV_KEYS, "true")
    if tool_type.strip() == DEFAULT_CREATE_TOOL_TYPE:
        _append_code_env_tool_envs(envs, resolved_model_name)
    return envs or None


def _build_create_tool_request(
    *,
    tool_type: str,
    name: Optional[str],
    tos_bucket: Optional[str],
    tos_region: str,
    cpu: int = DEFAULT_CPU,
    model_name: Optional[str] = None,
    model_api_key: Optional[str] = None,
) -> tools_types.CreateToolRequest:
    resolved_tool_type = tool_type.strip() or DEFAULT_CREATE_TOOL_TYPE
    resolved_name = (name or "").strip() or _generate_tool_name(resolved_tool_type)
    tos_mount_config = _build_tos_mount_config(tos_bucket, tos_region)
    cpu_milli, memory_mb = _cpu_to_resource_shape(cpu)

    return tools_types.CreateToolRequest(
        Name=resolved_name,
        ToolType=resolved_tool_type,
        CpuMilli=cpu_milli,
        MemoryMb=memory_mb,
        AuthorizerConfiguration=tools_types.AuthorizerForCreateTool(
            KeyAuth=tools_types.AuthorizerKeyAuthForCreateTool(
                ApiKeyName=generate_apikey_name(),
                ApiKeyLocation="Header",
            )
        ),
        NetworkConfiguration=tools_types.NetworkForCreateTool(
            EnablePublicNetwork=True,
            EnablePrivateNetwork=False,
        ),
        TosMountConfig=tos_mount_config,
        Envs=_build_tool_model_envs(
            tool_type=resolved_tool_type,
            model_name=model_name,
            model_api_key=model_api_key,
        ),
    )


def _build_tos_mount_config(
    tos_bucket: Optional[str],
    region: str,
) -> tools_types.TosMountForCreateTool | None:
    if not (tos_bucket or "").strip():
        return None

    resolved_bucket = _resolve_tos_bucket(tos_bucket)
    service = TOSService(
        TOSServiceConfig(
            bucket=resolved_bucket,
            region=region,
        )
    )
    mount_config = service.build_mount_config(
        bucket_path=DEFAULT_TOS_BUCKET_PATH,
        local_mount_path=DEFAULT_TOS_LOCAL_PATH,
    )
    return _to_create_tool_tos_mount_config(mount_config)


def _to_create_tool_tos_mount_config(
    mount_config: TOSMountConfig,
) -> tools_types.TosMountForCreateTool:
    return tools_types.TosMountForCreateTool(
        EnableTos=True,
        Credentials=tools_types.TosMountCredentialsForCreateTool(
            AccessKeyId=mount_config.credentials.access_key_id,
            SecretAccessKey=mount_config.credentials.secret_access_key,
        ),
        MountPoints=[
            tools_types.TosMountMountPointsItemForCreateTool(
                BucketName=mount.bucket_name,
                BucketPath=mount.bucket_path,
                Endpoint=mount.endpoint,
                LocalMountPath=mount.local_mount_path,
                ReadOnly=mount.read_only,
            )
            for mount in mount_config.mount_points
        ],
    )


def _format_tool_failure(response: tools_types.GetToolResponse) -> str:
    mount_summary = ""
    tos_config = getattr(response, "tos_mount_config", None)
    if tos_config and tos_config.mount_points:
        mount = tos_config.mount_points[0]
        mount_summary = (
            "\nTOS: "
            f"BucketName={mount.bucket_name or '-'}, "
            f"BucketPath={mount.bucket_path or '-'}, "
            f"LocalMountPath={mount.local_mount_path or '-'}"
        )
    return (
        f"Tool {getattr(response, 'tool_id', None) or '<unknown>'} "
        f"entered terminal status: "
        f"{getattr(response, 'status', None) or 'Unknown'}\n"
        "GetTool did not return a detailed failure reason. "
        "Summary:\n"
        f"Name: {getattr(response, 'name', None) or '-'}\n"
        f"ToolType: {getattr(response, 'tool_type', None) or '-'}\n"
        f"ImageUrl: {getattr(response, 'image_url', None) or '-'}\n"
        f"Command: {getattr(response, 'command', None) or '-'}\n"
        f"Port: {getattr(response, 'port', None) or '-'}"
        f"{mount_summary}"
    )


def _wait_for_tool_ready(
    client: AgentkitToolsClient,
    tool_id: str,
    *,
    timeout_seconds: int = TOOL_WAIT_TIMEOUT_SECONDS,
    interval_seconds: int = TOOL_WAIT_INTERVAL_SECONDS,
) -> tools_types.GetToolResponse:
    deadline = time.monotonic() + timeout_seconds
    last_status = None

    while True:
        response = client.get_tool(tools_types.GetToolRequest(ToolId=tool_id))
        status = response.status or ""
        if status != last_status:
            typer.echo(f"工具状态：{status or 'Unknown'}")
            last_status = status

        if status == TOOL_READY_STATUS:
            return response
        if status in TOOL_FAILED_STATUSES:
            raise RuntimeError(_format_tool_failure(response))
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for Tool {tool_id} to become Ready. "
                f"Last status: {status or 'Unknown'}"
            )

        time.sleep(interval_seconds)


def create_tool(
    *,
    tool_type: str = DEFAULT_CREATE_TOOL_TYPE,
    tool_name: Optional[str] = None,
    tos_bucket: Optional[str] = None,
    cpu: int = DEFAULT_CPU,
    model_name: Optional[str] = None,
    model_api_key: Optional[str] = None,
) -> dict[str, object]:
    region = _resolve_region(SANDBOX_REGION_ENV)
    tos_region = _resolve_region(SANDBOX_TOS_REGION_ENV)
    request = _build_create_tool_request(
        tool_type=tool_type,
        name=tool_name,
        tos_bucket=tos_bucket,
        tos_region=tos_region,
        cpu=cpu,
        model_name=model_name,
        model_api_key=model_api_key,
    )
    client = AgentkitToolsClient(
        region=region,
    )
    response = client.create_tool(request)
    tool_id = response.tool_id
    if not tool_id:
        raise RuntimeError("CreateTool response missing ToolId")
    final_tool = _wait_for_tool_ready(client, tool_id)
    return {
        "tool_id": tool_id,
        "tool_type": final_tool.tool_type or request.tool_type,
        "name": final_tool.name or request.name,
        "status": final_tool.status or TOOL_READY_STATUS,
    }


def create_command(
    tool_type: str = typer.Option(
        DEFAULT_CREATE_TOOL_TYPE,
        "--tool-type",
        help="Tool type. Defaults to CodeEnv.",
    ),
    tool_name: Optional[str] = typer.Option(
        None,
        "--tool-name",
        help="Tool name. Defaults to an auto-generated name.",
    ),
    tos_bucket: Optional[str] = typer.Option(
        None,
        "--tos-bucket",
        help=(
            "TOS bucket to mount at /home/gem. "
            "Omit to create the tool without a TOS mount."
        ),
    ),
    cpu: int = typer.Option(
        DEFAULT_CPU,
        "--cpu",
        help="Sandbox vCPU count. Allowed values: 2, 4, 8, 16.",
        callback=_validate_cpu,
    ),
    model_name: Optional[str] = typer.Option(
        None,
        "--model-name",
        help=(
            "Model name to inject into OPENCODE_MODEL, CODEX_MODEL, "
            "and ANTHROPIC_MODEL when creating a tool."
        ),
    ),
    model_api_key: Optional[str] = typer.Option(
        None,
        "--model-api-key",
        help=(
            "Model API key to inject into OPENCODE_API_KEY, CODEX_API_KEY, "
            "and ANTHROPIC_AUTH_TOKEN when creating a tool."
        ),
    ),
) -> None:
    """Create an AgentKit Tool with optional TOS mount."""
    try:
        result = create_tool(
            tool_type=tool_type,
            tool_name=tool_name,
            tos_bucket=tos_bucket,
            cpu=cpu,
            model_name=model_name,
            model_api_key=model_api_key,
        )
        save_tool_result(str(result["tool_type"]), result)
    except (typer.Abort, typer.Exit):
        raise
    except Exception as exc:
        error(str(exc))

    typer.echo("工具创建成功")
    typer.echo(f"工具ID：{result['tool_id']}")
    typer.echo(f"状态：{result['status']}")
