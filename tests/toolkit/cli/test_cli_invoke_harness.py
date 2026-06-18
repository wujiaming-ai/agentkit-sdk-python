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

"""Tests for ``agentkit invoke harness`` (and the bare-message fallback)."""

import json

import pytest
from typer.testing import CliRunner

from agentkit.toolkit.cli.cli import app
from agentkit.toolkit.cli import cli_invoke

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolate_login_home(monkeypatch, tmp_path):
    """Keep harness tests from reading the developer's real ~/.agentkit login session.

    Points AGENTKIT_HOME at a clean per-test dir so by default no login session exists
    (the harness then uses the harness.json key). Tests that exercise the id_token path
    create a session explicitly via _setup_login_session()."""
    monkeypatch.setenv("AGENTKIT_HOME", str(tmp_path / "_agentkit_home"))


def _write_registry(directory, mapping):
    """Write the ``harness.json`` registry that ``deploy --harness`` produces."""
    (directory / "harness.json").write_text(json.dumps(mapping))


def _run_harness(args):
    return runner.invoke(app, ["invoke", "harness", *args])


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _patch_post(monkeypatch, captured, *, payload=None, status_code=200):
    payload = payload or {"harness_name": "first", "overwrite": False, "output": "ok"}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse(payload, status_code)

    monkeypatch.setattr("requests.post", fake_post)


# --- pure helper: HarnessOverrides shape ------------------------------------


def test_build_harness_overrides_matches_harness_overrides_model():
    overrides = cli_invoke.build_harness_overrides(
        system_prompt="be terse",
        model_name="m1",
        tools="web_search,web_fetch",
        skills="s1",
        runtime="codex",
    )
    # model_name (not model.name); tools/skills as comma-separated STRINGS.
    assert overrides == {
        "system_prompt": "be terse",
        "model_name": "m1",
        "tools": "web_search,web_fetch",
        "skills": "s1",
        "runtime": "codex",
    }


def test_build_harness_overrides_empty_when_unset():
    assert cli_invoke.build_harness_overrides(None, None, None, None, None) == {}


# --- fast-fail: unknown harness ---------------------------------------------


