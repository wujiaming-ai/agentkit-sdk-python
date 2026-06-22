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


def _run_invoke(args):
    """Run the harness subcommand pinned to the ``invoke`` transport.

    ``--protocol`` now defaults to ``run_sse``; these tests exercise the
    ``/harness/invoke`` path, so they request it explicitly.
    """
    return _run_harness([*args, "--protocol", "invoke"])


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
        registry_space_id="space-1",
        registry_top_k=5,
        registry_endpoint="https://open.volcengineapi.com/",
        registry_region="cn-beijing",
    )
    # model_name (not model.name); tools/skills as comma-separated STRINGS.
    assert overrides == {
        "system_prompt": "be terse",
        "model_name": "m1",
        "tools": "web_search,web_fetch",
        "skills": "s1",
        "runtime": "codex",
        "registry_space_id": "space-1",
        "registry_top_k": 5,
        "registry_endpoint": "https://open.volcengineapi.com/",
        "registry_region": "cn-beijing",
    }


def test_build_harness_overrides_empty_when_unset():
    assert cli_invoke.build_harness_overrides(None, None, None, None, None) == {}


# --- fast-fail: unknown harness ---------------------------------------------


def test_unknown_harness_fails(tmp_path):
    _write_registry(tmp_path, {"other": {"url": "https://x", "key": "k"}})
    result = _run_invoke(["first", "hi", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    assert "not found in registry" in result.output


def test_no_registry_fails(tmp_path):
    result = _run_invoke(["first", "hi", "--directory", str(tmp_path)])
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

    result = _run_invoke(
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
    # No --session-id → random s-<id>, consistent with the run_sse path.
    assert body["run_agent_request"]["session_id"].startswith("s-")
    assert body["run_agent_request"]["max_llm_calls"] == 7
    # Partial overrides only (model_fields_set semantics).
    assert body["harness"] == {"system_prompt": "Reply PINEAPPLE."}


def test_harness_invoke_posts_registry_overrides(tmp_path, monkeypatch):
    _write_registry(
        tmp_path,
        {"first": {"url": "https://x", "key": "ak", "runtime_id": "r-1"}},
    )
    captured = {}
    _patch_post(monkeypatch, captured)

    result = _run_harness(
        [
            "first",
            "Find a finance expert.",
            "--directory",
            str(tmp_path),
            "--registry-space-id",
            "space-override",
            "--registry-top-k",
            "8",
            "--registry-endpoint",
            "https://open.volcengineapi.com/",
            "--registry-region",
            "cn-beijing",
        ]
    )

    assert result.exit_code == 0, result.output
    assert captured["json"]["harness"] == {
        "registry_space_id": "space-override",
        "registry_top_k": 8,
        "registry_endpoint": "https://open.volcengineapi.com/",
        "registry_region": "cn-beijing",
    }


def test_harness_invoke_registry_uri_override(tmp_path, monkeypatch):
    _write_registry(
        tmp_path,
        {"first": {"url": "https://x", "key": "ak", "runtime_id": "r-1"}},
    )
    captured = {}
    _patch_post(monkeypatch, captured)

    result = _run_invoke(
        [
            "first",
            "Find a finance expert.",
            "--directory",
            str(tmp_path),
            "--registry",
            "agentkit://a2a-registry?space_id=space-uri&top_k=4&region=cn-beijing",
        ]
    )

    assert result.exit_code == 0, result.output
    assert captured["json"]["harness"] == {
        "registry_space_id": "space-uri",
        "registry_top_k": 4,
        "registry_region": "cn-beijing",
    }


def test_harness_invoke_registry_space_name_resolves_to_space_id(tmp_path, monkeypatch):
    _write_registry(
        tmp_path,
        {"first": {"url": "https://x", "key": "ak", "runtime_id": "r-1"}},
    )
    captured = {}
    resolved = {}
    _patch_post(monkeypatch, captured)

    def fake_resolve_space_name(space_name, *, endpoint, region):
        resolved.update({"space_name": space_name, "endpoint": endpoint, "region": region})
        return "space-from-name"

    monkeypatch.setattr(
        "agentkit.toolkit.cli.cli_add._resolve_a2a_space_id_by_name",
        fake_resolve_space_name,
    )

    result = _run_invoke(
        [
            "first",
            "Find a finance expert.",
            "--directory",
            str(tmp_path),
            "--registry-space-name",
            "space-name",
            "--registry-endpoint",
            "https://open.volcengineapi.com/",
            "--registry-region",
            "cn-beijing",
        ]
    )

    assert result.exit_code == 0, result.output
    assert captured["json"]["harness"] == {
        "registry_space_id": "space-from-name",
        "registry_endpoint": "https://open.volcengineapi.com/",
        "registry_region": "cn-beijing",
    }
    assert resolved == {
        "space_name": "space-name",
        "endpoint": "https://open.volcengineapi.com/",
        "region": "cn-beijing",
    }


def test_harness_invoke_registry_uri_space_name_resolves_to_space_id(tmp_path, monkeypatch):
    _write_registry(
        tmp_path,
        {"first": {"url": "https://x", "key": "ak", "runtime_id": "r-1"}},
    )
    captured = {}
    resolved = {}
    _patch_post(monkeypatch, captured)

    def fake_resolve_space_name(space_name, *, endpoint, region):
        resolved.update({"space_name": space_name, "endpoint": endpoint, "region": region})
        return "space-from-uri-name"

    monkeypatch.setattr(
        "agentkit.toolkit.cli.cli_add._resolve_a2a_space_id_by_name",
        fake_resolve_space_name,
    )

    result = _run_invoke(
        [
            "first",
            "Find a finance expert.",
            "--directory",
            str(tmp_path),
            "--registry",
            "agentkit://a2a-registry?space_name=space-name&top_k=4&endpoint=https%3A%2F%2Fopen.volcengineapi.com%2F&region=cn-beijing",
        ]
    )

    assert result.exit_code == 0, result.output
    assert captured["json"]["harness"] == {
        "registry_space_id": "space-from-uri-name",
        "registry_top_k": 4,
        "registry_endpoint": "https://open.volcengineapi.com/",
        "registry_region": "cn-beijing",
    }
    assert resolved == {
        "space_name": "space-name",
        "endpoint": "https://open.volcengineapi.com/",
        "region": "cn-beijing",
    }


def test_harness_invoke_registry_uri_rejects_registry_space_name_alias(tmp_path):
    _write_registry(
        tmp_path,
        {"first": {"url": "https://x", "key": "ak", "runtime_id": "r-1"}},
    )

    result = _run_invoke(
        [
            "first",
            "Find a finance expert.",
            "--directory",
            str(tmp_path),
            "--registry",
            "agentkit://a2a-registry?registry_space_name=space-name",
        ]
    )

    assert result.exit_code == 1
    assert "Unsupported registry query param(s): registry_space_name" in result.output


def test_harness_invoke_registry_http_url_override(tmp_path, monkeypatch):
    _write_registry(
        tmp_path,
        {"first": {"url": "https://x", "key": "ak", "runtime_id": "r-1"}},
    )
    captured = {}
    _patch_post(monkeypatch, captured)
    discovery_url = (
        "https://open.volcengineapi.com/?Action=Discover&space_id=space-url"
    )

    result = _run_invoke(
        [
            "first",
            "Find a finance expert.",
            "--directory",
            str(tmp_path),
            "--registry",
            discovery_url,
        ]
    )

    assert result.exit_code == 0, result.output
    assert captured["json"]["harness"] == {
        "registry_endpoint": discovery_url,
        "registry_space_id": "space-url",
    }


def test_harness_invoke_no_overrides_omits_harness_key(tmp_path, monkeypatch):
    _write_registry(tmp_path, {"first": {"url": "https://x", "key": "ak"}})
    captured = {}
    _patch_post(monkeypatch, captured)

    result = _run_invoke(["first", "hi", "--directory", str(tmp_path)])

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

    result = _run_invoke(
        ["first", "hi", "--directory", str(tmp_path), "--tools", "bogus"]
    )
    assert result.exit_code == 1
    assert "Harness error" in result.output
    assert "not a supported built-in tool" in result.output


def test_harness_invoke_http_error_fails(tmp_path, monkeypatch):
    _write_registry(tmp_path, {"first": {"url": "https://x", "key": "ak"}})
    captured = {}
    _patch_post(monkeypatch, captured, payload={"detail": "boom"}, status_code=500)

    result = _run_invoke(["first", "hi", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    assert "HTTP 500" in result.output


def test_apikey_overrides_registry_key(tmp_path, monkeypatch):
    _write_registry(tmp_path, {"first": {"url": "https://x", "key": "registrykey"}})
    captured = {}
    _patch_post(monkeypatch, captured)

    result = _run_invoke(
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
    result = _run_invoke(["first", "hi", "--directory", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert captured["headers"].get("Authorization") == f"Bearer {tok}"


def test_harness_invoke_apikey_overrides_login_id_token(monkeypatch, tmp_path):
    _setup_login_session(monkeypatch, tmp_path, _make_id_token())
    _write_registry(tmp_path, {"first": {"url": "https://h.example.com"}})
    captured = {}
    _patch_post(monkeypatch, captured)
    result = _run_invoke(["first", "hi", "--directory", str(tmp_path), "--apikey", "explicit-key"])
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
    result = _run_invoke(["first", "hi", "--directory", str(tmp_path)])
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
    result = _run_invoke(["first", "hi", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    assert "login" in result.output.lower()


def test_harness_invoke_keyauth_uses_key_even_when_logged_in(monkeypatch, tmp_path):
    # P1 guard: a key_auth harness ({"key": ...}) must use the static key even when logged
    # in — a key_auth authorizer would reject an OIDC JWT.
    _setup_login_session(monkeypatch, tmp_path, _make_id_token())
    _write_registry(tmp_path, {"first": {"url": "https://h.example.com", "key": "ak", "runtime_id": "r-1"}})
    captured = {}
    _patch_post(monkeypatch, captured)
    result = _run_invoke(["first", "hi", "--directory", str(tmp_path)])
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
    result = _run_invoke(["first", "hi", "--directory", str(tmp_path)])
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
    result = _run_invoke(["first", "hi", "--directory", str(tmp_path)])
    assert result.exit_code == 1
    assert len(calls) == 2  # original + exactly one refresh-retry, no third
    assert "HTTP 401" in result.output


# --- run_sse protocol --------------------------------------------------------


def test_harness_run_sse_streams_answer(tmp_path, monkeypatch):
    _write_registry(tmp_path, {"first": {"url": "https://x", "key": "ak"}})
    calls = []
    sse_lines = [
        'data: {"content":{"parts":[{"text":"KI"}],"role":"model"},"partial":true}',
        'data: {"content":{"parts":[{"text":"WI"}],"role":"model"},"partial":true}',
        # final aggregate repeats everything (incl. a thought part); must be skipped
        'data: {"content":{"parts":[{"text":"reasoning","thought":true},'
        '{"text":"KIWI"}],"role":"model"},"partial":false}',
    ]

    class _SSEResp:
        status_code = 200
        text = ""

        def iter_lines(self, decode_unicode=False):
            return iter(sse_lines)

    def fake_post(url, json=None, headers=None, timeout=None, stream=False):
        calls.append({"url": url, "json": json})
        return _SSEResp() if url.endswith("/run_sse") else _FakeResponse({}, 200)

    monkeypatch.setattr("requests.post", fake_post)

    result = _run_harness(
        [
            "first",
            "hello",
            "--directory",
            str(tmp_path),
            "--protocol",
            "run_sse",
            "--session-id",
            "s-1",
        ]
    )
    assert result.exit_code == 0, result.output
    # Answer streamed from the deltas; thought text and the final aggregate are
    # not double-printed.
    assert "KIWI" in result.output
    assert "reasoning" not in result.output

    run_call = next(c for c in calls if c["url"].endswith("/run_sse"))
    assert run_call["url"] == "https://x/run_sse"
    assert run_call["json"]["app_name"] == "harness"  # fixed
    assert run_call["json"]["session_id"] == "s-1"  # caller-provided
    assert run_call["json"]["user_id"].startswith("u-")  # random, CLI-generated
    assert "harness" not in run_call["json"]  # no overrides passed

    sess_call = next(c for c in calls if "/sessions/" in c["url"])
    assert sess_call["url"] == "https://x/apps/harness/users/" + run_call["json"]["user_id"] + "/sessions/s-1"


def test_harness_run_sse_sends_overrides(tmp_path, monkeypatch):
    _write_registry(tmp_path, {"first": {"url": "https://x", "key": "ak"}})
    calls = []
    sse = ['data: {"content":{"parts":[{"text":"PINEAPPLE"}]},"partial":true}']

    class _SSEResp:
        status_code = 200
        text = ""

        def iter_lines(self, decode_unicode=False):
            return iter(sse)

    def fake_post(url, json=None, headers=None, timeout=None, stream=False):
        calls.append({"url": url, "json": json})
        return _SSEResp() if url.endswith("/run_sse") else _FakeResponse({}, 200)

    monkeypatch.setattr("requests.post", fake_post)

    result = _run_harness(
        [
            "first",
            "x",
            "--directory",
            str(tmp_path),
            "--protocol",
            "run_sse",
            "--system-prompt",
            "Reply PINEAPPLE.",
            "--tools",
            "web_search",
        ]
    )
    assert result.exit_code == 0, result.output
    run_call = next(c for c in calls if c["url"].endswith("/run_sse"))
    # Overrides are sent as the `harness` field (mirrors HarnessOverrides shape).
    assert run_call["json"]["harness"] == {
        "system_prompt": "Reply PINEAPPLE.",
        "tools": "web_search",
    }


def _make_jwt(sub):
    """Build an unsigned JWT whose payload carries the given ``sub`` claim."""
    import base64

    def _seg(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{_seg({'alg': 'none'})}.{_seg({'sub': sub})}.sig"


def test_run_sse_user_id_from_jwt_sub(tmp_path, monkeypatch):
    """With a JWT bearer token, user_id is the token's ``sub`` (not random)."""
    _write_registry(tmp_path, {"first": {"url": "https://x", "key": "ak"}})
    calls = []
    sse = ['data: {"content":{"parts":[{"text":"OK"}]},"partial":true}']

    class _SSEResp:
        status_code = 200
        text = ""

        def iter_lines(self, decode_unicode=False):
            return iter(sse)

    def fake_post(url, json=None, headers=None, timeout=None, stream=False):
        calls.append({"url": url, "json": json})
        return _SSEResp() if url.endswith("/run_sse") else _FakeResponse({}, 200)

    monkeypatch.setattr("requests.post", fake_post)

    token = _make_jwt("user-abc-123")
    result = _run_harness(
        [
            "first",
            "hi",
            "--directory",
            str(tmp_path),
            "--protocol",
            "run_sse",
            "--session-id",
            "s-1",
            "--apikey",
            token,
        ]
    )
    assert result.exit_code == 0, result.output
    run_call = next(c for c in calls if c["url"].endswith("/run_sse"))
    assert run_call["json"]["user_id"] == "user-abc-123"
    # Session is created under the same (sub-derived) user_id.
    sess_call = next(c for c in calls if "/sessions/" in c["url"])
    assert sess_call["url"] == "https://x/apps/harness/users/user-abc-123/sessions/s-1"


def test_user_id_from_token_helper():
    assert cli_invoke._user_id_from_token(_make_jwt("u-42")) == "u-42"
    assert cli_invoke._user_id_from_token("opaque-api-key") is None
    assert cli_invoke._user_id_from_token("") is None


def test_default_protocol_is_run_sse_with_random_session(tmp_path, monkeypatch):
    """No --protocol and no --session-id → run_sse with a freshly minted session."""
    _write_registry(tmp_path, {"first": {"url": "https://x", "key": "ak"}})
    calls = []
    sse = ['data: {"content":{"parts":[{"text":"OK"}]},"partial":true}']

    class _SSEResp:
        status_code = 200
        text = ""

        def iter_lines(self, decode_unicode=False):
            return iter(sse)

    def fake_post(url, json=None, headers=None, timeout=None, stream=False):
        calls.append({"url": url, "json": json})
        return _SSEResp() if url.endswith("/run_sse") else _FakeResponse({}, 200)

    monkeypatch.setattr("requests.post", fake_post)

    result = _run_harness(["first", "hi", "--directory", str(tmp_path)])
    assert result.exit_code == 0, result.output
    # Default transport is run_sse (not /harness/invoke).
    run_call = next(c for c in calls if c["url"].endswith("/run_sse"))
    sid = run_call["json"]["session_id"]
    assert sid.startswith("s-")  # random, not the invoke-path default
    # The session is created (idempotently) before the run, under that id.
    sess_call = next(c for c in calls if "/sessions/" in c["url"])
    assert sess_call["url"].endswith(f"/sessions/{sid}")


def test_run_sse_hides_reasoning_shows_answer(tmp_path, monkeypatch):
    """Reasoning (thought) stays behind the spinner; only the answer is printed."""
    _write_registry(tmp_path, {"first": {"url": "https://x", "key": "ak"}})
    sse = [
        'data: {"content":{"parts":[{"text":"let me think","thought":true}]},"partial":true}',
        'data: {"content":{"parts":[{"text":"FINAL"}]},"partial":true}',
    ]

    class _SSEResp:
        status_code = 200
        text = ""

        def iter_lines(self, decode_unicode=False):
            return iter(sse)

    def fake_post(url, json=None, headers=None, timeout=None, stream=False):
        return _SSEResp() if url.endswith("/run_sse") else _FakeResponse({}, 200)

    monkeypatch.setattr("requests.post", fake_post)

    result = _run_harness(
        ["first", "hi", "--directory", str(tmp_path), "--session-id", "s-1"]
    )
    assert result.exit_code == 0, result.output
    out = result.output
    assert "FINAL" in out
    assert "let me think" not in out  # reasoning is not dumped to the user


def test_harness_invalid_protocol_fails(tmp_path):
    _write_registry(tmp_path, {"first": {"url": "https://x", "key": "ak"}})
    result = _run_harness(
        ["first", "hi", "--directory", str(tmp_path), "--protocol", "bogus"]
    )
    assert result.exit_code == 1
    assert "must be 'invoke' or 'run_sse'" in result.output
