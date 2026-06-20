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

"""Tests for ``agentkit list sessions --harness``."""

import base64
import json

import requests
from typer.testing import CliRunner

from agentkit.toolkit.cli.cli import app

runner = CliRunner()


def _jwt(sub: str) -> str:
    """Build an unsigned JWT whose payload carries the given `sub` claim."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    payload = (
        base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode())
        .rstrip(b"=")
        .decode()
    )
    return f"{header}.{payload}.sig"


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else []
        self.text = text

    def json(self):
        return self._payload


def _patch_registry(monkeypatch, entries):
    monkeypatch.setattr(
        "agentkit.toolkit.harness.load_harness_registry",
        lambda directory: entries,
        raising=True,
    )


def _patch_get(monkeypatch, resp):
    """Patch requests.get; capture the URL it was called with."""
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return resp

    monkeypatch.setattr(requests, "get", fake_get)
    return captured


_SESSIONS = [
    {"id": "sess-1", "userId": "u-1", "events": [1, 2], "lastUpdateTime": 0},
    {"id": "sess-2", "userId": "u-1", "events": [], "lastUpdateTime": 0},
]


def test_list_sessions_explicit_user_id(monkeypatch):
    _patch_registry(monkeypatch, {"my-harness": {"url": "https://x", "key": "sk-1"}})
    captured = _patch_get(monkeypatch, _Resp(200, _SESSIONS))

    result = runner.invoke(
        app, ["list", "sessions", "--harness", "my-harness", "--user-id", "u-1"]
    )

    assert result.exit_code == 0, result.output
    assert captured["url"].endswith("/apps/harness/users/u-1/sessions")
    assert "sess-1" in result.output


def test_list_sessions_user_id_from_jwt_sub(monkeypatch):
    # custom_jwt harness (no stored key); user_id derives from the -ak JWT sub.
    _patch_registry(
        monkeypatch, {"my-harness": {"url": "https://x", "auth_type": "custom_jwt"}}
    )
    captured = _patch_get(monkeypatch, _Resp(200, []))

    result = runner.invoke(
        app,
        ["list", "sessions", "--harness", "my-harness", "-ak", _jwt("user-xyz")],
    )

    assert result.exit_code == 0, result.output
    assert "/apps/harness/users/user-xyz/sessions" in captured["url"]


def test_list_sessions_requires_user_id_for_key_auth(monkeypatch):
    # key_auth harness: the api key is not a JWT, so user_id can't be derived.
    _patch_registry(monkeypatch, {"my-harness": {"url": "https://x", "key": "sk-1"}})

    called = {"get": False}

    def fake_get(*a, **k):
        called["get"] = True
        return _Resp(200, [])

    monkeypatch.setattr(requests, "get", fake_get)

    result = runner.invoke(app, ["list", "sessions", "--harness", "my-harness"])

    assert result.exit_code == 1
    assert "user_id" in result.output
    assert called["get"] is False  # fast-fails before any network call


def test_list_sessions_unknown_harness(monkeypatch):
    _patch_registry(monkeypatch, {})

    result = runner.invoke(
        app, ["list", "sessions", "--harness", "nope", "--user-id", "u-1"]
    )

    assert result.exit_code == 1
    assert "not found in registry" in result.output


def test_list_sessions_quiet_prints_only_ids(monkeypatch):
    _patch_registry(monkeypatch, {"my-harness": {"url": "https://x", "key": "sk-1"}})
    _patch_get(monkeypatch, _Resp(200, _SESSIONS))

    result = runner.invoke(
        app,
        ["list", "sessions", "--harness", "my-harness", "--user-id", "u-1", "--quiet"],
    )

    assert result.exit_code == 0, result.output
    assert result.output.split() == ["sess-1", "sess-2"]


def test_list_sessions_json_output(monkeypatch):
    _patch_registry(monkeypatch, {"my-harness": {"url": "https://x", "key": "sk-1"}})
    _patch_get(monkeypatch, _Resp(200, _SESSIONS))

    result = runner.invoke(
        app,
        [
            "list",
            "sessions",
            "--harness",
            "my-harness",
            "--user-id",
            "u-1",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [s["id"] for s in data] == ["sess-1", "sess-2"]
