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
