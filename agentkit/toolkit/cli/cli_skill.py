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

from __future__ import annotations

from typing import List, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agentkit.sdk.skills.client import AgentkitSkillsClient
from agentkit.sdk.skills import types as skills_types
from agentkit.toolkit.cli.cli_skills import _dump_output, _print_api_error
from agentkit.toolkit.cli.cli_skills_workflow import _wait_for_running_version

console = Console()

skill_app = typer.Typer(
    name="skill",
    help="Install and manage individual Skills",
    add_completion=False,
)

_REGION_HELP = (
    "Region override for this command (e.g. cn-beijing, cn-shanghai). "
    "Defaults to VOLCENGINE_AGENTKIT_REGION/VOLCENGINE_REGION/global config."
)


def _make_client(region: Optional[str]) -> AgentkitSkillsClient:
    """Build the skills client, surfacing missing credentials cleanly."""
    try:
        return AgentkitSkillsClient(region=(region or "").strip())
    except ValueError as e:
        console.print(
            Panel.fit(f"[red]{e}[/red]", title="Credentials Error", border_style="red")
        )
        raise typer.Exit(1)


def _resolve_skill_space_id(client: AgentkitSkillsClient, skill_space: str) -> str:
    """Resolve a ``--skill-space`` value to a SkillSpace ID.

    A value starting with ``ss-`` is treated as the ID and returned as-is;
    otherwise it is treated as a SkillSpace name and resolved to its ID via an
    exact-name lookup.
    """
    value = (skill_space or "").strip()
    if value.startswith("ss-"):
        return value

    resp = client.list_skill_spaces(
        skills_types.ListSkillSpacesRequest(
            page_number=1,
            page_size=50,
            filter=skills_types.SkillSpaceFilter(name=value),
        )
    )
    matches = [s for s in (resp.items or []) if s.name == value]
    if not matches:
        raise typer.BadParameter(f"SkillSpace not found by name: '{value}'")
    if len(matches) > 1:
        raise typer.BadParameter(
            f"Multiple SkillSpaces named '{value}'. Use the 'ss-...' ID instead."
        )
    return matches[0].id


def _resolve_skill_id(
    client: AgentkitSkillsClient, skill: str, project_name: str
) -> str:
    """Resolve a Skill reference to a Skill ID.

    A value starting with ``s-`` is treated as the ID; otherwise it is treated
    as a Skill name and resolved to its ID via an exact-name lookup.
    """
    value = (skill or "").strip()
    if value.startswith("s-"):
        return value

    resp = client.list_skills(
        skills_types.ListSkillsRequest(
            page_number=1,
            page_size=50,
            filter=skills_types.SkillFilter(name=value),
            project_name=project_name,
        )
    )
    matches = [s for s in (resp.items or []) if s.name == value]
    if not matches:
        raise typer.BadParameter(f"Skill not found by name: '{value}'")
    if len(matches) > 1:
        raise typer.BadParameter(
            f"Multiple Skills named '{value}'. Use the 's-...' ID instead."
        )
    return matches[0].id


