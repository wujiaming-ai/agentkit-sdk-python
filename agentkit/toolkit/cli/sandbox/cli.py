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

"""AgentKit CLI - Sandbox commands."""

from __future__ import annotations

import typer

from agentkit.toolkit.cli.sandbox.sandbox_create import create_command
from agentkit.toolkit.cli.sandbox.sandbox_exec import exec_command
from agentkit.toolkit.cli.sandbox.sandbox_get import get_command
from agentkit.toolkit.cli.sandbox.sandbox_terminal import terminal_command

sandbox_app = typer.Typer(
    name="sandbox",
    help="Manage AgentKit sandbox sessions",
    add_completion=False,
)

sandbox_app.command("create")(create_command)
sandbox_app.command("get")(get_command)
sandbox_app.command("exec")(exec_command)
sandbox_app.command("terminal")(terminal_command)
