# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
# Licensed under the Apache License, Version 2.0.

"""Unit tests for agentkit.auth.model_login (pure logic - no network)."""

from __future__ import annotations

import base64
import json

import pytest

from agentkit.auth import model_login as ml


def _b64url(obj: dict) -> str:
    raw = json.dumps(obj).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _make_jwt(payload: dict) -> str:
    return f"{_b64url({'alg': 'RS256'})}.{_b64url(payload)}.sig"


def _injected_payload(cmd: str) -> dict:
    """Decode the base64 blob an injection command writes, back into a dict."""
    b64 = cmd.split("printf %s '", 1)[1].split("'", 1)[0]
    return json.loads(base64.b64decode(b64).decode())


# b64 / jwt helpers
def test_b64_roundtrip():
    assert base64.b64decode(ml.b64("héllo")).decode() == "héllo"
    assert "\n" not in ml.b64("x" * 1000)  # single line - safe to embed in printf


def test_decode_jwt_claims():
    tok = _make_jwt({"email": "a@b.com", "exp": 123})
    assert ml.decode_jwt_claims(tok) == {"email": "a@b.com", "exp": 123}


def test_decode_jwt_claims_malformed():
    with pytest.raises(ml.ModelLoginError):
        ml.decode_jwt_claims("not-a-jwt")


# codex auth validation + summary
def test_validate_codex_auth_tokens_ok():
    ml.validate_codex_auth({"tokens": {"id_token": "x.y.z"}})


def test_validate_codex_auth_apikey_ok():
    ml.validate_codex_auth({"OPENAI_API_KEY": "sk-x", "tokens": None})


def test_validate_codex_auth_empty_raises():
    with pytest.raises(ml.ModelLoginError):
        ml.validate_codex_auth({"tokens": {}, "OPENAI_API_KEY": None})


def test_codex_auth_summary_flags_local_key_but_not_injected():
    s = ml.codex_auth_summary({"tokens": {"id_token": "a.b.c"}, "OPENAI_API_KEY": "sk-secret"})
    assert s["has_oauth_login"] is True
    assert s["has_local_api_key"] is True  # surfaced so the CLI can warn
    assert "sk-secret" not in json.dumps(s)  # the key value never appears in the summary


def test_codex_auth_summary_decodes_plan():
    idt = _make_jwt(
        {
            "email": "u@x.com",
            "exp": 9999999999,
            ml.CODEX_OAUTH_NAMESPACE: {"chatgpt_plan_type": "pro", "chatgpt_account_id": "acc-1"},
        }
    )
    s = ml.codex_auth_summary({"auth_mode": "chatgpt", "tokens": {"id_token": idt, "refresh_token": "r"}})
    assert s["plan"] == "pro"
    assert s["email"] == "u@x.com"
    assert s["id_token_expired"] is False


# security: OAuth-only sanitization (never inject a long-lived API key)
def test_sanitize_strips_api_key_keeps_oauth():
    raw = {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": "sk-LONG-LIVED-SECRET",
        "tokens": {"id_token": "i", "access_token": "a", "refresh_token": "r", "account_id": "acc"},
        "last_refresh": "2026-06-30",
    }
    safe = ml.sanitize_codex_auth_for_injection(raw)
    assert safe["OPENAI_API_KEY"] is None
    assert "sk-LONG-LIVED-SECRET" not in json.dumps(safe)  # the key never survives
    assert safe["tokens"] == raw["tokens"]  # OAuth tokens carried through
    assert safe["auth_mode"] == "chatgpt"


def test_sanitize_refuses_apikey_only():
    with pytest.raises(ml.ModelLoginError):
        ml.sanitize_codex_auth_for_injection({"OPENAI_API_KEY": "sk-x", "tokens": None})


def test_sanitize_drops_unknown_top_level_fields():
    raw = {
        "tokens": {"id_token": "i"},
        "OPENAI_API_KEY": "sk-x",
        "some_other_secret": "leak-me",
    }
    safe = ml.sanitize_codex_auth_for_injection(raw)
    assert set(safe.keys()) == {"OPENAI_API_KEY", "auth_mode", "tokens", "last_refresh"}
    assert "leak-me" not in json.dumps(safe)