@skill_app.command("install")
def install_skill_command(
    skill_name: str = typer.Argument(
        ..., help="Name of the Skill to install (e.g. theme-factory)"
    ),
    skill_space: Optional[str] = typer.Option(
        None,
        "--skill-space",
        help=(
            "Optional target SkillSpace: an 'ss-...' ID or a name. "
            "When omitted, the Skill is only created (not added to any SkillSpace)."
        ),
    ),
    project_name: str = typer.Option(
        "default", "--project-name", help="Project name"
    ),
    wait: bool = typer.Option(
        True,
        "--wait/--no-wait",
        help="Poll until the Skill leaves 'creating' and becomes running",
    ),
    wait_timeout: int = typer.Option(
        300, "--wait-timeout", help="Max seconds to wait for the Skill to become running"
    ),
    poll_interval: int = typer.Option(
        5, "--poll-interval", help="Seconds between status polls while waiting"
    ),
    output: str = typer.Option("yaml", "--output", "-o", help="Output: json|yaml"),
    region: Optional[str] = typer.Option(None, "--region", help=_REGION_HELP),
):
    """Install a Skill (optionally into a SkillSpace).

    Generates a temporary TOS URL for the named Skill package, then creates the
    Skill from that URL. If ``--skill-space`` is given, the Skill is also added
    to that SkillSpace.
    """
    client = _make_client(region)
    space_id = (
        _resolve_skill_space_id(client, skill_space)
        if (skill_space or "").strip()
        else None
    )

    # Step 1: generate a temporary TOS URL for the Skill package.
    try:
        temp = client.gen_temp_tos_object_url(
            skills_types.GenTempTosObjectUrlRequest(
                project_name=project_name,
                skill_name=skill_name,
            )
        )
    except Exception as e:
        _print_api_error(
            "GenTempTosObjectUrl",
            e,
            hints=[
                f"Verify the Skill name '{skill_name}' exists for project '{project_name}'",
            ],
        )
        raise typer.Exit(1)

    tos_url = (temp.url or "").strip()
    if not tos_url:
        console.print(
            Panel.fit(
                "[red]No TOS URL in GenTempTosObjectUrl response.[/red]\n"
                f"Returned fields: {sorted((temp.model_extra or {}).keys())}",
                title="GenTempTosObjectUrl Error",
                border_style="red",
            )
        )
        raise typer.Exit(1)

    # Step 2: create the Skill from the temporary TOS URL.
    try:
        resp = client.create_skill(
            skills_types.CreateSkillRequest(
                name=skill_name,
                tos_url=tos_url,
                skill_spaces=[space_id] if space_id else None,
                project_name=project_name,
            )
        )
    except Exception as e:
        _print_api_error(
            "CreateSkill",
            e,
            hints=["Use 'agentkit skills space list' to find valid SkillSpace IDs"],
        )
        raise typer.Exit(1)

    skill_id = resp.id

    # Step 3: a freshly created Skill starts in 'creating'; poll until running.
    version = ""
    if wait:
        try:
            with console.status(
                f"[cyan]Skill '{skill_name}' is creating, waiting until running...[/cyan]"
            ):
                latest = _wait_for_running_version(
                    client=client,
                    skill_id=skill_id,
                    timeout_seconds=int(wait_timeout),
                    poll_interval_seconds=int(poll_interval),
                )
            version = latest.version or ""
        except (ValueError, TimeoutError) as e:
            console.print(
                Panel.fit(
                    "\n".join(
                        [
                            "[red]Skill created but did not become running.[/red]",
                            f"SkillId: {skill_id}",
                            f"Reason: {e}",
                        ]
                    ),
                    title="Skill Install",
                    border_style="red",
                )
            )
            raise typer.Exit(1)

    lines = [
        "[green]✅ Installed[/green]",
        f"Skill: {skill_name}",
        f"SkillId: {skill_id}",
        f"SkillSpace: {space_id or '(none)'}",
    ]
    if wait:
        lines.append(f"Version: {version} (running)")
    else:
        lines.append("Status: creating (use 'agentkit skill list' to check)")
    console.print(Panel.fit("\n".join(lines), title="Skill Install", border_style="green"))
    _dump_output(resp.model_dump(by_alias=True, exclude_none=True), output)


@skill_app.command("uninstall")
def uninstall_skill_command(
    skill: str = typer.Argument(
        ..., help="Skill name or 's-...' ID to uninstall (e.g. research-utils)"
    ),
    skill_space: Optional[str] = typer.Option(
        None,
        "--skill-space",
        help=(
            "Optional SkillSpace: an 'ss-...' ID or a name. When given, the Skill "
            "is only removed from that SkillSpace. When omitted, the Skill is deleted."
        ),
    ),
    project_name: str = typer.Option(
        "default", "--project-name", help="Project name (for resolving Skill by name)"
    ),
    force: bool = typer.Option(False, "--force", help="Skip confirmation"),
    region: Optional[str] = typer.Option(None, "--region", help=_REGION_HELP),
):
    """Uninstall a Skill.

    With ``--skill-space`` the Skill is only removed from that SkillSpace (the
    Skill itself is kept). Without it, the Skill is deleted entirely.
    """
    client = _make_client(region)
    skill_id = _resolve_skill_id(client, skill, project_name)
    space_id = (
        _resolve_skill_space_id(client, skill_space)
        if (skill_space or "").strip()
        else None
    )

    if space_id:
        if not force:
            typer.confirm(
                f"Remove Skill {skill_id} from SkillSpace {space_id}?", abort=True
            )
        try:
            client.remove_skill_from_skill_space(
                skills_types.RemoveSkillFromSkillSpaceRequest(
                    skill_id=skill_id, skill_space_id=space_id
                )
            )
        except Exception as e:
            _print_api_error("RemoveSkillFromSkillSpace", e)
            raise typer.Exit(1)
        console.print(
            Panel.fit(
                "\n".join(
                    [
                        "[green]✅ Removed from SkillSpace[/green]",
                        f"SkillId: {skill_id}",
                        f"SkillSpace: {space_id}",
                    ]
                ),
                title="Skill Uninstall",
                border_style="green",
            )
        )
        return

    if not force:
        typer.confirm(
            f"Delete Skill {skill_id}? This cannot be undone.", abort=True
        )
    try:
        client.delete_skill(skills_types.DeleteSkillRequest(id=skill_id))
    except Exception as e:
        _print_api_error("DeleteSkill", e)
        raise typer.Exit(1)
    console.print(
        Panel.fit(
            f"[green]✅ Deleted[/green]\nSkillId: {skill_id}",
            title="Skill Uninstall",
            border_style="green",
        )
    )


