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

"""Tests for ``agentkit add harness``."""

import json

from typer.testing import CliRunner

from agentkit.toolkit.cli.cli import app
from agentkit.toolkit.harness.env_mapping import to_runtime_env

runner = CliRunner()


def _run(args):
    return runner.invoke(app, ["add", *args])


def test_creates_harness_json_with_layered_structure(tmp_path):
    result = _run(
        [
            "harness",
            "--name",
            "my-harness",
            "--model-name",
            "doubao-seed-1-6-250615",
            "--tools",
            "web_search, web_fetch",
            "--system-prompt",
            "You are helpful.",
            "--runtime",
            "codex",
            "--knowledgebase-type",
            "viking",
            "--knowledgebase-project",
            "myproj",
            "--knowledgebase-region",
            "cn-beijing",
            "--directory",
            str(tmp_path),
        ]
    )

    assert result.exit_code == 0, result.output
    data = json.loads((tmp_path / "my-harness.harness.json").read_text())
    assert data == {
        "harness_name": "my-harness",
        "runtime": "codex",
        "short_term_memory": {"type": "local"},
        "model": {"name": "doubao-seed-1-6-250615"},
        "system_prompt": "You are helpful.",
        "tools": ["web_search", "web_fetch"],
        "knowledgebase": {
            "type": "viking",
            "project": "myproj",
            "region": "cn-beijing",
        },
    }


def test_rerun_merges_into_existing_file(tmp_path):
    assert _run(["harness", "--name", "h", "--directory", str(tmp_path)]).exit_code == 0
    result = _run(
        [
            "harness",
            "--name",
            "h",
            "--skills",
            "code_review",
            "--discovery-url",
            "https://x/.well-known/openid-configuration",
            "--allowed-id",
            "cid1,cid2",
            "--directory",
            str(tmp_path),
        ]
    )

    assert result.exit_code == 0, result.output
    data = json.loads((tmp_path / "h.harness.json").read_text())
    # Default scaffold fields survive the merge ...
    assert data["harness_name"] == "h"
    assert data["short_term_memory"] == {"type": "local"}
    # ... and the new options are written.
    assert data["skills"] == ["code_review"]
    assert data["auth"] == {
        "discovery_url": "https://x/.well-known/openid-configuration",
        "allowed_ids": ["cid1", "cid2"],
    }


def test_blank_component_type_is_pruned(tmp_path):
    # A connection param without a backend `type` leaves no orphan section.
    result = _run(
        [
            "harness",
            "--name",
            "h",
            "--long-term-memory-project",
            "p",
            "--directory",
            str(tmp_path),
        ]
    )

    assert result.exit_code == 0, result.output
    data = json.loads((tmp_path / "h.harness.json").read_text())
    assert "long_term_memory" not in data


def test_invalid_runtime_fails(tmp_path):
    result = _run(
        ["harness", "--name", "h", "--runtime", "foo", "--directory", str(tmp_path)]
    )
    assert result.exit_code == 1
    assert not (tmp_path / "h.harness.json").exists()


def test_invalid_name_fails(tmp_path):
    result = _run(["harness", "--name", "bad name", "--directory", str(tmp_path)])
    assert result.exit_code == 1


def test_registry_flags_write_agentkit_a2a_section(tmp_path):
    result = _run(
        [
            "harness",
            "--name",
            "h",
            "--registry",
            "agentkit://a2a-registry?space_id=space-test&top_k=2",
            "--registry-top-k",
            "7",
            "--registry-region",
            "cn-beijing",
            "--structured-tool-calls",
            "--include-tools-every-turn",
            "--directory",
            str(tmp_path),
        ]
    )

    assert result.exit_code == 0, result.output
    data = json.loads((tmp_path / "h.harness.json").read_text())
    assert data["registry"] == {
        "type": "agentkit_a2a",
        "space_id": "space-test",
        "top_k": 7,
        "region": "cn-beijing",
    }
    assert data["structured_tool_calls"] is True
    assert data["include_tools_every_turn"] is True


def test_registry_config_maps_to_runtime_env():
    env = to_runtime_env(
        {
            "registry": {
                "type": "agentkit_a2a",
                "space_id": "space-test",
                "top_k": 7,
                "region": "cn-beijing",
            },
            "structured_tool_calls": True,
            "include_tools_every_turn": True,
        }
    )

    assert env["REGISTRY_TYPE"] == "agentkit_a2a"
    assert env["REGISTRY_SPACE_ID"] == "space-test"
    assert env["REGISTRY_TOP_K"] == "7"
    assert env["REGISTRY_REGION"] == "cn-beijing"
    assert env["STRUCTURED_TOOL_CALLS"] == "true"
    assert env["INCLUDE_TOOLS_EVERY_TURN"] == "true"


def test_add_harness_register_self_resolves_runtime_and_space(
    tmp_path, monkeypatch
):
    (tmp_path / "harness.json").write_text(
        json.dumps({"h": {"url": "https://x", "runtime_id": "r-test"}})
    )
    captured = {}

    def fake_create_a2a_agent(**kwargs):
        captured.update(kwargs)
        return {
            "outcome": "success",
            "agent_id": "a-test",
            "tags": [],
            "diagnostics": {"request_id": "req-1"},
        }

    monkeypatch.setattr(
        "agentkit.toolkit.cli.cli_add._create_a2a_agent",
        fake_create_a2a_agent,
    )

    result = _run(
        [
            "harness",
            "--name",
            "h",
            "--registry-space-id",
            "space-test",
            "--register-self",
            "--register-tag",
            "env=test",
            "--register-endpoint",
            "https://agentkit.cn-beijing.volcengineapi.com/",
            "--directory",
            str(tmp_path),
        ]
    )

    assert result.exit_code == 0, result.output
    assert "A2A agent registered" in result.output
    assert "Harness URL:" in result.output
    assert captured["a2a_space_id"] == "space-test"
    assert captured["runtime_id"] == "r-test"
    assert captured["network_type"] == "public"
    assert captured["tags"] == [{"Key": "env", "Value": "test"}]
    assert captured["endpoint"] == "https://agentkit.cn-beijing.volcengineapi.com/"
    assert captured["region"] == "cn-beijing"


def test_add_harness_register_self_requires_harness_json_entry(tmp_path):
    result = _run(
        [
            "harness",
            "--name",
            "h",
            "--registry-space-id",
            "space-test",
            "--register-self",
            "--directory",
            str(tmp_path),
        ]
    )

    assert result.exit_code == 1
    assert "does not contain an entry for 'h'" in result.output


def test_add_harness_register_self_requires_url_and_runtime_id(tmp_path):
    (tmp_path / "harness.json").write_text(json.dumps({"h": {"url": "https://x"}}))

    result = _run(
        [
            "harness",
            "--name",
            "h",
            "--registry-space-id",
            "space-test",
            "--register-self",
            "--directory",
            str(tmp_path),
        ]
    )

    assert result.exit_code == 1
    assert "missing required field(s): runtime_id" in result.output


def test_add_harness_register_self_rejects_invalid_network_type(tmp_path):
    (tmp_path / "harness.json").write_text(
        json.dumps({"h": {"url": "https://x", "runtime_id": "r-test"}})
    )
    result = _run(
        [
            "harness",
            "--name",
            "h",
            "--register-space-id",
            "space-test",
            "--register-self",
            "--register-network-type",
            "internet",
            "--directory",
            str(tmp_path),
        ]
    )

    assert result.exit_code == 1
    assert "--register-network-type must be one of" in result.output
