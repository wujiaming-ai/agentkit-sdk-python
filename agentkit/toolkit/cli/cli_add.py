# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
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

"""AgentKit CLI - ``add`` commands.

``agentkit add harness`` writes a harness configuration file
``<name>.harness.json`` describing a deployable agent. The schema mirrors the
attributes of the VeADK harness (model / tools / skills / system prompt /
runtime, plus the knowledge-base and memory components and an optional OAuth2
auth block), serialized as a layered JSON document::

    {
      "harness_name": "my-harness",
      "model": {"name": "doubao-seed-1-6-250615"},
      "tools": ["web_search"],
      "skills": [],
      "system_prompt": "You are a helpful assistant.",
      "runtime": "adk",
      "knowledgebase": {"type": "viking", "project": "...", "region": "..."},
      "long_term_memory": {"type": ""},
      "short_term_memory": {"type": "local"},
      "auth": {"discovery_url": "...", "allowed_ids": ["..."]}
    }

Re-running ``add harness`` for the same ``--name`` merges the supplied options
into the existing file, so configuration can be built up incrementally.
"""

import json
import re
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

console = Console()

add_app = typer.Typer(
    name="add",
    help="Add and configure agent resources (e.g. a harness).",
    add_completion=False,
)

# Allowed values for the enumerated fields. ``""`` always means "leave unset"
# (the component is disabled / VeADK falls back to its own default).
_RUNTIMES = ("adk", "codex")
_KNOWLEDGEBASE_TYPES = ("viking", "opensearch", "redis", "tos_vector", "context_search")
_LONG_TERM_MEMORY_TYPES = ("viking", "opensearch", "redis", "mem0")
_SHORT_TERM_MEMORY_TYPES = ("local", "sqlite", "mysql", "postgresql")

# Harness name charset (matches `init`'s directory-name rule).
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _split_csv(value: Optional[str]) -> Optional[list[str]]:
    """Parse a comma-separated option into a trimmed list, or None if unset."""
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def _is_blank(value: object) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _prune(data: dict) -> None:
    """Drop unset fields so the file holds only what is configured.

    Empty scalars/lists are removed; a component section left with no ``type``
    is dropped entirely. ``short_term_memory`` is always kept (its ``local``
    default is meaningful).
    """
    components = ("knowledgebase", "long_term_memory", "short_term_memory")
    for key in list(data):
        value = data[key]
        if isinstance(value, dict):
            for sub in list(value):
                if _is_blank(value[sub]):
                    del value[sub]
            keep_empty_type = key == "short_term_memory"
            if key in components and not value.get("type") and not keep_empty_type:
                del data[key]
            elif not value:
                del data[key]
        elif _is_blank(value):
            del data[key]


def _validate_choice(
    label: str, value: Optional[str], allowed: tuple[str, ...]
) -> None:
    """Fast-fail when an enumerated option is set to an unsupported value."""
    if value is None or value == "":
        return
    if value not in allowed:
        console.print(
            f"[red]Error: invalid {label} '{value}'. "
            f"Allowed: {', '.join(allowed)}[/red]"
        )
        raise typer.Exit(1)


