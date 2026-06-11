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

from dataclasses import dataclass
import re
import time
from typing import Optional

import typer

from agentkit.platform import VolcConfiguration
from agentkit.sdk.tools.client import AgentkitToolsClient
from agentkit.sdk.tools import types as tools_types
from agentkit.toolkit.cli.sandbox.session_create import (
    MODEL_API_KEY_ENV_KEYS,
    MODEL_NAME_ENV_KEYS,
)
from agentkit.toolkit.cli.sandbox.utils import error
from agentkit.toolkit.config.constants import DEFAULT_TOS_BUCKET_TEMPLATE_NAME
from agentkit.toolkit.volcengine.sts import VeSTS
from agentkit.utils.misc import generate_apikey_name, generate_random_id

DEFAULT_CREATE_TOOL_REGION = "cn-beijing"
DEFAULT_CREATE_TOOL_TYPE = "CodeEnv"
DEFAULT_TOS_BUCKET_PATH = "/sandbox-session/default/default"
DEFAULT_TOS_LOCAL_PATH = "/home/gem"
DEFAULT_MODEL_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_ANTHROPIC_BASE_URL = "https://ark.cn-beijing.volces.com/api/compatible"
MODEL_BASE_URL_ENV_KEYS = (
    "OPENCODE_BASE_URL",
    "CODEX_BASE_URL",
    "MODEL_BASE_URL",
)
ANTHROPIC_BASE_URL_ENV_KEYS = ("ANTHROPIC_BASE_URL",)
TOS_DIRECTORY_CONTENT_TYPE = "application/x-directory"
TOOL_READY_STATUS = "Ready"
TOOL_FAILED_STATUSES = {"Error", "Failed", "CreateFailed", "Deleting", "Deleted"}
TOOL_WAIT_INTERVAL_SECONDS = 5
TOOL_WAIT_TIMEOUT_SECONDS = 600
PLATFORM_CREDENTIAL_SERVICE = "agentkit"


@dataclass(frozen=True)
class EnvCredentials:
    access_key: str
    secret_key: str
    session_token: Optional[str] = None


def _load_env_credentials(region: Optional[str] = None) -> EnvCredentials:
    credentials = VolcConfiguration(region=region).get_service_credentials(
        PLATFORM_CREDENTIAL_SERVICE
    )
    return EnvCredentials(
        access_key=credentials.access_key,
        secret_key=credentials.secret_key,
        session_token=credentials.session_token,
    )


def _normalize_region(region: Optional[str]) -> str:
    return (region or DEFAULT_CREATE_TOOL_REGION).strip() or DEFAULT_CREATE_TOOL_REGION


