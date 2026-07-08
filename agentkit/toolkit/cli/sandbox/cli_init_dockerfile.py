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

"""Initialize Dockerfile templates for custom sandbox images."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import typer

from agentkit.toolkit.cli.sandbox.sandbox_client import error


@dataclass(frozen=True)
class DockerfileTemplate:
    name: str
    resource_path: str
    default_output: str
    description: str


_TEMPLATE_ROOT = (
    Path(__file__).resolve().parents[2]
    / "resources"
    / "templates"
    / "sandbox"
)

_AVAILABLE_TEMPLATES: dict[str, DockerfileTemplate] = {
    "package": DockerfileTemplate(
        name="package",
        resource_path="Dockerfile.install-package",
        default_output="Dockerfile.install-package",
        description="Base code-cli image with additional npm packages installed.",
    ),
    "skill": DockerfileTemplate(
        name="skill",
        resource_path="Dockerfile.install-skills",
        default_output="Dockerfile.install-skills",
        description="Base code-cli image with local Codex skills copied in.",
    ),
    "web-server": DockerfileTemplate(
        name="web-server",
        resource_path="Dockerfile.web-server",
        default_output="Dockerfile.web-server",
        description="Base code-cli image with nginx routing to a local server.",
    ),
}


def _resolve_template(template: str) -> DockerfileTemplate:
    normalized = (template or "").strip()
    if normalized in _AVAILABLE_TEMPLATES:
        return _AVAILABLE_TEMPLATES[normalized]
    valid = ", ".join(_AVAILABLE_TEMPLATES.keys())
    error(f"Unknown Dockerfile template '{template}'. Valid templates: {valid}")


def _read_template_file(template: DockerfileTemplate) -> str:
    path = _TEMPLATE_ROOT / template.resource_path
    if not path.is_file():
        raise FileNotFoundError(f"Dockerfile template resource not found: {path}")
    return path.read_text(encoding="utf-8")


def init_dockerfile_command(
    template: str = typer.Option(
        "package",
        "--template",
        "-t",
        help=(
            "Dockerfile template to generate. Available: "
            f"{', '.join(_AVAILABLE_TEMPLATES.keys())}."
        ),
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output Dockerfile path. Defaults to the template's filename.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite the output file if it already exists.",
    ),
) -> None:
    """Create a Dockerfile template for a custom sandbox image."""
    selected = _resolve_template(template)
    output_path = output or Path(selected.default_output)
    output_path = output_path.expanduser()

    if output_path.exists() and not force:
        error(f"{output_path} already exists. Use --force to overwrite it.")

    try:
        content = _read_template_file(selected)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
    except Exception as exc:
        error(str(exc))

    typer.echo(f"Dockerfile template '{selected.name}' written to {output_path}")