@add_app.command("harness")
def harness_command(
    name: str = typer.Option(
        ...,
        "--name",
        help="Harness name; the config is written to <name>.harness.json.",
    ),
    # --- core agent parameters ----------------------------------------------
    system_prompt: Optional[str] = typer.Option(
        None, "--system-prompt", help="Agent system prompt / instruction."
    ),
    model_name: Optional[str] = typer.Option(
        None, "--model-name", help="Reasoning model name."
    ),
    tools: Optional[str] = typer.Option(
        None, "--tools", help="Comma-separated built-in tool names (e.g. web_search)."
    ),
    skills: Optional[str] = typer.Option(
        None, "--skills", help="Comma-separated skill hub names."
    ),
    runtime: Optional[str] = typer.Option(
        None, "--runtime", help=f"Agent runtime backend ({' | '.join(_RUNTIMES)})."
    ),
    # --- component backend types --------------------------------------------
    knowledgebase_type: Optional[str] = typer.Option(
        None, "--knowledgebase-type", help="Knowledge base backend."
    ),
    long_term_memory_type: Optional[str] = typer.Option(
        None, "--long-term-memory-type", help="Long-term memory backend."
    ),
    short_term_memory_type: Optional[str] = typer.Option(
        None, "--short-term-memory-type", help="Short-term memory backend."
    ),
    # --- knowledge base connection params -----------------------------------
    knowledgebase_project: Optional[str] = typer.Option(
        None, "--knowledgebase-project", help="Knowledge base `project`."
    ),
    knowledgebase_region: Optional[str] = typer.Option(
        None, "--knowledgebase-region", help="Knowledge base `region`."
    ),
    knowledgebase_host: Optional[str] = typer.Option(
        None, "--knowledgebase-host", help="Knowledge base `host`."
    ),
    knowledgebase_port: Optional[str] = typer.Option(
        None, "--knowledgebase-port", help="Knowledge base `port`."
    ),
    knowledgebase_username: Optional[str] = typer.Option(
        None, "--knowledgebase-username", help="Knowledge base `username`."
    ),
    knowledgebase_password: Optional[str] = typer.Option(
        None, "--knowledgebase-password", help="Knowledge base `password`."
    ),
    knowledgebase_use_ssl: Optional[str] = typer.Option(
        None, "--knowledgebase-use-ssl", help="Knowledge base `use_ssl` (true/false)."
    ),
    knowledgebase_cert_path: Optional[str] = typer.Option(
        None, "--knowledgebase-cert-path", help="Knowledge base `cert_path`."
    ),
    knowledgebase_secret_token: Optional[str] = typer.Option(
        None, "--knowledgebase-secret-token", help="Knowledge base `secret_token`."
    ),
    knowledgebase_db: Optional[str] = typer.Option(
        None, "--knowledgebase-db", help="Knowledge base `db`."
    ),
    # --- long-term memory connection params ---------------------------------
    long_term_memory_project: Optional[str] = typer.Option(
        None, "--long-term-memory-project", help="Long-term memory `project`."
    ),
    long_term_memory_region: Optional[str] = typer.Option(
        None, "--long-term-memory-region", help="Long-term memory `region`."
    ),
    long_term_memory_host: Optional[str] = typer.Option(
        None, "--long-term-memory-host", help="Long-term memory `host`."
    ),
    long_term_memory_port: Optional[str] = typer.Option(
        None, "--long-term-memory-port", help="Long-term memory `port`."
    ),
    long_term_memory_username: Optional[str] = typer.Option(
        None, "--long-term-memory-username", help="Long-term memory `username`."
    ),
    long_term_memory_password: Optional[str] = typer.Option(
        None, "--long-term-memory-password", help="Long-term memory `password`."
    ),
    long_term_memory_db: Optional[str] = typer.Option(
        None, "--long-term-memory-db", help="Long-term memory `db`."
    ),
    long_term_memory_api_key: Optional[str] = typer.Option(
        None, "--long-term-memory-api-key", help="Long-term memory `api_key`."
    ),
    long_term_memory_api_key_id: Optional[str] = typer.Option(
        None, "--long-term-memory-api-key-id", help="Long-term memory `api_key_id`."
    ),
    long_term_memory_project_id: Optional[str] = typer.Option(
        None, "--long-term-memory-project-id", help="Long-term memory `project_id`."
    ),
    long_term_memory_base_url: Optional[str] = typer.Option(
        None, "--long-term-memory-base-url", help="Long-term memory `base_url`."
    ),
    # --- short-term memory connection params --------------------------------
    short_term_memory_host: Optional[str] = typer.Option(
        None, "--short-term-memory-host", help="Short-term memory `host`."
    ),
    short_term_memory_port: Optional[str] = typer.Option(
        None, "--short-term-memory-port", help="Short-term memory `port`."
    ),
    short_term_memory_user: Optional[str] = typer.Option(
        None, "--short-term-memory-user", help="Short-term memory `user`."
    ),
    short_term_memory_password: Optional[str] = typer.Option(
        None, "--short-term-memory-password", help="Short-term memory `password`."
    ),
    short_term_memory_database: Optional[str] = typer.Option(
        None, "--short-term-memory-database", help="Short-term memory `database`."
    ),
    short_term_memory_charset: Optional[str] = typer.Option(
        None, "--short-term-memory-charset", help="Short-term memory `charset`."
    ),
    # --- optional OAuth2 / JWT auth -----------------------------------------
    discovery_url: Optional[str] = typer.Option(
        None, "--discovery-url", help="OIDC discovery URL; enables OAuth2/JWT auth."
    ),
    allowed_id: Optional[str] = typer.Option(
        None, "--allowed-id", help="Comma-separated allowed client IDs for OAuth2/JWT."
    ),
    directory: str = typer.Option(
        ".", "--directory", help="Directory to write <name>.harness.json into."
    ),
):
    """Create or update a harness config file ``<name>.harness.json``.

    Each option SETS its value; ``--tools`` / ``--skills`` / ``--allowed-id``
    take comma-separated lists. Connection params are written under their
    component section alongside its ``type``. Re-running for the same ``--name``
    merges options into the existing file.
    """
    if not _NAME_RE.match(name):
        console.print(
            f"[red]Error: harness name '{name}' contains invalid characters. "
            "Only letters, numbers, hyphens, and underscores are allowed.[/red]"
        )
        raise typer.Exit(1)

    _validate_choice("--runtime", runtime, _RUNTIMES)
    _validate_choice("--knowledgebase-type", knowledgebase_type, _KNOWLEDGEBASE_TYPES)
    _validate_choice(
        "--long-term-memory-type", long_term_memory_type, _LONG_TERM_MEMORY_TYPES
    )
    _validate_choice(
        "--short-term-memory-type", short_term_memory_type, _SHORT_TERM_MEMORY_TYPES
    )

    target = Path(directory).resolve() / f"{name}.harness.json"
    if target.exists() and not target.is_file():
        console.print(f"[red]Error: '{target}' exists but is not a file.[/red]")
        raise typer.Exit(1)

    # Start from the existing file (merge) or a minimal default scaffold.
    if target.is_file():
        data = json.loads(target.read_text())
    else:
        data = {
            "harness_name": name,
            "runtime": "adk",
            "short_term_memory": {"type": "local"},
        }
    data["harness_name"] = name

    if model_name is not None:
        model = data.get("model")
        data["model"] = model if isinstance(model, dict) else {}
        data["model"]["name"] = model_name
    if system_prompt is not None:
        data["system_prompt"] = system_prompt
    if runtime is not None:
        data["runtime"] = runtime
    tools_list = _split_csv(tools)
    if tools_list is not None:
        data["tools"] = tools_list
    skills_list = _split_csv(skills)
    if skills_list is not None:
        data["skills"] = skills_list

    # Component sections: backend `type` plus its connection params.
    component_params: dict[str, dict[str, Optional[str]]] = {
        "knowledgebase": {
            "type": knowledgebase_type,
            "project": knowledgebase_project,
            "region": knowledgebase_region,
            "host": knowledgebase_host,
            "port": knowledgebase_port,
            "username": knowledgebase_username,
            "password": knowledgebase_password,
            "use_ssl": knowledgebase_use_ssl,
            "cert_path": knowledgebase_cert_path,
            "secret_token": knowledgebase_secret_token,
            "db": knowledgebase_db,
        },
        "long_term_memory": {
            "type": long_term_memory_type,
            "project": long_term_memory_project,
            "region": long_term_memory_region,
            "host": long_term_memory_host,
            "port": long_term_memory_port,
            "username": long_term_memory_username,
            "password": long_term_memory_password,
            "db": long_term_memory_db,
            "api_key": long_term_memory_api_key,
            "api_key_id": long_term_memory_api_key_id,
            "project_id": long_term_memory_project_id,
            "base_url": long_term_memory_base_url,
        },
        "short_term_memory": {
            "type": short_term_memory_type,
            "host": short_term_memory_host,
            "port": short_term_memory_port,
            "user": short_term_memory_user,
            "password": short_term_memory_password,
            "database": short_term_memory_database,
            "charset": short_term_memory_charset,
        },
    }
    for component, params in component_params.items():
        for param, value in params.items():
            if value is None:
                continue
            section = data.get(component)
            if not isinstance(section, dict):
                section = {}
                data[component] = section
            section[param] = value

    # Optional auth block (presence of either flag enables OAuth2/JWT).
    allowed_ids = _split_csv(allowed_id)
    if discovery_url is not None or allowed_ids is not None:
        auth = data.get("auth")
        auth = auth if isinstance(auth, dict) else {}
        if discovery_url is not None:
            auth["discovery_url"] = discovery_url
        if allowed_ids is not None:
            auth["allowed_ids"] = allowed_ids
        data["auth"] = auth

    _prune(data)
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    console.print(f"[green]✓ Wrote harness config: {target}[/green]")