@skill_app.command("list")
def list_skill_command(
    skill_space: Optional[str] = typer.Option(
        None,
        "--skill-space",
        help=(
            "Optional SkillSpace: an 'ss-...' ID or a name. When given, lists the "
            "Skills in that SkillSpace. When omitted, lists all Skills."
        ),
    ),
    name: Optional[str] = typer.Option(None, "--name", help="Filter by Skill name"),
    status: Optional[str] = typer.Option(
        None, "--status", help="Filter by status (comma-separated for multiple)"
    ),
    project_name: Optional[str] = typer.Option(None, "--project-name", help="Project"),
    page_number: int = typer.Option(1, "--page-number", help="Page number (1-based)"),
    page_size: int = typer.Option(20, "--page-size", help="Page size"),
    all_pages: bool = typer.Option(False, "--all", help="Fetch all pages"),
    output: str = typer.Option(
        "table", "--output", "-o", help="Output: table|json|yaml"
    ),
    region: Optional[str] = typer.Option(None, "--region", help=_REGION_HELP),
):
    """List Skills, optionally scoped to a SkillSpace."""
    client = _make_client(region)
    pn = max(1, int(page_number))
    ps = max(1, int(page_size))
    space_id = (
        _resolve_skill_space_id(client, skill_space)
        if (skill_space or "").strip()
        else None
    )

    if space_id:
        relation_filter = skills_types.SkillRelationFilter()
        has_filter = False
        if status:
            relation_filter.status = [s.strip() for s in status.split(",") if s.strip()]
            has_filter = True
        if name:
            relation_filter.name = name.strip()
            has_filter = True

        items: List[skills_types.Relation] = []
        try:
            while True:
                resp = client.list_skills_by_skill_space(
                    skills_types.ListSkillsBySkillSpaceRequest(
                        skill_space_id=space_id,
                        filter=relation_filter if has_filter else None,
                        page_number=pn,
                        page_size=ps,
                    )
                )
                batch = resp.items or []
                items.extend(batch)
                total = resp.total_count or 0
                if not all_pages or len(items) >= total:
                    break
                pn += 1
        except Exception as e:
            _print_api_error("ListSkillsBySkillSpace", e)
            raise typer.Exit(1)

        fmt = (output or "table").lower().strip()
        if fmt in {"json", "yaml"}:
            _dump_output(
                {
                    "TotalCount": len(items),
                    "Items": [
                        i.model_dump(by_alias=True, exclude_none=True) for i in items
                    ],
                },
                fmt,
            )
            return

        table = Table(title=f"Skills in SkillSpace ({space_id})")
        table.add_column("SkillId", style="cyan", no_wrap=True)
        table.add_column("SkillName", style="green")
        table.add_column("Status")
        table.add_column("Version")
        for r in items:
            table.add_row(
                r.skill_id, r.skill_name or "", r.skill_status or "", r.version or ""
            )
        console.print(table)
        return

    skill_filter = skills_types.SkillFilter()
    has_filter = False
    if name:
        skill_filter.name = name.strip()
        has_filter = True
    if status:
        skill_filter.status = [s.strip() for s in status.split(",") if s.strip()]
        has_filter = True

    skills: List[skills_types.Skill] = []
    try:
        while True:
            resp = client.list_skills(
                skills_types.ListSkillsRequest(
                    page_number=pn,
                    page_size=ps,
                    filter=skill_filter if has_filter else None,
                    project_name=project_name,
                )
            )
            batch = resp.items or []
            skills.extend(batch)
            total = resp.total_count or 0
            if not all_pages or len(skills) >= total:
                break
            pn += 1
    except Exception as e:
        _print_api_error("ListSkills", e, hints=["Remove filters to view all Skills"])
        raise typer.Exit(1)

    fmt = (output or "table").lower().strip()
    if fmt in {"json", "yaml"}:
        _dump_output(
            {
                "TotalCount": len(skills),
                "Items": [
                    i.model_dump(by_alias=True, exclude_none=True) for i in skills
                ],
            },
            fmt,
        )
        return

    table = Table(title="Skills")
    table.add_column("Id", style="cyan", no_wrap=True)
    table.add_column("Name", style="green")
    table.add_column("Status")
    table.add_column("Versions")
    table.add_column("Project")
    table.add_column("UpdatedAt")
    for s in skills:
        table.add_row(
            s.id,
            s.name,
            s.status,
            ",".join(s.versions or []),
            s.project_name,
            s.update_time_stamp,
        )
    console.print(table)
