# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
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

"""`agentkit sandbox codex-login` — bring your own model subscription into a sandbox.

Codex/ChatGPT and Claude Code are used via a JWT obtained by an interactive SSO login, not a
static API key. Codex supports that SSO **locally**; in the sandbox it is remote. So instead of
federating the platform UserPool with OpenAI/Anthropic, this command:

  1. reads the provider's native credential the local SSO already produced
     (``$CODEX_HOME/auth.json`` for Codex, ``~/.claude/.credentials.json`` for Claude Code), and
  2. injects it into the **same native path inside the sandbox session**, so the sandbox's codex
     finds its token exactly where it natively looks.

The sandbox refreshes the token itself against the provider, just like a local install would. The
heavy lifting (resolve / validate / redact / config rewrite / build the injection command) lives in
``agentkit.auth.model_login``; this file is only the sandbox-session transport + CLI surface.
"""

from __future__ import annotations

import json
import re
from typing import Optional

import typer

from agentkit.toolkit.cli.sandbox.cli_file import _exec_shell_command
from agentkit.toolkit.cli.sandbox.session_create import (
    SANDBOX_TOOL_ID_ENV,
    ensure_sandbox_session,
)
from agentkit.toolkit.cli.sandbox.tool_resolve import SandboxToolType
from agentkit.toolkit.cli.sandbox.sandbox_client import error


def _shell_output(payload: dict) -> str:
    data = payload.get("data")
    if isinstance(data, dict):
        out = data.get("output")
        if isinstance(out, str):
            return out
    return ""


def _redact_inject(cmd: str) -> str:
    """Redact the base64 token blob in an injection command (for --dry-run display)."""
    return re.sub(
        r"printf %s '([A-Za-z0-9+/=]+)'",
        lambda m: f"printf %s '<base64 {len(m.group(1))} bytes — redacted>'",
        cmd,
    )


