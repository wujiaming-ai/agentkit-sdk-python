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

"""AgentKit CLI - ``list`` commands.

``agentkit list harness`` enumerates every AgentKit runtime reachable with the
configured Volcengine credentials (AK/SK read from the environment) and keeps
only the ones deployed as a harness — i.e. tagged ``agentkit:agenttype=harness``
at deploy time (see :func:`agentkit.toolkit.harness.deploy.deploy_harness`).
"""

from typing import Optional

import typer
from rich.console import Console

from agentkit.sdk.runtime.client import AgentkitRuntimeClient
from agentkit.sdk.runtime import types as rt
from agentkit.toolkit.harness.deploy import HARNESS_TAG_KEY, HARNESS_TAG_VALUE

console = Console()

list_app = typer.Typer(
    name="list",
    help="List AgentKit resources.",
    add_completion=False,
)


def _is_harness_runtime(runtime: rt.AgentKitRuntimesForListRuntimes) -> bool:
    """True when the runtime carries the deploy-time harness tag."""
    for tag in runtime.tags or []:
        if tag.key == HARNESS_TAG_KEY and tag.value == HARNESS_TAG_VALUE:
            return True
    return False


# Fixed ADK app name the harness loader serves its agent under (the deployed
# HARNESS_NAME is irrelevant to the ADK app path).
_HARNESS_APP = "harness"


def _user_id_from_token(token: str) -> Optional[str]:
    """Return the OIDC ``sub`` claim from a JWT bearer token, else ``None``.

    A custom_jwt harness is reached with an OIDC id_token whose ``sub`` is the
    authenticated user's stable id. A key_auth token is an opaque api key (not a
    JWT, no ``sub``), so this returns ``None`` and the caller must be given an
    explicit ``--user-id``.

    NOTE: this mirrors ``cli_invoke._user_id_from_token``; consolidate the two
    into one shared helper once the ``invoke harness`` run_sse work lands.
    """
    import base64
    import binascii
    import json

    parts = token.split(".")
    if len(parts) != 3:
        return None  # not a JWT (e.g. a key_auth api key)
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)  # restore base64 padding
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, binascii.Error):
        return None  # malformed JWT payload
    sub = claims.get("sub")
    return sub if isinstance(sub, str) and sub else None


@list_app.command("harness")
def list_harness_command(
    region: Optional[str] = typer.Option(
        None,
        "--region",
        help=(
            "Region override for this command (e.g. cn-beijing, cn-shanghai). "
            "Defaults to VOLCENGINE_AGENTKIT_REGION/VOLCENGINE_REGION/global config."
        ),
    ),
    output: str = typer.Option(
        "table", "--output", help="Output format: table|json|yaml"
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Print only RuntimeId values"
    ),
    no_color: bool = typer.Option(
        False, "--no-color", "-nc", help="Disable colored output for tables/panels"
    ),
    limit: int = typer.Option(
        50, "--limit", "-l", help="Runtimes fetched per batch while paging"
    ),
):
    """List runtimes deployed as a harness (tagged ``agentkit:agenttype=harness``).

    Fetches every runtime visible to the configured credentials, then filters to
    those carrying the harness tag stamped at deploy time.
    """
    from agentkit.toolkit.cli.utils import PaginationHelper, OutputFormatter

    local_console = console if not no_color else Console(no_color=True)

    client = AgentkitRuntimeClient(region=(region or "").strip())

    def build_request(next_token_val):
        return rt.ListRuntimesRequest(
            max_results=limit,
            next_token=next_token_val,
        )

    # Paging through every runtime can take a few seconds; show a spinner so the
    # CLI doesn't look frozen while it fetches.
    with local_console.status("[cyan]Fetching runtimes...[/cyan]", spinner="dots"):
        runtimes, _, _ = PaginationHelper.fetch_all_pages(
            request_func=client.list_runtimes,
            request_builder=build_request,
            max_results=limit,
            next_token=None,
            fetch_all=True,
            max_batches=None,
            sleep_ms=0,
        )

    harnesses = [r for r in runtimes if _is_harness_runtime(r)]

    if quiet:
        for r in harnesses:
            local_console.print(r.runtime_id or "")
        return

    if output.lower() == "json":
        local_console.print(OutputFormatter.format_json_output(harnesses, False, {}))
        return
    if output.lower() == "yaml":
        local_console.print(OutputFormatter.format_yaml_output(harnesses, False, {}))
        return

    columns = [
        ("RuntimeId", "RuntimeId", "cyan"),
        ("Name", "Name", "white"),
        ("Status", "Status", "green"),
        ("CurrentVersionNumber", "Version", "blue"),
        ("ProjectName", "ProjectName", "yellow"),
        ("UpdatedAt", "UpdatedAt", "magenta"),
    ]
    table = OutputFormatter.create_table(
        items=harnesses,
        columns=columns,
        title=f"Harness Runtimes (Count: {len(harnesses)})",
    )
    local_console.print(table)


