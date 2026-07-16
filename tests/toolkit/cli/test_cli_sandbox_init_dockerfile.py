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


def test_init_dockerfile_package_writes_default_template(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
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


def test_init_dockerfile_package_writes_custom_output_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        sandbox_app,
        ["init-dockerfile", "--template", "package", "-o", "./Dockerfile"],
    )

    assert result.exit_code == 0
    content = open("Dockerfile", encoding="utf-8").read()
    assert 'ENV PATH="/opt/nodejs/22/bin:${PATH}"' in content


def test_init_dockerfile_refuses_to_overwrite_without_force(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with open("Dockerfile", "w", encoding="utf-8") as file:
        file.write("FROM scratch\n")

    result = runner.invoke(
        sandbox_app,
        ["init-dockerfile", "--template", "package", "-o", "./Dockerfile"],
    )

    assert result.exit_code == 1
    assert "already exists. Use --force to overwrite it." in result.output
    assert open("Dockerfile", encoding="utf-8").read() == "FROM scratch\n"


def test_init_dockerfile_force_overwrites_existing_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
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
    assert "is-even@1.0.0" in open("Dockerfile", encoding="utf-8").read()


def test_init_dockerfile_skill_writes_default_template(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        sandbox_app,
        ["init-dockerfile", "--template", "skill"],
    )

    assert result.exit_code == 0
    assert "Dockerfile template 'skill' written to Dockerfile.install-skills" in (
        result.output
    )
    content = open("Dockerfile.install-skills", encoding="utf-8").read()
    assert "Base image + local Codex skills" in content
    assert "agentkit skills init <skill-name> --path ./skills" in content
    assert 'ENV CODEX_HOME="/home/gem/.codex"' in content
    assert "COPY skills/ /home/gem/.codex/skills/" in content


def test_init_dockerfile_web_server_writes_default_template(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        sandbox_app,
        ["init-dockerfile", "--template", "web-server"],
    )

    assert result.exit_code == 0
    assert "Dockerfile template 'web-server' written to Dockerfile.web-server" in (
        result.output
    )
    content = open("Dockerfile.web-server", encoding="utf-8").read()
    assert "Base image + nginx route + local server" in content
    assert 'ENV PUBLIC_PORT="8080"' in content
    assert 'ENV APP_PORT="8000"' in content
    assert "location /app/" in content
    assert "proxy_pass http://127.0.0.1:8000/" in content
    assert "nginx -g 'daemon off;'" in content


def test_init_dockerfile_unknown_template_exits_with_clear_error():
    result = runner.invoke(
        sandbox_app,
        ["init-dockerfile", "--template", "missing"],
    )

    assert result.exit_code == 1
    assert (
        "Unknown Dockerfile template 'missing'. Valid templates: package, skill, web-server"
        in result.output
    )
