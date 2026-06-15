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

"""Tests for ``agentkit list harness``."""

import json

from typer.testing import CliRunner

from agentkit.toolkit.cli.cli import app
from agentkit.toolkit.cli import cli_list
from agentkit.sdk.runtime import types as rt
from agentkit.toolkit.harness.deploy import HARNESS_TAG_KEY, HARNESS_TAG_VALUE

runner = CliRunner()


def _runtime(runtime_id, name, *, tags):
    return rt.AgentKitRuntimesForListRuntimes.model_validate(
        {
            "RuntimeId": runtime_id,
            "Name": name,
            "Status": "Ready",
            "Tags": [{"Key": k, "Value": v} for k, v in tags],
        }
    )


_HARNESS_TAG = (HARNESS_TAG_KEY, HARNESS_TAG_VALUE)


# --- pure helper ------------------------------------------------------------


def test_is_harness_runtime_matches_deploy_tag():
    rt_harness = _runtime("rt-1", "demo", tags=[_HARNESS_TAG])
    rt_plain = _runtime("rt-2", "other", tags=[("team", "x")])
    rt_untagged = _runtime("rt-3", "bare", tags=[])

    assert cli_list._is_harness_runtime(rt_harness) is True
    assert cli_list._is_harness_runtime(rt_plain) is False
    assert cli_list._is_harness_runtime(rt_untagged) is False


# --- CLI: filtering ---------------------------------------------------------


def _patch_client(monkeypatch, runtimes):
    """Stub AgentkitRuntimeClient so list_runtimes returns ``runtimes`` once."""

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def list_runtimes(self, request):
            return rt.ListRuntimesResponse.model_validate(
                {
                    "AgentKitRuntimes": [
                        r.model_dump(by_alias=True, exclude_none=True)
                        for r in runtimes
                    ],
                    "NextToken": "",
                }
            )

    monkeypatch.setattr(cli_list, "AgentkitRuntimeClient", _FakeClient, raising=True)


def test_list_harness_filters_to_tagged_runtimes(monkeypatch):
    _patch_client(
        monkeypatch,
        [
            _runtime("rt-harness", "my-harness", tags=[_HARNESS_TAG]),
            _runtime("rt-plain", "plain", tags=[("team", "x")]),
        ],
    )

    result = runner.invoke(app, ["list", "harness"])

    assert result.exit_code == 0, result.output
    assert "rt-harness" in result.output
    assert "rt-plain" not in result.output


def test_list_harness_quiet_prints_only_ids(monkeypatch):
    _patch_client(
        monkeypatch,
        [
            _runtime("rt-harness", "my-harness", tags=[_HARNESS_TAG]),
            _runtime("rt-plain", "plain", tags=[]),
        ],
    )

    result = runner.invoke(app, ["list", "harness", "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "rt-harness"


def test_list_harness_json_output(monkeypatch):
    _patch_client(
        monkeypatch,
        [_runtime("rt-harness", "my-harness", tags=[_HARNESS_TAG])],
    )

    result = runner.invoke(app, ["list", "harness", "--output", "json"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [r["RuntimeId"] for r in data] == ["rt-harness"]


def test_list_harness_follows_pagination(monkeypatch):
    """A harness runtime on a later page must still be found (NextToken paging)."""

    pages = {
        "": ([_runtime("rt-plain", "plain", tags=[])], "tok-2"),
        "tok-2": ([_runtime("rt-harness", "my-harness", tags=[_HARNESS_TAG])], ""),
    }

    class _PagingClient:
        def __init__(self, *args, **kwargs):
            pass

        def list_runtimes(self, request):
            items, next_token = pages[request.next_token or ""]
            return rt.ListRuntimesResponse.model_validate(
                {
                    "AgentKitRuntimes": [
                        r.model_dump(by_alias=True, exclude_none=True) for r in items
                    ],
                    "NextToken": next_token,
                }
            )

    monkeypatch.setattr(cli_list, "AgentkitRuntimeClient", _PagingClient, raising=True)

    result = runner.invoke(app, ["list", "harness", "--quiet"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "rt-harness"