def _resolve_harness_endpoint(harness: str, directory: str, apikey: Optional[str]):
    """Resolve a deployed harness's data-plane base URL and bearer token.

    Mirrors ``invoke harness``'s auth-type-aware credential selection:
      1. ``--apikey`` always wins (explicit override);
      2. a custom_jwt harness (no stored ``key``) → the ``agentkit login`` OIDC
         id_token (auto-refreshed), whose ``sub`` identifies the caller;
      3. a key_auth harness → its static ``key`` (an opaque api key, not a JWT).

    Returns ``(base_url, token)``. Fast-fails when the harness is not in the
    registry, or when a needed login id_token cannot be refreshed.
    """
    from agentkit.toolkit.harness import load_harness_registry

    registry = load_harness_registry(directory)
    entry = registry.get(harness)
    if not isinstance(entry, dict) or not entry.get("url"):
        from pathlib import Path

        registry_path = Path(directory).resolve() / "harness.json"
        console.print(
            f"[red]Error: harness '{harness}' not found in registry {registry_path}. "
            f"Deploy it first with `agentkit deploy --harness {harness}`.[/red]"
        )
        raise typer.Exit(1)

    base_url = entry["url"].rstrip("/")

    from agentkit.auth.errors import AuthError
    from agentkit.auth.sso import load_session

    is_jwt_harness = entry.get("auth_type") == "custom_jwt" or not entry.get("key")
    token = apikey or ""
    if not token and is_jwt_harness:
        try:
            auth_session = load_session()
        except Exception:
            auth_session = None
        if auth_session is not None:
            try:
                token = auth_session.valid_id_token()
            except AuthError as e:
                console.print(f"[red]❌ {e}[/red]")
                if getattr(e, "hint", None):
                    console.print(f"[yellow]{e.hint}[/yellow]")
                raise typer.Exit(1)
    if not token:
        token = entry.get("key") or ""

    return base_url, token


