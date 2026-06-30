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

"""Bring a user's *own* third-party model subscription into the sandbox.

Many foreign models (ChatGPT/Codex, Claude Code) are used via a JWT obtained by an
interactive SSO login, not a static API key. A user who subscribed to one of those
plans wants the **sandbox's** codex to run on *their* subscription.

The agentkit UserPool does not (and need not) federate with OpenAI/Anthropic. Codex
already supports SSO **locally**; in the sandbox it is "remote", so the agreed design
is dead simple and is what this module implements:

    1. The user runs the provider's native SSO **on their PC** (``codex login`` for
       Codex/ChatGPT, ``claude`` for Claude Code). That writes the provider's native
       credential file:
           Codex   ->  $CODEX_HOME/auth.json   (default ~/.codex/auth.json)
           Claude  ->  ~/.claude/.credentials.json   (or the macOS Keychain)
    2. We read that file verbatim and inject it into the **same native path inside
       the user's private sandbox** — so the sandbox's codex finds its token exactly
       where it natively looks. No proxy, no federation: the sandbox refreshes the
       token itself against the provider, just like a local install would.

This module is pure (stdlib only) and side-effect-light so it is unit-testable; the
network/forwarding lives in the CLI layer (``cli_accesscontrol.py``).
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable, Optional

# ── Codex / ChatGPT facts ────────────────────────────────────────────────────
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # codex CLI's public OAuth client (the id_token aud)
CODEX_OAUTH_NAMESPACE = "https://api.openai.com/auth"  # claim namespace holding the ChatGPT plan
DEFAULT_SANDBOX_CODEX_HOME = "/home/gem/.codex"  # informational; the shell resolves ${CODEX_HOME:-$HOME/.codex}
CODEX_INJECT_MARKER = "AGENTKIT_CODEX_INJECTED"
CLAUDE_INJECT_MARKER = "AGENTKIT_CLAUDE_INJECTED"
CLAUDE_KEYCHAIN_SERVICE = "Claude Code-credentials"

PROVIDERS = ("codex", "claude")


class ModelLoginError(RuntimeError):
    """A user-facing failure resolving or injecting a model subscription token."""


# ── small helpers ────────────────────────────────────────────────────────────
def b64(data: str | bytes) -> str:
    """Standard base64 (single line, no newlines) — safe to embed in a shell ``printf``."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.b64encode(data).decode("ascii")


def _iso(epoch_seconds: int) -> str:
    return datetime.datetime.fromtimestamp(epoch_seconds, tz=datetime.timezone.utc).isoformat()


def decode_jwt_claims(jwt: str) -> dict:
    """Decode (NOT verify) a JWT's payload claims. We only read non-secret metadata."""
    parts = (jwt or "").split(".")
    if len(parts) < 2:
        raise ModelLoginError("malformed JWT")
    seg = parts[1]
    try:
        return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
    except Exception as exc:  # noqa: BLE001
        raise ModelLoginError(f"cannot decode JWT claims: {exc}") from exc