def test_build_codex_injection_command_injects_oauth_only():
    raw = {"OPENAI_API_KEY": "sk-secret", "tokens": {"id_token": "i", "refresh_token": "r"}}
    cmd = ml.build_codex_injection_command(auth_data=raw)
    assert ml.CODEX_INJECT_MARKER in cmd
    assert 'chmod 600 "$CH/auth.json"' in cmd
    assert "${CODEX_HOME:-$HOME/.codex}" in cmd
    assert "sk-secret" not in cmd and "sk-secret" not in base64.b64decode(
        cmd.split("printf %s '", 1)[1].split("'", 1)[0]
    ).decode()
    payload = _injected_payload(cmd)
    assert payload["OPENAI_API_KEY"] is None
    assert payload["tokens"]["id_token"] == "i"


def test_build_codex_injection_command_refuses_apikey_only():
    with pytest.raises(ml.ModelLoginError):
        ml.build_codex_injection_command(auth_data={"OPENAI_API_KEY": "sk-x"})


def test_build_codex_injection_command_never_touches_config():
    cmd = ml.build_codex_injection_command(auth_data={"tokens": {"id_token": "i"}})
    assert "config.toml" not in cmd  # inject-only: config left to the platform / exec-time -c


# local resolution
def test_read_codex_auth_missing(tmp_path):
    with pytest.raises(ml.ModelLoginError):
        ml.read_codex_auth(tmp_path / "nope.json")


def test_resolve_codex_auth_file(tmp_path):
    p = tmp_path / "auth.json"
    p.write_text('{"tokens":{"id_token":"x.y.z"}}')
    path, data = ml.resolve_local_codex_auth(auth_file=str(p))
    assert path == p
    assert data["tokens"]["id_token"] == "x.y.z"


def test_resolve_codex_missing_no_login(tmp_path):
    with pytest.raises(ml.ModelLoginError):
        ml.resolve_local_codex_auth(codex_home=str(tmp_path), allow_login=False)


def test_resolve_codex_runs_login_runner(tmp_path):
    home = tmp_path / "codex"

    def fake_login(*, codex_home, timeout):
        codex_home.mkdir(parents=True, exist_ok=True)
        (codex_home / "auth.json").write_text('{"tokens":{"id_token":"x"}}')

    path, data = ml.resolve_local_codex_auth(
        codex_home=str(home), allow_login=True, login_runner=fake_login
    )
    assert path == home / "auth.json"
    assert data["tokens"]["id_token"] == "x"


# claude
def test_claude_read_validate_summary(tmp_path):
    creds = {
        "claudeAiOauth": {
            "accessToken": "at",
            "refreshToken": "rt",
            "expiresAt": 1781797582000,  # ms
            "scopes": ["user:inference"],
            "subscriptionType": "max",
        }
    }
    p = tmp_path / ".credentials.json"
    p.write_text(json.dumps(creds))
    data = ml.read_claude_creds(creds_file=str(p))
    ml.validate_claude_creds(data)
    s = ml.claude_creds_summary(data)
    assert s["subscription"] == "max"
    assert s["has_refresh_token"] is True
    assert s["access_token_expires"].startswith("2026-")


def test_claude_validate_missing_token():
    with pytest.raises(ml.ModelLoginError):
        ml.validate_claude_creds({"claudeAiOauth": {}})


def test_claude_missing_file_raises(tmp_path):
    with pytest.raises(ml.ModelLoginError):
        ml.read_claude_creds(creds_file=str(tmp_path / "nope.json"))


def test_claude_sanitize_drops_non_oauth_fields():
    raw = {
        "claudeAiOauth": {"accessToken": "at", "refreshToken": "rt"},
        "primaryApiKey": "sk-ant-LONG-LIVED",
    }
    safe = ml.sanitize_claude_creds_for_injection(raw)
    assert set(safe.keys()) == {"claudeAiOauth"}
    assert "sk-ant-LONG-LIVED" not in json.dumps(safe)


def test_claude_sanitize_refuses_without_oauth():
    with pytest.raises(ml.ModelLoginError):
        ml.sanitize_claude_creds_for_injection({"primaryApiKey": "sk-ant-x"})


def test_build_claude_injection_command():
    raw = {"claudeAiOauth": {"accessToken": "at"}, "primaryApiKey": "sk-ant-x"}
    cmd = ml.build_claude_injection_command(creds_data=raw)
    assert ml.CLAUDE_INJECT_MARKER in cmd
    assert ".credentials.json" in cmd
    assert "chmod 600" in cmd
    assert "sk-ant-x" not in base64.b64decode(cmd.split("printf %s '", 1)[1].split("'", 1)[0]).decode()