@list_app.command("sessions")
def list_sessions_command(
    harness: str = typer.Option(
        ..., "--harness", help="Harness name (resolved via the harness.json registry)."
    ),
    user_id: Optional[str] = typer.Option(
        None,
        "--user-id",
        help=(
            "user_id whose sessions to list. If omitted, it is taken from the "
            "JWT `sub` of the harness credential; a key_auth harness has no JWT, "
            "so --user-id is required there."
        ),
    ),
    apikey: Optional[str] = typer.Option(
        None,
        "--apikey",
        "-ak",
        help="Bearer token (e.g. a custom_jwt harness's OIDC JWT) overriding the registry credential.",
    ),
    directory: str = typer.Option(
        ".", "--directory", help="Directory containing harness.json."
    ),
    output: str = typer.Option(
        "table", "--output", help="Output format: table|json|yaml"
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Print only session ids"
    ),
    no_color: bool = typer.Option(
        False, "--no-color", "-nc", help="Disable colored output for tables/panels"
    ),
):
    """List a user's conversation sessions on a deployed harness runtime.

    Calls the harness runtime's ADK endpoint
    ``GET /apps/harness/users/{user_id}/sessions``. ADK requires a user_id (there
    is no cross-user listing), so it must be given explicitly via --user-id or be
    derivable from the JWT `sub` of the harness credential.
    """
    import json
    from datetime import datetime

    import requests
    from rich.table import Table

    local_console = console if not no_color else Console(no_color=True)

    base_url, token = _resolve_harness_endpoint(harness, directory, apikey)

    resolved_user_id = user_id or _user_id_from_token(token)
    if not resolved_user_id:
        local_console.print(
            "[red]Error: cannot determine user_id — no --user-id given and the "
            "harness credential is not a JWT with a `sub` claim (e.g. a key_auth "
            "harness). Pass --user-id explicitly.[/red]"
        )
        raise typer.Exit(1)

    url = f"{base_url}/apps/{_HARNESS_APP}/users/{resolved_user_id}/sessions"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    with local_console.status("[cyan]Fetching sessions...[/cyan]", spinner="dots"):
        try:
            resp = requests.get(url, headers=headers, timeout=60)
        except requests.RequestException as e:
            local_console.print(f"[red]❌ List sessions request failed: {e}[/red]")
            raise typer.Exit(1)

    if resp.status_code != 200:
        local_console.print(
            f"[red]❌ List sessions HTTP {resp.status_code}: {resp.text[:300]}[/red]"
        )
        raise typer.Exit(1)

    sessions = resp.json() or []

    if quiet:
        for s in sessions:
            local_console.print(s.get("id", ""))
        return

    if output.lower() == "json":
        local_console.print(json.dumps(sessions, indent=2, ensure_ascii=False))
        return
    if output.lower() == "yaml":
        import yaml

        local_console.print(yaml.safe_dump(sessions, sort_keys=False, allow_unicode=True))
        return

    table = Table(
        title=f"Sessions for user '{resolved_user_id}' (Count: {len(sessions)})",
        show_lines=False,
    )
    table.add_column("SessionId", style="cyan")
    table.add_column("Events", style="green")
    table.add_column("LastUpdate", style="magenta")
    for s in sessions:
        ts = s.get("lastUpdateTime")
        last_update = (
            datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
            if isinstance(ts, (int, float)) and ts
            else ""
        )
        table.add_row(
            str(s.get("id", "")),
            str(len(s.get("events", []))),
            last_update,
        )
    local_console.print(table)


@list_app.command("credentials")
def list_credentials_command(
    region: Optional[str] = typer.Option(
        None,
        "--region",
        help=(
            "Region override for this command (e.g. cn-beijing, cn-shanghai). "
            "Defaults to VOLCENGINE_AGENTKIT_REGION/VOLCENGINE_REGION/global config."
        ),
    ),
    output: str = typer.Option(
        "table", "--output", help="Output format: table|json|yaml"
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Print only credential names"
    ),
    no_color: bool = typer.Option(
        False, "--no-color", "-nc", help="Disable colored output for tables/panels"
    ),
    limit: int = typer.Option(
        50, "--limit", "-l", help="Configs fetched per batch while paging"
    ),
):
    """List credentials (inbound auth configs) visible to the credentials."""
    from agentkit.toolkit.cli.utils import PaginationHelper, OutputFormatter
    from agentkit.sdk.identity.client import AgentkitIdentityClient
    from agentkit.sdk.identity import types as it

    local_console = console if not no_color else Console(no_color=True)

    client = AgentkitIdentityClient(region=(region or "").strip())

    def build_request(next_token_val):
        return it.ListInboundAuthConfigsRequest(
            max_results=limit,
            next_token=next_token_val,
        )

    with local_console.status("[cyan]Fetching credentials...[/cyan]", spinner="dots"):
        configs, _, _ = PaginationHelper.fetch_all_pages(
            request_func=client.list_inbound_auth_configs,
            request_builder=build_request,
            max_results=limit,
            next_token=None,
            fetch_all=True,
            max_batches=None,
            sleep_ms=0,
        )

    if quiet:
        for c in configs:
            local_console.print(c.config_name or "")
        return

    if output.lower() == "json":
        local_console.print(OutputFormatter.format_json_output(configs, False, {}))
        return
    if output.lower() == "yaml":
        local_console.print(OutputFormatter.format_yaml_output(configs, False, {}))
        return

    columns = [
        ("ConfigName", "Name", "cyan"),
        ("AuthType", "AuthType", "white"),
        ("InboundAuthConfigId", "InboundAuthConfigId", "green"),
        ("CreatedAt", "CreatedAt", "magenta"),
    ]
    table = OutputFormatter.create_table(
        items=configs,
        columns=columns,
        title=f"Credentials (Count: {len(configs)})",
    )
    local_console.print(table)
