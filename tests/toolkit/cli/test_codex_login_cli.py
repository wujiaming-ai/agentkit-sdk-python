# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
# Licensed under the Apache License, Version 2.0.

"""CLI tests for `agentkit sandbox codex-login` (the model-subscription injector).

The local credential is supplied via --auth-file; the sandbox session + shell exec are mocked so
no network/login happens. We assert the feature injects ONLY the OAuth token (never a long-lived
API key) and uses the codex_login provider without a built-in model catalog.
"""

from __future__ import annotations

import base64
import json

from typer.testing import CliRunner

from agentkit.auth import model_login as ml
from agentkit.toolkit.cli.cli import app
from agentkit.toolkit.cli.sandbox import cli_model_login as cml

runner = CliRunner()


def _patch_sandbox(monkeypatch, captured):
    """Mock the sandbox session + shell exec; record every command and answer it."""
    captured_session = {}

    def fake_ensure_sandbox_session(**kw):
        captured_session.update(kw)
        return {"session_id": "s-1", "endpoint": "https://sbx.example"}

    monkeypatch.setattr(
        cml,
        "ensure_sandbox_session",
        fake_ensure_sandbox_session,
    )

    def fake_exec(session, command, quiet_errors=False):
        captured.append(command)
        if ml.CODEX_INJECT_MARKER in command:
            return {"data": {"output": f"{ml.CODEX_INJECT_MARKER} /home/gem/.codex", "exit_code": 0}}
        if ml.CLAUDE_INJECT_MARKER in command:
            return {"data": {"output": f"{ml.CLAUDE_INJECT_MARKER} /home/gem/.claude", "exit_code": 0}}
        return {"data": {"output": "", "exit_code": 0}}

    monkeypatch.setattr(cml, "_exec_shell_command", fake_exec)
    return captured_session


def _injected_payload(cmd: str) -> dict:
    b64 = cmd.split("printf %s '", 1)[1].split("'", 1)[0]
    return json.loads(base64.b64decode(b64).decode())


def test_codex_login_injects_oauth_only_and_uses_codex_login_provider(
    tmp_path,
    monkeypatch,
):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": "sk-LONG-LIVED-SECRET",   # must never reach the sandbox
        "tokens": {"id_token": "a.b.c", "refresh_token": "r", "account_id": "acc"},
    }))
    captured: list[str] = []
    captured_session = _patch_sandbox(monkeypatch, captured)

    result = runner.invoke(app, ["sandbox", "codex-login", "--auth-file", str(auth)])
    assert result.exit_code == 0, result.output

    # exactly one exec: the injection (no config read/write)
    assert not any("config.toml" in c for c in captured)
    inject = next(c for c in captured if ml.CODEX_INJECT_MARKER in c)
    assert "sk-LONG-LIVED-SECRET" not in inject
    payload = _injected_payload(inject)
    assert payload["OPENAI_API_KEY"] is None            # key stripped
    assert payload["tokens"]["id_token"] == "a.b.c"     # OAuth carried
    assert "s-1" in result.output                       # session id surfaced
    assert "sk-LONG-LIVED-SECRET" not in result.output  # never printed
    envs = {item.key: item.value for item in captured_session["envs"]}
    assert envs["AGENTKIT_SANDBOX_MODEL_PROVIDER"] == "codex_login"
    assert envs["CODEX_MODEL"] == "gpt-5.5"
    assert 'model_provider = "codex_login"' in envs["CODEX_CONFIG_TOML"]
    assert "requires_openai_auth = true" in envs["CODEX_CONFIG_TOML"]
    assert "model_catalog_json" not in envs["CODEX_CONFIG_TOML"]
    assert "CODEX_MODEL_CATALOG_JSON" not in envs
    assert "CODEX_API_KEY" not in envs
    assert "OPENCODE_API_KEY" not in envs
    assert "ANTHROPIC_AUTH_TOKEN" not in envs


def test_codex_login_refuses_apikey_only(tmp_path, monkeypatch):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"OPENAI_API_KEY": "sk-x", "tokens": None}))
    captured: list[str] = []
    _patch_sandbox(monkeypatch, captured)

    result = runner.invoke(app, ["sandbox", "codex-login", "--auth-file", str(auth)])
    assert result.exit_code != 0
    assert "OAuth" in result.output or "refusing" in result.output
    assert not captured  # nothing injected


def test_codex_login_dry_run_no_session(tmp_path, monkeypatch):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"id_token": "a.b.c"}}))

    def boom(**kw):
        raise AssertionError("dry-run must not create a session")

    monkeypatch.setattr(cml, "ensure_sandbox_session", boom)
    result = runner.invoke(app, ["sandbox", "codex-login", "--auth-file", str(auth), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "redacted" in result.output
    assert "a.b.c" not in result.output  # token never printed


def test_codex_login_missing_auth_file(monkeypatch):
    captured: list[str] = []
    _patch_sandbox(monkeypatch, captured)
    result = runner.invoke(app, ["sandbox", "codex-login", "--auth-file", "/no/such/file.json"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_claude_login_injects_oauth_only(tmp_path, monkeypatch):
    creds = tmp_path / ".credentials.json"
    creds.write_text(json.dumps({
        "claudeAiOauth": {"accessToken": "at", "refreshToken": "rt", "subscriptionType": "max"},
        "primaryApiKey": "sk-ant-LONG-LIVED",
    }))
    captured: list[str] = []
    _patch_sandbox(monkeypatch, captured)

    result = runner.invoke(app, ["sandbox", "model-login", "--provider", "claude", "--auth-file", str(creds)])
    assert result.exit_code == 0, result.output
    inject = next(c for c in captured if ml.CLAUDE_INJECT_MARKER in c)
    assert ".credentials.json" in inject
    payload = _injected_payload(inject)
    assert set(payload.keys()) == {"claudeAiOauth"}      # only OAuth
    assert "sk-ant-LONG-LIVED" not in inject


def test_unknown_provider(monkeypatch):
    captured: list[str] = []
    _patch_sandbox(monkeypatch, captured)
    result = runner.invoke(app, ["sandbox", "model-login", "--provider", "gemini"])
    assert result.exit_code == 1
    assert "provider" in result.output.lower()
