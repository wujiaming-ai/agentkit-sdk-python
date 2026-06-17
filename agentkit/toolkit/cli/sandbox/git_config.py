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

"""Git config helpers for sandbox exec and shell commands."""

from __future__ import annotations

import configparser
import json
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping, Optional

from agentkit.toolkit.cli.sandbox.cli_file import _exec_shell_command


LOCAL_GIT_CONFIG_VALUE = "local"


def _non_empty_string(value: object) -> Optional[str]:
    if value is None:
        return None
    resolved = str(value).strip()
    return resolved or None


def _extract_git_config_values(data: Mapping[str, Any]) -> tuple[str, str]:
    name = _non_empty_string(data.get("user.name"))
    email = _non_empty_string(data.get("user.email"))

    user_section = data.get("user")
    if isinstance(user_section, Mapping):
        name = name or _non_empty_string(user_section.get("name"))
        email = email or _non_empty_string(user_section.get("email"))

    if not name or not email:
        raise ValueError("Failed to resolve user.name and user.email from git config")

    return name, email


def _read_local_git_config_value(key: str) -> str:
    try:
        result = subprocess.run(
            ["git", "config", "--get", key],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ValueError("git command not found") from exc

    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        raise ValueError(f"Failed to resolve {key} from local git config")
    return value


def _read_local_git_config() -> tuple[str, str]:
    if shutil.which("git") is None:
        raise ValueError("git command not found")

    try:
        result = subprocess.run(
            ["git", "config", "--list"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ValueError("git command not found") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        if message:
            raise ValueError(f"Failed to read local git config: {message}")
        raise ValueError("Failed to read local git config")
    if not result.stdout.strip():
        raise ValueError("Local git config is empty")

    return (
        _read_local_git_config_value("user.name"),
        _read_local_git_config_value("user.email"),
    )


def _read_json_git_config(path: Path) -> tuple[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Failed to parse git config JSON file: {path}") from exc
    if not isinstance(data, Mapping):
        raise ValueError("Git config JSON file must contain an object")
    return _extract_git_config_values(data)


def _read_toml_git_config(path: Path) -> tuple[str, str]:
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python 3.10 compatibility.
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError as exc:
            raise ValueError(
                "TOML git config requires Python 3.11+ or the tomli package"
            ) from exc

    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except Exception as exc:
        raise ValueError(f"Failed to parse git config TOML file: {path}") from exc
    return _extract_git_config_values(data)


def _read_ini_git_config(path: Path) -> tuple[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        content = path.read_text(encoding="utf-8")
        try:
            parser.read_string(content)
        except configparser.MissingSectionHeaderError:
            parser.read_string(f"[__root__]\n{content}")
    except Exception as exc:
        raise ValueError(f"Failed to parse git config INI file: {path}") from exc

    data: dict[str, object] = dict(parser.defaults())
    if parser.has_section("__root__"):
        data.update(dict(parser.items("__root__")))
    if parser.has_section("user"):
        data["user"] = dict(parser.items("user"))

    return _extract_git_config_values(data)


def resolve_git_config(git_config: Optional[str]) -> Optional[tuple[str, str]]:
    resolved = (git_config or "").strip()
    if not resolved:
        return None

    if resolved.lower() == LOCAL_GIT_CONFIG_VALUE:
        return _read_local_git_config()

    path = Path(resolved).expanduser()
    if not path.exists():
        raise ValueError(f"Git config file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Git config path is not a file: {path}")

    suffix = path.suffix.lower()
    if suffix == ".json":
        return _read_json_git_config(path)
    if suffix == ".toml":
        return _read_toml_git_config(path)
    return _read_ini_git_config(path)


def build_remote_git_config_command(user_name: str, user_email: str) -> str:
    return (
        f"git config --global user.name {shlex.quote(user_name)}; "
        f"git config --global user.email {shlex.quote(user_email)}"
    )


def apply_git_config_to_session(
    session: dict[str, object],
    git_config: Optional[str],
) -> None:
    resolved = resolve_git_config(git_config)
    if resolved is None:
        return

    user_name, user_email = resolved
    _exec_shell_command(
        session,
        build_remote_git_config_command(user_name, user_email),
    )