def test_unknown_harness_fails(tmp_path):
    _write_registry(tmp_path, {"other": {"url": "https://x", "key": "k"}})
    result = _run_harness(["first", "hi", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    assert "not found in registry" in result.output


def test_no_registry_fails(tmp_path):
    result = _run_harness(["first", "hi", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    assert "not found in registry" in result.output


# --- happy path: builds InvokeHarnessRequest and POSTs /harness/invoke ------


def test_harness_invoke_posts_correct_request(tmp_path, monkeypatch):
    _write_registry(
        tmp_path,
        {"first": {"url": "https://x", "key": "ak", "runtime_id": "r-1"}},
    )
    captured = {}
    _patch_post(monkeypatch, captured, payload={
        "harness_name": "first",
        "overwrite": True,
        "output": "PINEAPPLE",
    })

    result = _run_harness(
        [
            "first",
            "What should you reply?",
            "--directory",
            str(tmp_path),
            "--system-prompt",
            "Reply PINEAPPLE.",
            "--max-llm-calls",
            "7",
        ]
    )

    assert result.exit_code == 0, result.output
    assert "PINEAPPLE" in result.output
    # Endpoint + auth.
    assert captured["url"] == "https://x/harness/invoke"
    assert captured["headers"]["Authorization"] == "Bearer ak"
    # InvokeHarnessRequest shape.
    body = captured["json"]
    assert body["prompt"] == "What should you reply?"
    assert body["harness_name"] == "first"
    assert body["run_agent_request"]["user_id"] == "agentkit_user"
    assert body["run_agent_request"]["max_llm_calls"] == 7
    # Partial overrides only (model_fields_set semantics).
    assert body["harness"] == {"system_prompt": "Reply PINEAPPLE."}


def test_harness_invoke_no_overrides_omits_harness_key(tmp_path, monkeypatch):
    _write_registry(tmp_path, {"first": {"url": "https://x", "key": "ak"}})
    captured = {}
    _patch_post(monkeypatch, captured)

    result = _run_harness(["first", "hi", "--directory", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "harness" not in captured["json"]
    assert "max_llm_calls" not in captured["json"]["run_agent_request"]


def test_harness_error_field_is_surfaced(tmp_path, monkeypatch):
    _write_registry(tmp_path, {"first": {"url": "https://x", "key": "ak"}})
    captured = {}
    _patch_post(
        monkeypatch,
        captured,
        payload={
            "harness_name": "first",
            "overwrite": True,
            "output": "",
            "error": "Tool 'bogus' is not a supported built-in tool. Available: web_search",
        },
    )

    result = _run_harness(
        ["first", "hi", "--directory", str(tmp_path), "--tools", "bogus"]
    )
    assert result.exit_code == 1
    assert "Harness error" in result.output
    assert "not a supported built-in tool" in result.output


def test_harness_invoke_http_error_fails(tmp_path, monkeypatch):
    _write_registry(tmp_path, {"first": {"url": "https://x", "key": "ak"}})
    captured = {}
    _patch_post(monkeypatch, captured, payload={"detail": "boom"}, status_code=500)

    result = _run_harness(["first", "hi", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    assert "HTTP 500" in result.output


def test_apikey_overrides_registry_key(tmp_path, monkeypatch):
    _write_registry(tmp_path, {"first": {"url": "https://x", "key": "registrykey"}})
    captured = {}
    _patch_post(monkeypatch, captured)

    result = _run_harness(
        ["first", "hi", "--directory", str(tmp_path), "--apikey", "jwt-token"]
    )
    assert result.exit_code == 0, result.output
    assert captured["headers"]["Authorization"] == "Bearer jwt-token"


# --- bare-message fallback still routes to `run` ----------------------------


def test_bare_message_falls_back_to_run(monkeypatch):
    captured = {}

    class _FakeResult:
        success = True
        error = None
        error_code = None
        is_streaming = False
        response = {"text": "ok"}

    class _FakeExecutor:
        def __init__(self, *args, **kwargs):
            pass

        def execute(self, **kwargs):
            captured.update(kwargs)
            return _FakeResult()

    monkeypatch.setattr(
        "agentkit.toolkit.executors.InvokeExecutor", _FakeExecutor, raising=True
    )

    class _FakeCommon:
        agent_type = ""

    class _FakeConfig:
        def get_common_config(self):
            return _FakeCommon()

    monkeypatch.setattr(cli_invoke, "get_config", lambda config_path: _FakeConfig())

    result = runner.invoke(app, ["invoke", "hello"])

    assert result.exit_code == 0, result.output
    # Non-direct `run` path uses the yaml config_file and passes no config_dict.
    assert captured.get("config_dict") is None
    assert captured["config_file"] is not None
    assert captured["payload"] == {"prompt": "hello"}


# --- data-plane JWT: harness invoke uses the `agentkit login` id_token --------


def _make_id_token(exp_delta=3600, sub="u-alice"):
    import datetime

    import jwt
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    return jwt.encode({"sub": sub, "exp": int(now + exp_delta)}, "test-signing-secret-0123456789abcdef", algorithm="HS256")


def _setup_login_session(monkeypatch, tmp_path, id_token, refresh_token="rt-1"):
    """Create an active `agentkit login` session carrying ``id_token`` under a temp home."""
    monkeypatch.setenv("AGENTKIT_HOME", str(tmp_path / "home"))
    from agentkit.auth.profile import AuthProfile, save_profile, set_active_profile
    from agentkit.auth.session import AuthSession

    prof = AuthProfile(
        name="default", issuer="https://userpool-x.example.com",
        client_id="c1", role_trn="trn:iam::1:role/r",
    )
    save_profile(prof)
    set_active_profile("default")
    AuthSession(prof, refresh_token=refresh_token, id_token=id_token).save()
    return prof


def test_harness_invoke_uses_login_id_token_as_bearer(monkeypatch, tmp_path):
    tok = _make_id_token()
    _setup_login_session(monkeypatch, tmp_path, tok)
    _write_registry(tmp_path, {"first": {"url": "https://h.example.com"}})  # no static key
    captured = {}
    _patch_post(monkeypatch, captured)
    result = _run_harness(["first", "hi", "--directory", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured["headers"].get("Authorization") == f"Bearer {tok}"


def test_harness_invoke_apikey_overrides_login_id_token(monkeypatch, tmp_path):
    _setup_login_session(monkeypatch, tmp_path, _make_id_token())
    _write_registry(tmp_path, {"first": {"url": "https://h.example.com"}})
    captured = {}
    _patch_post(monkeypatch, captured)
    result = _run_harness(["first", "hi", "--directory", str(tmp_path), "--apikey", "explicit-key"])
    assert result.exit_code == 0, result.output
    assert captured["headers"].get("Authorization") == "Bearer explicit-key"


def test_harness_invoke_refreshes_on_401_and_retries_once(monkeypatch, tmp_path):
    tok1 = _make_id_token(sub="u1")
    tok2 = _make_id_token(sub="u1-refreshed")
    _setup_login_session(monkeypatch, tmp_path, tok1)
    _write_registry(tmp_path, {"first": {"url": "https://h.example.com"}})

    import agentkit.auth.session as sess_mod
    monkeypatch.setattr("agentkit.auth.ssl_trust.harden_default_ssl_context", lambda *a, **k: None)
    monkeypatch.setattr(
        sess_mod.OAuthClient, "refresh",
        lambda self, rt: {"id_token": tok2, "refresh_token": "rt-2"},
    )

    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(headers.get("Authorization"))
        return _FakeResponse({"output": "ok"}, 401 if len(calls) == 1 else 200)

    monkeypatch.setattr("requests.post", fake_post)
    result = _run_harness(["first", "hi", "--directory", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert calls == [f"Bearer {tok1}", f"Bearer {tok2}"]  # one refresh + one retry


def test_harness_invoke_relogin_error_when_refresh_fails_on_expired(monkeypatch, tmp_path):
    _setup_login_session(monkeypatch, tmp_path, _make_id_token(exp_delta=-10))  # already expired
    _write_registry(tmp_path, {"first": {"url": "https://h.example.com"}})

    import agentkit.auth.session as sess_mod
    from agentkit.auth.errors import AuthError
    monkeypatch.setattr("agentkit.auth.ssl_trust.harden_default_ssl_context", lambda *a, **k: None)

    def boom(self, rt):
        raise AuthError("token endpoint rejected the request")

    monkeypatch.setattr(sess_mod.OAuthClient, "refresh", boom)
    result = _run_harness(["first", "hi", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    assert "login" in result.output.lower()


def test_harness_invoke_keyauth_uses_key_even_when_logged_in(monkeypatch, tmp_path):
    # P1 guard: a key_auth harness ({"key": ...}) must use the static key even when logged
    # in — a key_auth authorizer would reject an OIDC JWT.
    _setup_login_session(monkeypatch, tmp_path, _make_id_token())
    _write_registry(tmp_path, {"first": {"url": "https://h.example.com", "key": "ak", "runtime_id": "r-1"}})
    captured = {}
    _patch_post(monkeypatch, captured)
    result = _run_harness(["first", "hi", "--directory", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured["headers"].get("Authorization") == "Bearer ak"


def test_harness_invoke_custom_jwt_entry_uses_login_id_token(monkeypatch, tmp_path):
    tok = _make_id_token()
    _setup_login_session(monkeypatch, tmp_path, tok)
    _write_registry(tmp_path, {"first": {
        "url": "https://h.example.com", "runtime_id": "r-1", "auth_type": "custom_jwt",
        "discovery_url": "https://up/.well-known/openid-configuration", "allowed_ids": ["c1"],
    }})
    captured = {}
    _patch_post(monkeypatch, captured)
    result = _run_harness(["first", "hi", "--directory", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured["headers"].get("Authorization") == f"Bearer {tok}"


def test_harness_invoke_persistent_401_stops_after_one_retry(monkeypatch, tmp_path):
    tok1 = _make_id_token(sub="u1")
    tok2 = _make_id_token(sub="u1-refreshed")
    _setup_login_session(monkeypatch, tmp_path, tok1)
    _write_registry(tmp_path, {"first": {"url": "https://h.example.com"}})

    import agentkit.auth.session as sess_mod
    monkeypatch.setattr("agentkit.auth.ssl_trust.harden_default_ssl_context", lambda *a, **k: None)
    monkeypatch.setattr(sess_mod.OAuthClient, "refresh", lambda self, rt: {"id_token": tok2, "refresh_token": "rt-2"})

    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(headers.get("Authorization"))
        return _FakeResponse({"detail": "denied"}, 401)

    monkeypatch.setattr("requests.post", fake_post)
    result = _run_harness(["first", "hi", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    assert len(calls) == 2  # original + exactly one refresh-retry, no third
    assert "HTTP 401" in result.output