def _generate_tool_name(tool_type: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", tool_type.lower()).strip("-")
    if not normalized:
        normalized = "tool"
    return f"agentkit-{normalized}-{generate_random_id(8)}"


class _TOSBucketService:
    def __init__(
        self,
        *,
        bucket_name: str,
        region: str,
        credentials: EnvCredentials,
    ) -> None:
        try:
            import tos
        except ImportError as exc:
            raise ImportError(
                "TOS SDK not installed. Install with: pip install tos"
            ) from exc

        from agentkit.platform import VolcConfiguration

        self._tos = tos
        self.bucket_name = bucket_name
        endpoint = VolcConfiguration(
            region=region,
            access_key=credentials.access_key,
            secret_key=credentials.secret_key,
            session_token=credentials.session_token,
        ).get_service_endpoint("tos")
        self.endpoint = endpoint.host
        self.client = tos.TosClientV2(
            credentials.access_key,
            credentials.secret_key,
            endpoint.host,
            endpoint.region,
            security_token=credentials.session_token,
        )

    def bucket_exists(self) -> bool:
        try:
            self.client.head_bucket(bucket=self.bucket_name)
            return True
        except self._tos.exceptions.TosServerError as exc:
            if exc.status_code == 404:
                return False
            raise

    def create_bucket(self) -> None:
        self.client.create_bucket(bucket=self.bucket_name)

    def object_exists(self, key: str) -> bool:
        try:
            self.client.head_object(bucket=self.bucket_name, key=key)
            return True
        except self._tos.exceptions.TosServerError as exc:
            if exc.status_code == 404:
                return False
            raise

    def create_directory(self, key: str) -> None:
        self.client.put_object(
            bucket=self.bucket_name,
            key=key,
            content=b"",
            content_length=0,
            content_type=TOS_DIRECTORY_CONTENT_TYPE,
        )


def _build_tos_service(
    bucket_name: str,
    region: str,
    credentials: EnvCredentials,
) -> _TOSBucketService:
    return _TOSBucketService(
        bucket_name=bucket_name,
        region=region,
        credentials=credentials,
    )


def _build_tos_mount_endpoint(region: str) -> str:
    return f"http://tos-{region}.ivolces.com"


def _ensure_tos_bucket_ready(
    bucket_name: str,
    region: str,
    credentials: EnvCredentials,
) -> str:
    service = _build_tos_service(bucket_name, region, credentials)
    if service.bucket_exists():
        return service.endpoint

    service.create_bucket()
    typer.echo(f"TOS bucket created: {bucket_name}")
    return service.endpoint


def _build_tos_directory_keys(bucket_path: str) -> list[str]:
    parts = [part for part in bucket_path.strip("/").split("/") if part]
    keys = []
    for index in range(1, len(parts) + 1):
        keys.append("/".join(parts[:index]) + "/")
    return keys


def _ensure_tos_bucket_path_ready(
    bucket_name: str,
    bucket_path: str,
    region: str,
    credentials: EnvCredentials,
) -> None:
    service = _build_tos_service(bucket_name, region, credentials)
    created = False
    for key in _build_tos_directory_keys(bucket_path):
        if not service.object_exists(key):
            service.create_directory(key)
            created = True
    if created:
        typer.echo(f"TOS bucket path created: {bucket_path}")


def _generate_default_tos_bucket(
    credentials: EnvCredentials,
    region: str,
) -> str:
    account_id = VeSTS(
        access_key=credentials.access_key,
        secret_key=credentials.secret_key,
        region=region,
        session_token=credentials.session_token,
    ).get_account_id()
    if not account_id:
        raise ValueError("Failed to get account_id for default TOS bucket")
    return DEFAULT_TOS_BUCKET_TEMPLATE_NAME.replace("{{account_id}}", str(account_id))


def _resolve_tos_bucket(
    tos_bucket: Optional[str],
    region: str,
    credentials: EnvCredentials,
) -> str:
    resolved_bucket = (tos_bucket or "").strip()
    if resolved_bucket:
        return resolved_bucket
    return _generate_default_tos_bucket(credentials, region)


def _append_tool_envs(
    envs: list[tools_types.EnvsItemForCreateTool],
    keys: tuple[str, ...],
    value: Optional[str],
) -> None:
    resolved = (value or "").strip()
    if not resolved:
        return

    envs.extend(
        tools_types.EnvsItemForCreateTool(key=key, value=resolved)
        for key in keys
    )


def _build_tool_model_envs(
    *,
    model_name: Optional[str] = None,
    model_api_key: Optional[str] = None,
    model_base_url: Optional[str] = None,
) -> list[tools_types.EnvsItemForCreateTool] | None:
    envs: list[tools_types.EnvsItemForCreateTool] = []
    _append_tool_envs(envs, MODEL_NAME_ENV_KEYS, model_name)
    _append_tool_envs(envs, MODEL_API_KEY_ENV_KEYS, model_api_key)
    resolved_model_base_url = (model_base_url or "").strip()
    _append_tool_envs(
        envs,
        MODEL_BASE_URL_ENV_KEYS,
        resolved_model_base_url or DEFAULT_MODEL_BASE_URL,
    )
    _append_tool_envs(
        envs,
        ANTHROPIC_BASE_URL_ENV_KEYS,
        resolved_model_base_url or DEFAULT_ANTHROPIC_BASE_URL,
    )
    return envs or None


def _build_create_tool_request(
    *,
    tool_type: str,
    name: Optional[str],
    tos_bucket: Optional[str],
    region: str,
    credentials: EnvCredentials,
    model_name: Optional[str] = None,
    model_api_key: Optional[str] = None,
    model_base_url: Optional[str] = None,
) -> tools_types.CreateToolRequest:
    resolved_tool_type = tool_type.strip() or DEFAULT_CREATE_TOOL_TYPE
    resolved_name = (name or "").strip() or _generate_tool_name(resolved_tool_type)
    resolved_bucket = _resolve_tos_bucket(tos_bucket, region, credentials)
    _ensure_tos_bucket_ready(resolved_bucket, region, credentials)
    _ensure_tos_bucket_path_ready(
        resolved_bucket,
        DEFAULT_TOS_BUCKET_PATH,
        region,
        credentials,
    )
    mount_endpoint = _build_tos_mount_endpoint(region)
    tos_mount_config = tools_types.TosMountForCreateTool(
        enable_tos=True,
        credentials=tools_types.TosMountCredentialsForCreateTool(
            access_key_id=credentials.access_key,
            secret_access_key=credentials.secret_key,
        ),
        mount_points=[
            tools_types.TosMountMountPointsItemForCreateTool(
                bucket_name=resolved_bucket,
                bucket_path=DEFAULT_TOS_BUCKET_PATH,
                endpoint=mount_endpoint,
                local_mount_path=DEFAULT_TOS_LOCAL_PATH,
                read_only=False,
            )
        ],
    )

    return tools_types.CreateToolRequest(
        name=resolved_name,
        tool_type=resolved_tool_type,
        authorizer_configuration=tools_types.AuthorizerForCreateTool(
            key_auth=tools_types.AuthorizerKeyAuthForCreateTool(
                api_key_name=generate_apikey_name(),
                api_key_location="Header",
            )
        ),
        network_configuration=tools_types.NetworkForCreateTool(
            enable_public_network=True,
            enable_private_network=False,
        ),
        tos_mount_config=tos_mount_config,
        envs=_build_tool_model_envs(
            model_name=model_name,
            model_api_key=model_api_key,
            model_base_url=model_base_url,
        ),
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
        response = client.get_tool(tools_types.GetToolRequest(tool_id=tool_id))
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
    region: str = DEFAULT_CREATE_TOOL_REGION,
    model_name: Optional[str] = None,
    model_api_key: Optional[str] = None,
    model_base_url: Optional[str] = None,
) -> dict[str, object]:
    resolved_region = _normalize_region(region)
    credentials = _load_env_credentials(resolved_region)
    request = _build_create_tool_request(
        tool_type=tool_type,
        name=tool_name,
        tos_bucket=tos_bucket,
        region=resolved_region,
        credentials=credentials,
        model_name=model_name,
        model_api_key=model_api_key,
        model_base_url=model_base_url,
    )
    client = AgentkitToolsClient(
        access_key=credentials.access_key,
        secret_key=credentials.secret_key,
        region=resolved_region,
        session_token=credentials.session_token or "",
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
        help="TOS bucket to mount at /home/gem.",
    ),
    region: str = typer.Option(
        DEFAULT_CREATE_TOOL_REGION,
        "--region",
        help="Region for CreateTool and TOS bucket operations.",
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
    model_base_url: Optional[str] = typer.Option(
        None,
        "--model-base-url",
        help=(
            "Model base URL to inject into OPENCODE_BASE_URL, CODEX_BASE_URL, "
            "ANTHROPIC_BASE_URL, and MODEL_BASE_URL when creating a tool. "
            "Defaults to Volcengine Ark compatible endpoints."
        ),
    ),
) -> None:
    """Create an AgentKit Tool with optional TOS mount."""
    try:
        result = create_tool(
            tool_type=tool_type,
            tool_name=tool_name,
            tos_bucket=tos_bucket,
            region=region,
            model_name=model_name,
            model_api_key=model_api_key,
            model_base_url=model_base_url,
        )
    except (typer.Abort, typer.Exit):
        raise
    except Exception as exc:
        error(str(exc))

    typer.echo("工具创建成功")
    typer.echo(f"工具ID：{result['tool_id']}")
    typer.echo(f"状态：{result['status']}")