# Credential types accepted by ``agentkit add credential``. Only ``api-key`` is
# wired up today; the value maps to the API's ``AuthType`` field.
_CREDENTIAL_AUTH_TYPES = {"api-key": "ApiKey"}


@add_app.command("credential")
def credential_command(
    type_: str = typer.Option(
        ...,
        "--type",
        help=f"Credential type ({' | '.join(_CREDENTIAL_AUTH_TYPES)}).",
    ),
    name: str = typer.Option(
        ..., "--name", help="Credential name (used as the API key name)."
    ),
    api_key: Optional[str] = typer.Option(
        None, "--api-key", help="API key value (required for --type api-key)."
    ),
    region: Optional[str] = typer.Option(
        None,
        "--region",
        help=(
            "Region override for this command (e.g. cn-beijing, cn-shanghai). "
            "Defaults to VOLCENGINE_AGENTKIT_REGION/VOLCENGINE_REGION/global config."
        ),
    ),
):
    """Add a credential by creating an inbound auth config.

    ``--type api-key`` registers ``--api-key`` under ``--name`` so deployed
    agents can authenticate inbound requests with that key.
    """
    auth_type = _CREDENTIAL_AUTH_TYPES.get(type_)
    if auth_type is None:
        console.print(
            f"[red]Error: invalid --type '{type_}'. "
            f"Allowed: {', '.join(_CREDENTIAL_AUTH_TYPES)}[/red]"
        )
        raise typer.Exit(1)
    if not name.strip():
        console.print("[red]Error: --name must not be empty.[/red]")
        raise typer.Exit(1)
    if not api_key:
        console.print("[red]Error: --api-key is required for --type api-key.[/red]")
        raise typer.Exit(1)

    from agentkit.sdk.identity.client import AgentkitIdentityClient
    from agentkit.sdk.identity import types as it

    client = AgentkitIdentityClient(region=(region or "").strip())
    request = it.CreateInboundAuthConfigRequest(
        auth_type=auth_type,
        config_name=name,
        api_key_auth_configs=[it.ApiKeyAuthConfig(api_key_name=name, api_key=api_key)],
    )
    response = client.create_inbound_auth_config(request)

    console.print(f"[green]✓ Created credential '{name}'[/green]")
    if response.inbound_auth_config_id:
        console.print(f"  InboundAuthConfigId: {response.inbound_auth_config_id}")