# ── Codex (ChatGPT) ──────────────────────────────────────────────────────────
def codex_home_path(explicit: Optional[str] = None) -> Path:
    """Resolve the LOCAL codex home: --codex-home > $CODEX_HOME > ~/.codex."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("CODEX_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".codex"


def read_codex_auth(path: str | Path) -> dict:
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ModelLoginError(f"codex auth file not found: {p}") from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise ModelLoginError(f"cannot read codex auth file {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise ModelLoginError(f"codex auth file {p} is not a JSON object")
    return data


def validate_codex_auth(data: dict) -> None:
    """A usable codex auth has ChatGPT tokens (SSO) or an API key."""
    tokens = data.get("tokens")
    has_tokens = isinstance(tokens, dict) and bool(tokens.get("id_token") or tokens.get("access_token"))
    has_key = bool(data.get("OPENAI_API_KEY"))
    if not (has_tokens or has_key):
        raise ModelLoginError(
            "codex auth.json has neither ChatGPT tokens nor an API key — run `codex login` first"
        )


def codex_auth_summary(data: dict) -> dict:
    """A redacted, secret-free summary (provider, plan, account, expiry) for display."""
    tokens = data.get("tokens") or {}
    summary: dict = {
        "provider": "codex (ChatGPT)",
        "auth_mode": data.get("auth_mode") or ("chatgpt" if tokens else "apikey"),
        "has_refresh_token": bool(tokens.get("refresh_token")),
        "account_id": tokens.get("account_id"),
    }
    idt = tokens.get("id_token")
    if idt:
        try:
            claims = decode_jwt_claims(idt)
        except ModelLoginError:
            claims = {}
        ns = claims.get(CODEX_OAUTH_NAMESPACE) or {}
        summary["email"] = claims.get("email")
        summary["plan"] = ns.get("chatgpt_plan_type")
        summary["chatgpt_account_id"] = ns.get("chatgpt_account_id") or summary.get("account_id")
        exp = claims.get("exp")
        if isinstance(exp, int):
            summary["id_token_expires"] = _iso(exp)
            summary["id_token_expired"] = exp < int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    return {k: v for k, v in summary.items() if v is not None}


def run_codex_login(
    *, codex_home: Optional[Path] = None, codex_bin: str = "codex", timeout: int = 300
) -> None:
    """Trigger codex's native local SSO (opens a browser) so it writes a fresh auth.json."""
    import subprocess

    env = dict(os.environ)
    if codex_home:
        env["CODEX_HOME"] = str(codex_home)
    try:
        subprocess.run([codex_bin, "login"], env=env, timeout=timeout, check=True)  # noqa: S603
    except FileNotFoundError as exc:
        raise ModelLoginError(
            f"`{codex_bin}` not found on PATH — install the Codex CLI and run `codex login`, "
            f"or pass --auth-file <path-to-your-auth.json>"
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise ModelLoginError(f"`codex login` failed (exit {exc.returncode})") from exc
    except subprocess.TimeoutExpired as exc:
        raise ModelLoginError("`codex login` timed out") from exc


def resolve_local_codex_auth(
    *,
    codex_home: Optional[str] = None,
    auth_file: Optional[str] = None,
    allow_login: bool = True,
    login_timeout: int = 300,
    login_runner: Optional[Callable[..., None]] = None,
) -> tuple[Path, dict]:
    """Return ``(path, data)`` of a usable LOCAL codex auth, running `codex login` if needed."""
    if auth_file:
        path = Path(auth_file).expanduser()
        if not path.exists():
            raise ModelLoginError(f"--auth-file not found: {path}")
        data = read_codex_auth(path)
        validate_codex_auth(data)
        return path, data

    home = codex_home_path(codex_home)
    path = home / "auth.json"
    if not path.exists():
        if not allow_login:
            raise ModelLoginError(
                f"no codex auth at {path} — run `codex login` (or pass --auth-file / --no-login off)"
            )
        (login_runner or run_codex_login)(codex_home=home, timeout=login_timeout)
        if not path.exists():
            raise ModelLoginError(f"`codex login` finished but {path} is still missing")
    data = read_codex_auth(path)
    validate_codex_auth(data)
    return path, data


# ── Codex config.toml: switch the sandbox codex to the ChatGPT subscription ──
_TOP_MODEL_PIN = re.compile(r"^\s*(model|model_provider|review_model)\s*=")
_AUTH_METHOD = re.compile(r"^\s*preferred_auth_method\s*=")
_TABLE_HEADER = re.compile(r"^\s*\[")


def rewrite_codex_config_for_chatgpt(toml_text: str) -> str:
    """Switch an existing codex config.toml to ChatGPT-subscription auth, preserving everything else.

    The sandbox's default config pins a custom ``model_provider``/``model`` to a Volcengine Ark
    endpoint that authenticates with an API key (``env_key``); in that mode codex never looks at the
    injected ChatGPT token. To use the subscription we:
      * drop the top-level ``model`` / ``model_provider`` / ``review_model`` pins, so codex falls back
        to its built-in ``openai`` provider and the subscription's default model, and
      * force ``preferred_auth_method = "chatgpt"`` so the OAuth token wins over any stray API key.
    Tables (``[tui]``, ``[projects.*]``, ``[mcp_servers.*]``, ``[model_providers.*]``) and other
    top-level keys (approval_policy, sandbox_mode, model_reasoning_effort, …) are kept verbatim.
    """
    out: list[str] = []
    seen_table = False
    for line in toml_text.splitlines():
        if _TABLE_HEADER.match(line):
            seen_table = True
        if _AUTH_METHOD.match(line):
            continue  # re-added at the top
        if not seen_table and _TOP_MODEL_PIN.match(line):
            continue
        out.append(line)
    body = "\n".join(out).strip("\n")
    return 'preferred_auth_method = "chatgpt"\n' + (body + "\n" if body else "")


def minimal_chatgpt_codex_config() -> str:
    """A config.toml for a sandbox that has no codex config yet — ChatGPT auth, headless-friendly."""
    return "\n".join(
        [
            'preferred_auth_method = "chatgpt"',
            'approval_policy = "never"',
            'sandbox_mode = "danger-full-access"',
            "",
            '[projects."/home/gem"]',
            'trust_level = "trusted"',
            "",
        ]
    )


def read_codex_config_command() -> str:
    """Shell command that prints the sandbox's current codex config.toml (empty if none)."""
    return 'cat "${CODEX_HOME:-$HOME/.codex}/config.toml" 2>/dev/null || true'


def build_codex_injection_command(*, auth_json: str, config_toml: Optional[str] = None) -> str:
    """A single POSIX-sh command that writes auth.json (and optionally config.toml) into the
    sandbox's native codex home (``${CODEX_HOME:-$HOME/.codex}``), 0600, and prints a marker."""
    lines = [
        "set -e",
        'CH="${CODEX_HOME:-$HOME/.codex}"',
        'mkdir -p "$CH"',
        "umask 077",
        f"printf %s '{b64(auth_json)}' | base64 -d > \"$CH/auth.json\"",
        'chmod 600 "$CH/auth.json"',
    ]
    if config_toml is not None:
        lines.append(f"printf %s '{b64(config_toml)}' | base64 -d > \"$CH/config.toml\"")
    lines.append(f'echo "{CODEX_INJECT_MARKER} $CH"')
    return "\n".join(lines)


# ── Claude Code ──────────────────────────────────────────────────────────────
def claude_creds_path(explicit: Optional[str] = None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    return Path.home() / ".claude" / ".credentials.json"


def _read_macos_keychain(service: str) -> Optional[str]:
    import subprocess

    try:
        out = subprocess.run(  # noqa: S603
            ["security", "find-generic-password", "-s", service, "-w"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return (out.stdout or "").strip() or None


def read_claude_creds(*, creds_file: Optional[str] = None, allow_keychain: bool = True) -> dict:
    """Read Claude Code subscription creds from the file, or the macOS Keychain as a fallback."""
    path = claude_creds_path(creds_file)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ModelLoginError(f"cannot read {path}: {exc}") from exc
    elif creds_file:
        raise ModelLoginError(f"--auth-file not found: {path}")
    elif allow_keychain and sys.platform == "darwin":
        raw = _read_macos_keychain(CLAUDE_KEYCHAIN_SERVICE)
        if not raw:
            raise ModelLoginError(
                "no Claude Code credentials in ~/.claude/.credentials.json or the macOS Keychain — "
                "run `claude` and log in with your subscription first"
            )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ModelLoginError("Claude Keychain entry is not valid JSON") from exc
    else:
        raise ModelLoginError(
            "no Claude Code credentials at ~/.claude/.credentials.json — "
            "run `claude` and log in with your subscription first"
        )
    if not isinstance(data, dict):
        raise ModelLoginError("Claude credentials file is not a JSON object")
    return data


def validate_claude_creds(data: dict) -> None:
    oauth = data.get("claudeAiOauth")
    if not (isinstance(oauth, dict) and oauth.get("accessToken")):
        raise ModelLoginError("Claude credentials missing claudeAiOauth.accessToken")


def claude_creds_summary(data: dict) -> dict:
    oauth = data.get("claudeAiOauth") or {}
    summary: dict = {
        "provider": "claude (Claude Code)",
        "subscription": oauth.get("subscriptionType"),
        "scopes": oauth.get("scopes"),
        "has_refresh_token": bool(oauth.get("refreshToken")),
    }
    exp = oauth.get("expiresAt")
    if isinstance(exp, (int, float)):
        summary["access_token_expires"] = _iso(int(exp / 1000))  # Claude stores epoch milliseconds
    return {k: v for k, v in summary.items() if v is not None}


def build_claude_injection_command(*, creds_json: str) -> str:
    """A single POSIX-sh command that writes Claude Code creds into ``$HOME/.claude``, 0600."""
    return "\n".join(
        [
            "set -e",
            'CD="$HOME/.claude"',
            'mkdir -p "$CD"',
            "umask 077",
            f"printf %s '{b64(creds_json)}' | base64 -d > \"$CD/.credentials.json\"",
            'chmod 600 "$CD/.credentials.json"',
            f'echo "{CLAUDE_INJECT_MARKER} $CD"',
        ]
    )
