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

from typer.testing import CliRunner

from agentkit.toolkit.cli.sandbox.cli import sandbox_app

runner = CliRunner()


def test_init_dockerfile_package_writes_default_template():
    with runner.isolated_filesystem():
        result = runner.invoke(
            sandbox_app,
            ["init-dockerfile", "--template", "package"],
        )

        assert result.exit_code == 0
        assert "Dockerfile template 'package' written to Dockerfile.install-package" in (
            result.output
        )
        content = open("Dockerfile.install-package", encoding="utf-8").read()
        assert "Base image + npm package installation" in content
        assert "agentkit sandbox deploy" in content
        assert (
            "FROM enterprise-public-cn-beijing.cr.volces.com/vefaas-public/code-cli:0.0.7"
            in content
        )
        assert "is-even@1.0.0" in content


def test_init_dockerfile_package_writes_custom_output_path():
    with runner.isolated_filesystem():
        result = runner.invoke(
            sandbox_app,
            ["init-dockerfile", "--template", "package", "-o", "./Dockerfile"],
        )

        assert result.exit_code == 0
        content = open("Dockerfile", encoding="utf-8").read()
        assert 'ENV PATH="/opt/nodejs/22/bin:${PATH}"' in content


def test_init_dockerfile_refuses_to_overwrite_without_force():
    with runner.isolated_filesystem():
        with open("Dockerfile", "w", encoding="utf-8") as file:
            file.write("FROM scratch\n")

        result = runner.invoke(
            sandbox_app,
            ["init-dockerfile", "--template", "package", "-o", "./Dockerfile"],
        )

        assert result.exit_code == 1
        assert "already exists. Use --force to overwrite it." in result.output
        assert open("Dockerfile", encoding="utf-8").read() == "FROM scratch\n"


def test_init_dockerfile_force_overwrites_existing_file():
    with runner.isolated_filesystem():
        with open("Dockerfile", "w", encoding="utf-8") as file:
            file.write("FROM scratch\n")

        result = runner.invoke(
            sandbox_app,
            [
                "init-dockerfile",
                "--template",
                "package",
                "-o",
                "./Dockerfile",
                "--force",
            ],
        )

        assert result.exit_code == 0
        assert "is-even@1.0.0" in open(
            "Dockerfile", encoding="utf-8"
        ).read()


def test_init_dockerfile_reserved_template_exits_with_clear_error():
    result = runner.invoke(
        sandbox_app,
        ["init-dockerfile", "--template", "skill"],
    )

    assert result.exit_code == 1
    assert "Template 'skill' is reserved but not implemented yet" in result.output
