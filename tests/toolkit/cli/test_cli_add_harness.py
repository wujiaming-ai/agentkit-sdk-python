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
