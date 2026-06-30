# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
# Licensed under the Apache License, Version 2.0.

"""CLI tests for `agentkit sandbox codex-login` (the model-subscription injector).

The local credential is supplied via --auth-file; the sandbox session + shell exec are mocked so
no network/login happens. We assert the injected payload + the ChatGPT config switch.
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
    monkeypatch.setattr(
        cml, "ensure_sandbox_session", lambda **kw: {"session_id": "s-1", "endpoint": "https://sbx.example"}
    )

    def fake_exec(session, command, quiet_errors=False):
        captured.append(command)
        if "config.toml" in command and command.strip().startswith("cat"):
            return {"data": {"output": 'model_provider = "codex"\nmodel = "ark"\n[tui]\nshow_tooltips = false\n', "exit_code": 0}}
        if ml.CODEX_INJECT_MARKER in command:
            return {"data": {"output": f"{ml.CODEX_INJECT_MARKER} /home/gem/.codex", "exit_code": 0}}
        if ml.CLAUDE_INJECT_MARKER in command:
            return {"data": {"output": f"{ml.CLAUDE_INJECT_MARKER} /home/gem/.claude", "exit_code": 0}}
        return {"data": {"output": "", "exit_code": 0}}

    monkeypatch.setattr(cml, "_exec_shell_command", fake_exec)


def test_codex_login_injects_and_switches_config(tmp_path, monkeypatch):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"id_token": "a.b.c", "refresh_token": "r"}}))
    captured: list[str] = []
    _patch_sandbox(monkeypatch, captured)

    result = runner.invoke(app, ["sandbox", "codex-login", "--auth-file", str(auth)])
    assert result.exit_code == 0, result.output

    # read the current sandbox config, then inject auth.json + a ChatGPT-mode config
    assert any(c.strip().startswith("cat") and "config.toml" in c for c in captured)
    inject = next(c for c in captured if ml.CODEX_INJECT_MARKER in c)
    assert ml.b64(json.dumps(json.loads(auth.read_text()), ensure_ascii=False)) in inject
    cfg_b64 = inject.split("printf %s '")[2].split("'")[0]
    pushed_cfg = base64.b64decode(cfg_b64).decode()
    assert pushed_cfg.startswith('preferred_auth_method = "chatgpt"')
    assert 'model_provider = "codex"' not in pushed_cfg
    assert "[tui]" in pushed_cfg
    assert "s-1" in result.output  # session id surfaced for reuse


def test_codex_login_keep_model_config(tmp_path, monkeypatch):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"id_token": "a.b.c"}}))
    captured: list[str] = []
    _patch_sandbox(monkeypatch, captured)

    result = runner.invoke(app, ["sandbox", "codex-login", "--auth-file", str(auth), "--keep-model-config"])
    assert result.exit_code == 0, result.output
    assert not any(c.strip().startswith("cat") for c in captured)  # no config read
    inject = next(c for c in captured if ml.CODEX_INJECT_MARKER in c)
    assert "config.toml" not in inject


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


def test_claude_login_injects(tmp_path, monkeypatch):
    creds = tmp_path / ".credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "at", "refreshToken": "rt", "subscriptionType": "max"}}))
    captured: list[str] = []
    _patch_sandbox(monkeypatch, captured)

    result = runner.invoke(app, ["sandbox", "model-login", "--provider", "claude", "--auth-file", str(creds)])
    assert result.exit_code == 0, result.output
    inject = next(c for c in captured if ml.CLAUDE_INJECT_MARKER in c)
    assert ".credentials.json" in inject


def test_unknown_provider(monkeypatch):
    captured: list[str] = []
    _patch_sandbox(monkeypatch, captured)
    result = runner.invoke(app, ["sandbox", "model-login", "--provider", "gemini"])
    assert result.exit_code == 1
    assert "provider" in result.output.lower()
