# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
# Licensed under the Apache License, Version 2.0.

"""Unit tests for agentkit.auth.model_login (pure logic — no network)."""

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


# ── b64 / jwt helpers ────────────────────────────────────────────────────────
def test_b64_roundtrip():
    assert base64.b64decode(ml.b64("héllo")).decode() == "héllo"
    assert "\n" not in ml.b64("x" * 1000)  # single line — safe to embed in printf


def test_decode_jwt_claims():
    tok = _make_jwt({"email": "a@b.com", "exp": 123})
    assert ml.decode_jwt_claims(tok) == {"email": "a@b.com", "exp": 123}


def test_decode_jwt_claims_malformed():
    with pytest.raises(ml.ModelLoginError):
        ml.decode_jwt_claims("not-a-jwt")


# ── codex auth validation + summary ──────────────────────────────────────────
def test_validate_codex_auth_tokens_ok():
    ml.validate_codex_auth({"tokens": {"id_token": "x.y.z"}})


def test_validate_codex_auth_apikey_ok():
    ml.validate_codex_auth({"OPENAI_API_KEY": "sk-x", "tokens": None})


def test_validate_codex_auth_empty_raises():
    with pytest.raises(ml.ModelLoginError):
        ml.validate_codex_auth({"tokens": {}, "OPENAI_API_KEY": None})


def test_codex_auth_summary_redacts_and_decodes_plan():
    idt = _make_jwt(
        {
            "email": "u@x.com",
            "exp": 9999999999,
            ml.CODEX_OAUTH_NAMESPACE: {"chatgpt_plan_type": "pro", "chatgpt_account_id": "acc-1"},
        }
    )
    data = {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {"id_token": idt, "refresh_token": "r", "account_id": "acc-1"},
    }
    s = ml.codex_auth_summary(data)
    assert s["plan"] == "pro"
    assert s["email"] == "u@x.com"
    assert s["has_refresh_token"] is True
    assert s["account_id"] == "acc-1"
    assert s["id_token_expired"] is False
    # no secret material leaks into the summary
    blob = json.dumps(s)
    assert idt not in blob and "r" != s.get("has_refresh_token")


def test_codex_auth_summary_expired_no_refresh():
    idt = _make_jwt({"email": "u@x.com", "exp": 1})  # long expired
    s = ml.codex_auth_summary({"tokens": {"id_token": idt}})
    assert s["id_token_expired"] is True
    assert s["has_refresh_token"] is False


# ── config rewrite ───────────────────────────────────────────────────────────
def test_rewrite_drops_ark_pins_keeps_tables():
    src = "\n".join(
        [
            'model_provider = "codex"',
            'model = "ark-x"',
            'review_model = "ark-x"',
            'model_reasoning_effort = "medium"',
            "",
            "[model_providers.codex]",
            'env_key = "CODEX_API_KEY"',
            'model = "should-not-be-removed-inside-table"',
            "",
            "[tui]",
            "show_tooltips = false",
        ]
    )
    out = ml.rewrite_codex_config_for_chatgpt(src)
    assert out.startswith('preferred_auth_method = "chatgpt"\n')
    # the top-level Ark pins are gone (the assignment lines, not the table name)
    assert 'model_provider = "codex"' not in out
    assert 'model = "ark-x"' not in out
    assert 'review_model = "ark-x"' not in out
    # non-pin keys & tables preserved verbatim
    assert "model_reasoning_effort" in out
    assert "[model_providers.codex]" in out  # the provider table itself is kept
    assert "[tui]" in out
    assert "should-not-be-removed-inside-table" in out  # `model=` inside a table is untouched


def test_rewrite_replaces_existing_auth_method():
    src = 'preferred_auth_method = "apikey"\nmodel_provider = "codex"\n'
    out = ml.rewrite_codex_config_for_chatgpt(src)
    assert out.count("preferred_auth_method") == 1
    assert '"apikey"' not in out


def test_minimal_config_uses_chatgpt():
    cfg = ml.minimal_chatgpt_codex_config()
    assert 'preferred_auth_method = "chatgpt"' in cfg
    assert "trust_level" in cfg


# ── injection command ────────────────────────────────────────────────────────
def test_build_codex_injection_command():
    auth = '{"tokens":{"id_token":"x"}}'
    cmd = ml.build_codex_injection_command(auth_json=auth, config_toml="cfg")
    assert ml.b64(auth) in cmd
    assert ml.b64("cfg") in cmd
    assert ml.CODEX_INJECT_MARKER in cmd
    assert 'chmod 600 "$CH/auth.json"' in cmd
    assert '${CODEX_HOME:-$HOME/.codex}' in cmd


def test_build_codex_injection_command_no_config():
    cmd = ml.build_codex_injection_command(auth_json="{}")
    assert "config.toml" not in cmd


def test_read_codex_config_command():
    assert "config.toml" in ml.read_codex_config_command()


# ── local resolution ─────────────────────────────────────────────────────────
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


# ── claude ───────────────────────────────────────────────────────────────────
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


def test_build_claude_injection_command():
    cmd = ml.build_claude_injection_command(creds_json='{"claudeAiOauth":{"accessToken":"x"}}')
    assert ml.CLAUDE_INJECT_MARKER in cmd
    assert '.credentials.json' in cmd
    assert 'chmod 600' in cmd