def codex_login_command(
    session_id: Optional[str] = typer.Option(
        None,
        "--session-id",
        "--sid",
        "-s",
        help=(
            "Sandbox session to inject into. Defaults to a new session; reuse the printed id "
            "with `agentkit sandbox exec --sid <id>`."
        ),
    ),
    provider: str = typer.Option(
        "codex",
        "--provider",
        "-p",
        help="Subscription to bring in: codex (ChatGPT) or claude (Claude Code).",
    ),
    auth_file: Optional[str] = typer.Option(
        None,
        "--auth-file",
        help=(
            "Use a specific local credential file (codex auth.json / claude .credentials.json) "
            "instead of running the local SSO."
        ),
    ),
    codex_home: Optional[str] = typer.Option(
        None,
        "--codex-home",
        help="Local codex home (default $CODEX_HOME or ~/.codex).",
    ),
    login: bool = typer.Option(
        True,
        "--login/--no-login",
        help="If no local credential exists, trigger `codex login` (browser SSO) once.",
    ),
    keep_model_config: bool = typer.Option(
        False,
        "--keep-model-config",
        help="Only inject the token; do not switch the sandbox codex config to subscription auth.",
    ),
    tool_id: Optional[str] = typer.Option(
        None,
        "--tool-id",
        help=f"Sandbox tool ID. Defaults to {SANDBOX_TOOL_ID_ENV}.",
    ),
    tool_type: SandboxToolType = typer.Option(
        SandboxToolType.CODE_ENV,
        "--tool-type",
        help="Sandbox tool type to resolve when --tool-id is omitted.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Resolve the local credential and print what would be injected (token redacted); no session.",
    ),
) -> None:
    """Inject your own ChatGPT/Codex or Claude Code subscription token into a sandbox session.

    Run the provider's local SSO once (codex supports it locally), then this injects the native
    token into the sandbox's native location so the sandbox codex runs on your subscription.
    """
    from agentkit.auth import model_login as ml

    provider = (provider or "codex").lower()
    if provider not in ml.PROVIDERS:
        error(f"unknown provider: {provider} (supported: {', '.join(ml.PROVIDERS)})")

    # 1) resolve the LOCAL native credential (runs the provider's local SSO if needed)
    try:
        if provider == "codex":
            src, data = ml.resolve_local_codex_auth(
                codex_home=codex_home, auth_file=auth_file, allow_login=login
            )
            summary = ml.codex_auth_summary(data)
        else:  # claude
            data = ml.read_claude_creds(creds_file=auth_file)
            ml.validate_claude_creds(data)
            src = ml.claude_creds_path(auth_file)
            summary = ml.claude_creds_summary(data)
    except ml.ModelLoginError as exc:
        error(str(exc))

    typer.secho(f"\nLocal credential: {src}", fg=typer.colors.CYAN, err=True)
    typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))
    if summary.get("id_token_expired") and not summary.get("has_refresh_token"):
        typer.secho(
            "  warning: token expired and has no refresh_token — re-run `codex login` locally first.",
            fg=typer.colors.YELLOW,
            err=True,
        )

    payload = json.dumps(data, ensure_ascii=False)

    # 2) build the injection (codex also switches its config to subscription auth unless --keep-model-config)
    if provider == "codex":
        if dry_run:
            typer.secho("\n— dry-run: read current sandbox codex config (read-only) —", fg=typer.colors.CYAN, err=True)
            typer.secho(f"  $ {ml.read_codex_config_command()}", err=True)
            cmd = ml.build_codex_injection_command(
                auth_json=payload,
                config_toml=None if keep_model_config else ml.minimal_chatgpt_codex_config(),
            )
            typer.secho("\n— dry-run: would inject (token redacted) —", fg=typer.colors.CYAN, err=True)
            typer.secho(_redact_inject(cmd), err=True)
            raise typer.Exit(0)
        marker = ml.CODEX_INJECT_MARKER
    else:  # claude
        cmd = ml.build_claude_injection_command(creds_json=payload)
        marker = ml.CLAUDE_INJECT_MARKER
        if dry_run:
            typer.secho("\n— dry-run: would inject (token redacted) —", fg=typer.colors.CYAN, err=True)
            typer.secho(_redact_inject(cmd), err=True)
            raise typer.Exit(0)

    # 3) ensure the sandbox session, then inject over its shell-exec endpoint
    try:
        session = ensure_sandbox_session(
            session_id=session_id,
            tool_id=tool_id,
            tool_type=tool_type.value,
        )
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001
        error(str(exc))

    sid = session.get("session_id")
    if not isinstance(sid, str) or not sid:
        error("sandbox session missing session_id")

    if provider == "codex":
        new_cfg = None
        if not keep_model_config:
            cur = _shell_output(
                _exec_shell_command(session, ml.read_codex_config_command(), quiet_errors=True)
            )
            new_cfg = (
                ml.rewrite_codex_config_for_chatgpt(cur)
                if cur.strip()
                else ml.minimal_chatgpt_codex_config()
            )
        cmd = ml.build_codex_injection_command(auth_json=payload, config_toml=new_cfg)

    out = _shell_output(_exec_shell_command(session, cmd))
    if marker not in out:
        error(f"injection did not confirm (marker missing). sandbox output: {out[:200]}")
    where = out.split(marker, 1)[1].strip() or "<sandbox>"

    typer.secho(
        f"\n✓ injected your {summary.get('provider', provider)} subscription into {where}",
        fg=typer.colors.GREEN,
        bold=True,
        err=True,
    )
    typer.secho(f"  session: {sid}", fg=typer.colors.CYAN, err=True)
    if provider == "codex":
        typer.secho(
            f'  next:  agentkit sandbox exec --sid {sid} --command "codex"   # codex runs on your subscription',
            fg=typer.colors.CYAN,
            err=True,
        )
        if not keep_model_config:
            typer.secho(
                "  (switched the sandbox codex config to ChatGPT subscription auth; keep it with --keep-model-config)",
                fg=typer.colors.BRIGHT_BLACK,
                err=True,
            )
    typer.secho(
        "  note: the token is refreshed by codex/claude inside the sandbox; re-inject if the session is recreated.",
        fg=typer.colors.BRIGHT_BLACK,
        err=True,
    )
    typer.echo(json.dumps({"injected": True, "provider": provider, "session_id": sid, "path": where}, ensure_ascii=False))
