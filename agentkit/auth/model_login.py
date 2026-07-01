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

"""Read a local model-subscription login and inject it into a sandbox.

Codex/ChatGPT and Claude Code log in over OAuth and store the token in a local file
($CODEX_HOME/auth.json for codex, ~/.claude/.credentials.json or the macOS Keychain for
Claude). This reads that file and writes the token to the same path inside the sandbox,
so the sandbox's codex runs on the user's subscription. codex refreshes the token itself.

Only the OAuth token is injected. The same file can also hold a long-lived API key
(codex: OPENAI_API_KEY); that is stripped before injection and an api-key-only file is
rejected, so a long-lived key never reaches the sandbox. See sanitize_*_for_injection.

The sandbox config.toml is not touched; select the subscription at exec with
`codex exec -c model_provider=openai`. Stdlib only; the sandbox transport lives in
sandbox/cli_model_login.py.
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Callable, Optional

# Codex / ChatGPT facts
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # codex CLI's public OAuth client (the id_token aud)
CODEX_OAUTH_NAMESPACE = "https://api.openai.com/auth"  # claim namespace holding the ChatGPT plan
CODEX_BUILTIN_PROVIDER = "openai"  # codex's reserved built-in provider that uses ChatGPT auth
CODEX_INJECT_MARKER = "AGENTKIT_CODEX_INJECTED"
CLAUDE_INJECT_MARKER = "AGENTKIT_CLAUDE_INJECTED"
CLAUDE_KEYCHAIN_SERVICE = "Claude Code-credentials"
# codex auth.json OAuth token fields we carry into the sandbox (everything else is dropped).
CODEX_OAUTH_TOKEN_FIELDS = ("id_token", "access_token", "refresh_token", "account_id")

PROVIDERS = ("codex", "claude")


class ModelLoginError(RuntimeError):
    """A user-facing failure resolving or injecting a model subscription token."""


# small helpers
def b64(data: str | bytes) -> str:
    """Standard base64 (single line, no newlines) - safe to embed in a shell ``printf``."""
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


# Codex (ChatGPT)
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


def codex_has_oauth(data: dict) -> bool:
    tokens = data.get("tokens")
    return isinstance(tokens, dict) and bool(tokens.get("id_token") or tokens.get("access_token"))


def validate_codex_auth(data: dict) -> None:
    """A usable codex auth has ChatGPT OAuth tokens or an API key. Only the OAuth path is
    injected; api-key-only auth is rejected in sanitize_codex_auth_for_injection."""
    if not (codex_has_oauth(data) or data.get("OPENAI_API_KEY")):
        raise ModelLoginError(
            "codex auth.json has neither ChatGPT tokens nor an API key; run `codex login` first"
        )


def sanitize_codex_auth_for_injection(data: dict) -> dict:
    """Return only the OAuth token material to inject, dropping any API key.

    codex keeps a long-lived OPENAI_API_KEY in the same auth.json as the OAuth tokens.
    This returns just the tokens with OPENAI_API_KEY set to null; an api-key-only auth
    (no OAuth login) is rejected.
    """
    if not codex_has_oauth(data):
        raise ModelLoginError(
            "no ChatGPT OAuth login in your codex auth. An API key is not injected into the "
            "sandbox; run `codex login` (ChatGPT SSO) first."
        )
    tokens = data.get("tokens") or {}
    safe_tokens = {k: tokens[k] for k in CODEX_OAUTH_TOKEN_FIELDS if tokens.get(k) is not None}
    return {
        "OPENAI_API_KEY": None,  # do not inject the API key
        "auth_mode": "chatgpt",
        "tokens": safe_tokens,
        "last_refresh": data.get("last_refresh"),
    }


def codex_auth_summary(data: dict) -> dict:
    """A redacted, secret-free summary (provider, plan, account, expiry) for display."""
    tokens = data.get("tokens") or {}
    summary: dict = {
        "provider": "codex (ChatGPT)",
        "auth_mode": data.get("auth_mode") or ("chatgpt" if tokens else "apikey"),
        "has_oauth_login": codex_has_oauth(data),
        "has_local_api_key": bool(data.get("OPENAI_API_KEY")),  # present locally; NOT injected
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
            f"`{codex_bin}` not found on PATH - install the Codex CLI and run `codex login`, "
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
                f"no codex auth at {path} - run `codex login` (or pass --auth-file / drop --no-login)"
            )
        (login_runner or run_codex_login)(codex_home=home, timeout=login_timeout)
        if not path.exists():
            raise ModelLoginError(f"`codex login` finished but {path} is still missing")
    data = read_codex_auth(path)
    validate_codex_auth(data)
    return path, data


def build_codex_injection_command(*, auth_data: dict) -> str:
    """POSIX-sh command that writes the sanitized auth.json (OAuth only, API key stripped) into
    ${CODEX_HOME:-$HOME/.codex} at 0600 and prints a marker. auth_data is sanitized here so the
    transport cannot inject an API key."""
    payload = json.dumps(sanitize_codex_auth_for_injection(auth_data), ensure_ascii=False)
    return "\n".join(
        [
            "set -e",
            'CH="${CODEX_HOME:-$HOME/.codex}"',
            'mkdir -p "$CH"',
            "umask 077",
            f"printf %s '{b64(payload)}' | base64 -d > \"$CH/auth.json\"",
            'chmod 600 "$CH/auth.json"',
            f'echo "{CODEX_INJECT_MARKER} $CH"',
        ]
    )


# Claude Code
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
                "no Claude Code credentials in ~/.claude/.credentials.json or the macOS Keychain - "
                "run `claude` and log in with your subscription first"
            )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ModelLoginError("Claude Keychain entry is not valid JSON") from exc
    else:
        raise ModelLoginError(
            "no Claude Code credentials at ~/.claude/.credentials.json - "
            "run `claude` and log in with your subscription first"
        )
    if not isinstance(data, dict):
        raise ModelLoginError("Claude credentials file is not a JSON object")
    return data


def validate_claude_creds(data: dict) -> None:
    oauth = data.get("claudeAiOauth")
    if not (isinstance(oauth, dict) and oauth.get("accessToken")):
        raise ModelLoginError("Claude credentials missing claudeAiOauth.accessToken")


def sanitize_claude_creds_for_injection(data: dict) -> dict:
    """Return only the claudeAiOauth object; other top-level fields (e.g. a stored API key)
    are dropped."""
    oauth = data.get("claudeAiOauth")
    if not (isinstance(oauth, dict) and oauth.get("accessToken")):
        raise ModelLoginError(
            "no Claude OAuth login found. An API key is not injected into the sandbox; "
            "run `claude` and log in with your subscription first."
        )
    return {"claudeAiOauth": oauth}


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


def build_claude_injection_command(*, creds_data: dict) -> str:
    """POSIX-sh command that writes the sanitized Claude creds into
    $HOME/.claude/.credentials.json at 0600."""
    payload = json.dumps(sanitize_claude_creds_for_injection(creds_data), ensure_ascii=False)
    return "\n".join(
        [
            "set -e",
            'CD="$HOME/.claude"',
            'mkdir -p "$CD"',
            "umask 077",
            f"printf %s '{b64(payload)}' | base64 -d > \"$CD/.credentials.json\"",
            'chmod 600 "$CD/.credentials.json"',
            f'echo "{CLAUDE_INJECT_MARKER} $CD"',
        ]
    )
