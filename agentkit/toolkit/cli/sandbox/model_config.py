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

"""Model and runtime configuration helpers for sandbox CLI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from typing import Optional

MODEL_NAME_ENV_KEYS = ("OPENCODE_MODEL", "CODEX_MODEL", "ANTHROPIC_MODEL")
MODEL_API_KEY_ENV_KEYS = (
    "OPENCODE_API_KEY",
    "CODEX_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
)
MODEL_BASE_URL_ENV_KEYS = (
    "OPENCODE_BASE_URL",
    "CODEX_BASE_URL",
    "MODEL_BASE_URL",
)
ANTHROPIC_BASE_URL_ENV_KEYS = ("ANTHROPIC_BASE_URL",)
MODEL_API_KEY_ENV = "MODEL_API_KEY"
MODEL_PROVIDER_ENV = "AGENTKIT_SANDBOX_MODEL_PROVIDER"

CODE_ENV_HOME = "/home/gem"
CODE_ENV_CODEX_HOME = "/home/gem/.codex"
CODEX_CONFIG_TOML_ENV = "CODEX_CONFIG_TOML"
CODEX_MODEL_CATALOG_JSON_ENV = "CODEX_MODEL_CATALOG_JSON"
CODEX_MODEL_CATALOG_PATH = f"{CODE_ENV_CODEX_HOME}/model-catalog.json"
# Provider ids reserved by Codex itself. Generated custom providers with these names are renamed
# so user-supplied API-key providers do not collide with Codex built-ins.
CODEX_RESERVED_MODEL_PROVIDER_IDS = {"openai"}
CODEX_LOGIN_MODEL_PROVIDER_ID = "codex_login"
# codex's ChatGPT-subscription endpoint - the default base_url for a codex_login provider when
# the caller does not pass --model-base-url (e.g. a regional proxy in front of OpenAI).
CODEX_CHATGPT_BASE_URL = "https://chatgpt.com/backend-api/codex"
# Default model for a codex_login provider when --model-name is omitted.
DEFAULT_CODEX_LOGIN_MODEL = "gpt-5.5"


class ModelProviderType(str, Enum):
    MODEL_SQUARE = "model_square"
    CODING_PLAN = "coding_plan"
    AGENT_PLAN = "agent_plan"
    BYTEPLUS_MODEL_SQUARE = "byteplus_model_square"
    BYTEPLUS_CODING_PLAN = "byteplus_coding_plan"


@dataclass(frozen=True)
class ModelSpec:
    supports_reasoning_summaries: bool
    context_window: int


@dataclass(frozen=True)
class ModelProviderConfig:
    model_base_url: str
    anthropic_base_url: str
    default_model_name: str
    models: dict[str, ModelSpec]


DEFAULT_MODEL_CONTEXT_WINDOW = 1000000
LIMITED_MODEL_CONTEXT_WINDOW = 200000
DEFAULT_MODEL_PROVIDER = ModelProviderType.MODEL_SQUARE.value
BYTEPLUS_DEFAULT_MODEL_PROVIDER = ModelProviderType.BYTEPLUS_MODEL_SQUARE.value

MODEL_PROVIDER_CONFIGS: dict[str, ModelProviderConfig] = {
    ModelProviderType.MODEL_SQUARE.value: ModelProviderConfig(
        model_base_url="https://ark.cn-beijing.volces.com/api/v3",
        anthropic_base_url="https://ark.cn-beijing.volces.com/api/compatible",
        default_model_name="deepseek-v4-flash-260425",
        models={
            "doubao-seed-2-0-pro-260215": ModelSpec(
                supports_reasoning_summaries=True,
                context_window=LIMITED_MODEL_CONTEXT_WINDOW,
            ),
            "deepseek-v4-flash-260425": ModelSpec(
                supports_reasoning_summaries=True,
                context_window=DEFAULT_MODEL_CONTEXT_WINDOW,
            ),
            "deepseek-v4-pro-260425": ModelSpec(
                supports_reasoning_summaries=True,
                context_window=DEFAULT_MODEL_CONTEXT_WINDOW,
            ),
        },
    ),
    ModelProviderType.CODING_PLAN.value: ModelProviderConfig(
        model_base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
        anthropic_base_url="https://ark.cn-beijing.volces.com/api/coding",
        default_model_name="deepseek-v4-flash",
        models={
            "doubao-seed-2.0-pro": ModelSpec(
                supports_reasoning_summaries=True,
                context_window=LIMITED_MODEL_CONTEXT_WINDOW,
            ),
            "deepseek-v4-flash": ModelSpec(
                supports_reasoning_summaries=True,
                context_window=DEFAULT_MODEL_CONTEXT_WINDOW,
            ),
            "deepseek-v4-pro": ModelSpec(
                supports_reasoning_summaries=True,
                context_window=DEFAULT_MODEL_CONTEXT_WINDOW,
            ),
        },
    ),
    ModelProviderType.AGENT_PLAN.value: ModelProviderConfig(
        model_base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
        anthropic_base_url="https://ark.cn-beijing.volces.com/api/plan",
        default_model_name="deepseek-v4-flash",
        models={
            "doubao-seed-2.0-pro": ModelSpec(
                supports_reasoning_summaries=True,
                context_window=LIMITED_MODEL_CONTEXT_WINDOW,
            ),
            "deepseek-v4-flash": ModelSpec(
                supports_reasoning_summaries=True,
                context_window=DEFAULT_MODEL_CONTEXT_WINDOW,
            ),
            "deepseek-v4-pro": ModelSpec(
                supports_reasoning_summaries=True,
                context_window=DEFAULT_MODEL_CONTEXT_WINDOW,
            ),
        },
    ),
    ModelProviderType.BYTEPLUS_MODEL_SQUARE.value: ModelProviderConfig(
        model_base_url="https://ark.ap-southeast.bytepluses.com/api/v3",
        anthropic_base_url="https://ark.ap-southeast.bytepluses.com/api/compatible",
        default_model_name="deepseek-v4-flash-260425",
        models={
            "doubao-seed-2-0-pro-260215": ModelSpec(
                supports_reasoning_summaries=True,
                context_window=LIMITED_MODEL_CONTEXT_WINDOW,
            ),
            "deepseek-v4-flash-260425": ModelSpec(
                supports_reasoning_summaries=True,
                context_window=DEFAULT_MODEL_CONTEXT_WINDOW,
            ),
            "deepseek-v4-pro-260425": ModelSpec(
                supports_reasoning_summaries=True,
                context_window=DEFAULT_MODEL_CONTEXT_WINDOW,
            ),
        },
    ),
    ModelProviderType.BYTEPLUS_CODING_PLAN.value: ModelProviderConfig(
        model_base_url="https://ark.ap-southeast.bytepluses.com/api/coding/v3",
        anthropic_base_url="https://ark.ap-southeast.bytepluses.com/api/coding",
        default_model_name="dola-seed-2.0-pro",
        models={
            "dola-seed-2.0-pro": ModelSpec(
                supports_reasoning_summaries=True,
                context_window=LIMITED_MODEL_CONTEXT_WINDOW,
            ),
            "dola-seed-2.0-lite": ModelSpec(
                supports_reasoning_summaries=True,
                context_window=LIMITED_MODEL_CONTEXT_WINDOW,
            ),
            "dola-seed-2.0-code": ModelSpec(
                supports_reasoning_summaries=True,
                context_window=LIMITED_MODEL_CONTEXT_WINDOW,
            ),
        },
    ),
}

DEFAULT_MODEL_NAME = MODEL_PROVIDER_CONFIGS[DEFAULT_MODEL_PROVIDER].default_model_name
DEFAULT_MODEL_NAME_LIST = tuple(
    dict.fromkeys(
        (
            DEFAULT_MODEL_NAME,
            *MODEL_PROVIDER_CONFIGS[DEFAULT_MODEL_PROVIDER].models,
        )
    )
)
DEFAULT_MODEL_BASE_URL = MODEL_PROVIDER_CONFIGS[DEFAULT_MODEL_PROVIDER].model_base_url
DEFAULT_ANTHROPIC_BASE_URL = MODEL_PROVIDER_CONFIGS[
    DEFAULT_MODEL_PROVIDER
].anthropic_base_url
BUILTIN_MODEL_BASE_URL_PROVIDERS = {
    config.model_base_url: provider
    for provider, config in MODEL_PROVIDER_CONFIGS.items()
}
BUILTIN_MODEL_BASE_URLS = tuple(
    BUILTIN_MODEL_BASE_URL_PROVIDERS,
)


def _model_provider_value(
    model_provider: str | ModelProviderType | None,
) -> Optional[str]:
    if isinstance(model_provider, ModelProviderType):
        return model_provider.value
    return model_provider


def default_model_provider() -> str:
    try:
        from agentkit.platform.provider import (
            CloudProvider,
            normalize_cloud_provider,
            read_cloud_provider_from_env,
        )

        if normalize_cloud_provider(read_cloud_provider_from_env()) == (
            CloudProvider.BYTEPLUS
        ):
            return BYTEPLUS_DEFAULT_MODEL_PROVIDER
    except Exception:
        pass
    return DEFAULT_MODEL_PROVIDER


def normalize_model_provider(
    model_provider: str | ModelProviderType | None,
) -> str:
    resolved = (
        _model_provider_value(model_provider) or default_model_provider()
    ).strip()
    return resolved


def normalize_optional_model_provider(
    model_provider: str | ModelProviderType | None,
) -> str | None:
    resolved = (_model_provider_value(model_provider) or "").strip()
    return resolved or None


def normalize_model_base_url(model_base_url: Optional[str]) -> str | None:
    resolved = (model_base_url or "").strip()
    return resolved or None


def is_builtin_model_base_url(model_base_url: Optional[str]) -> bool:
    resolved = normalize_model_base_url(model_base_url)
    return bool(resolved and resolved in BUILTIN_MODEL_BASE_URLS)


def infer_model_provider_from_base_url(model_base_url: Optional[str]) -> str | None:
    resolved = normalize_model_base_url(model_base_url)
    if not resolved:
        return None
    return BUILTIN_MODEL_BASE_URL_PROVIDERS.get(resolved)


def validate_model_provider_base_url(
    *,
    model_provider: str | ModelProviderType | None,
    model_base_url: Optional[str],
    model_provider_was_provided: Optional[bool] = None,
    model_base_url_was_provided: Optional[bool] = None,
) -> None:
    resolved_model_provider = normalize_optional_model_provider(model_provider)
    resolved_model_base_url = normalize_model_base_url(model_base_url)
    provider_was_provided = (
        bool(resolved_model_provider)
        if model_provider_was_provided is None
        else model_provider_was_provided
    )
    base_url_was_provided = (
        bool(resolved_model_base_url)
        if model_base_url_was_provided is None
        else model_base_url_was_provided
    )

    if (
        base_url_was_provided
        and resolved_model_base_url
        and not is_builtin_model_base_url(resolved_model_base_url)
        and not provider_was_provided
    ):
        raise ValueError(
            "--model-base-url requires --model-provider for non-Ark base URLs"
        )


def get_model_provider_config_if_known(
    model_provider: str | ModelProviderType | None,
) -> ModelProviderConfig | None:
    return MODEL_PROVIDER_CONFIGS.get(normalize_model_provider(model_provider))


def get_model_provider_config(
    model_provider: str | ModelProviderType | None,
) -> ModelProviderConfig:
    resolved_provider = normalize_model_provider(model_provider)
    config = MODEL_PROVIDER_CONFIGS.get(resolved_provider)
    if config is None:
        raise ValueError(
            f"--model-provider has no built-in configuration: {resolved_provider}"
        )
    return config


def model_provider_from_env_value(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    resolved = value.strip()
    if not resolved:
        return None
    return resolved


def resolve_model_name(
    model_name: Optional[str],
    model_provider: str | ModelProviderType | None,
) -> str:
    resolved_provider = normalize_model_provider(model_provider)
    config = MODEL_PROVIDER_CONFIGS.get(resolved_provider)
    resolved_model_name = (model_name or "").strip()
    if resolved_model_name:
        return resolved_model_name
    if provider_requires_openai_auth(resolved_provider):
        return DEFAULT_CODEX_LOGIN_MODEL
    if config:
        return config.default_model_name
    return DEFAULT_MODEL_NAME


def resolve_model_base_urls(
    *,
    model_provider: str | ModelProviderType | None,
    model_base_url: Optional[str] = None,
) -> tuple[str | None, str | None]:
    resolved_model_base_url = normalize_model_base_url(model_base_url)
    if resolved_model_base_url:
        return resolved_model_base_url, resolved_model_base_url

    config = get_model_provider_config_if_known(model_provider)
    if not config:
        return None, None
    return config.model_base_url, config.anthropic_base_url


def should_emit_codex_model_config(
    *,
    model_provider: str | ModelProviderType | None,
    model_base_url: Optional[str] = None,
) -> bool:
    return True


def should_emit_codex_model_catalog(
    model_provider: str | ModelProviderType | None,
) -> bool:
    resolved_provider = normalize_model_provider(model_provider)
    return resolved_provider in MODEL_PROVIDER_CONFIGS


def codex_model_provider_id(
    model_provider: str | ModelProviderType | None,
) -> str:
    resolved_provider = normalize_model_provider(model_provider)
    if resolved_provider in MODEL_PROVIDER_CONFIGS:
        return resolved_provider
    if resolved_provider in CODEX_RESERVED_MODEL_PROVIDER_IDS:
        return f"{resolved_provider}-custom"
    return resolved_provider


def provider_requires_openai_auth(
    model_provider: str | ModelProviderType | None,
) -> bool:
    """Whether this provider authenticates with the user's ChatGPT OAuth login (auth.json)
    instead of an API key. True for ``codex_login`` - codex then uses the token injected by
    ``agentkit sandbox codex-login`` and the config carries no ``env_key``."""
    return normalize_model_provider(model_provider) == CODEX_LOGIN_MODEL_PROVIDER_ID


def _toml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def build_codex_config_toml(
    model_name: str,
    model_provider: str | ModelProviderType | None = None,
    model_base_url: Optional[str] = None,
) -> str:
    resolved_provider = normalize_model_provider(model_provider)
    requires_openai_auth = provider_requires_openai_auth(resolved_provider)
    config = get_model_provider_config_if_known(resolved_provider)
    resolved_model_base_url = normalize_model_base_url(model_base_url)
    if requires_openai_auth:
        provider_base_url = resolved_model_base_url or CODEX_CHATGPT_BASE_URL
        resolved_model_name = (model_name or "").strip() or DEFAULT_CODEX_LOGIN_MODEL
    else:
        provider_base_url = resolved_model_base_url or (
            config.model_base_url
            if config
            else MODEL_PROVIDER_CONFIGS[default_model_provider()].model_base_url
        )
        resolved_model_name = resolve_model_name(model_name, resolved_provider)
    resolved_codex_provider = codex_model_provider_id(resolved_provider)
    quoted_model = _toml_quote(resolved_model_name)
    lines = [
        f"model_provider = {_toml_quote(resolved_codex_provider)}",
        f"model = {quoted_model}",
        f"review_model = {quoted_model}",
        'approval_policy = "never"',
        'sandbox_mode = "danger-full-access"',
        'model_reasoning_effort = "medium"',
        'personality = "pragmatic"',
        "check_for_update_on_startup = false",
        'web_search = "disabled"',
    ]
    if should_emit_codex_model_catalog(resolved_provider):
        lines.append(f"model_catalog_json = {_toml_quote(CODEX_MODEL_CATALOG_PATH)}")
    lines.extend(
        [
            'developer_instructions = """',
            (
                "When the user asks for simple browser operation tasks, "
                "you can use xdg-open to complete them."
            ),
            '"""',
            "",
            f"[model_providers.{resolved_codex_provider}]",
            f"name = {_toml_quote(resolved_codex_provider)}",
            f"base_url = {_toml_quote(provider_base_url)}",
            'wire_api = "responses"',
            # OAuth (ChatGPT login) providers carry no API key - codex uses the injected
            # auth.json via `requires_openai_auth`; all others use the CODEX_API_KEY env.
            ("requires_openai_auth = true" if requires_openai_auth else 'env_key = "CODEX_API_KEY"'),
            "",
            "[tui]",
            "show_tooltips = false",
            "",
            '[projects."/home/gem"]',
            'trust_level = "trusted"',
            "",
            "[mcp_servers.browser-use]",
            'url = "http://localhost:8100/mcp"',
            "",
        ]
    )
    return "\n".join(lines)


def _codex_config_toml_for_exec_env(
    *,
    model_name: Optional[str],
    model_provider: str | ModelProviderType | None = None,
    model_base_url: Optional[str] = None,
) -> str | None:
    resolved_model_base_url = normalize_model_base_url(model_base_url)
    effective_model_provider = model_provider or infer_model_provider_from_base_url(
        resolved_model_base_url
    )
    resolved_model_provider = normalize_optional_model_provider(
        effective_model_provider
    )
    resolved_model_name = (
        resolve_model_name(model_name, resolved_model_provider)
        if resolved_model_provider
        else (model_name or "").strip()
    )
    if not resolved_model_name or not should_emit_codex_model_config(
        model_provider=resolved_model_provider,
        model_base_url=resolved_model_base_url,
    ):
        return None
    return build_codex_config_toml(
        resolved_model_name,
        resolved_model_provider,
        resolved_model_base_url,
    )


def build_codex_hot_update_env(
    *,
    model_name: Optional[str] = None,
    model_api_key: Optional[str] = None,
    model_provider: str | ModelProviderType | None = None,
    model_base_url: Optional[str] = None,
    model_api_key_was_provided: bool = False,
    model_name_was_provided: bool = False,
    model_base_url_was_provided: bool = False,
) -> dict[str, str]:
    """Build the env payload for an existing CodeEnv Codex hot update."""

    env: dict[str, str] = {}
    if model_api_key_was_provided:
        value = model_api_key or ""
        env["CODEX_API_KEY"] = value
        env["ARK_API_KEY"] = value
        env["OPENAI_API_KEY"] = value
    if model_name_was_provided:
        env["CODEX_MODEL"] = model_name or ""
    if model_base_url_was_provided:
        env["CODEX_BASE_URL"] = normalize_model_base_url(model_base_url) or ""

    config_toml = _codex_config_toml_for_exec_env(
        model_name=model_name,
        model_provider=model_provider,
        model_base_url=model_base_url,
    )
    if config_toml:
        env[CODEX_CONFIG_TOML_ENV] = config_toml
    return env


def build_codex_hot_update_command() -> str:
    """Shell command that updates Codex env and config inside an existing sandbox."""

    return '''set -euo pipefail

ENV_FILE=/home/gem/.env
BASHRC=/home/gem/.bashrc
TARGET=/home/gem/.codex/config.toml

REQ_CODEX_API_KEY="${CODEX_API_KEY:-}"
REQ_ARK_API_KEY="${ARK_API_KEY:-}"
REQ_OPENAI_API_KEY="${OPENAI_API_KEY:-}"
REQ_CODEX_MODEL="${CODEX_MODEL:-}"
REQ_CODEX_BASE_URL="${CODEX_BASE_URL:-}"
REQ_CODEX_CONFIG_TOML="${CODEX_CONFIG_TOML:-}"

mkdir -p /home/gem/.codex

if [ -f "$ENV_FILE" ]; then
  . "$ENV_FILE"
fi

if [ -n "$REQ_CODEX_API_KEY" ]; then
  CODEX_API_KEY="$REQ_CODEX_API_KEY"
elif [ -n "$REQ_ARK_API_KEY" ]; then
  CODEX_API_KEY="$REQ_ARK_API_KEY"
elif [ -n "$REQ_OPENAI_API_KEY" ]; then
  CODEX_API_KEY="$REQ_OPENAI_API_KEY"
fi

if [ -n "$REQ_CODEX_MODEL" ]; then
  CODEX_MODEL="$REQ_CODEX_MODEL"
fi

if [ -n "$REQ_CODEX_BASE_URL" ]; then
  CODEX_BASE_URL="$REQ_CODEX_BASE_URL"
fi

CODEX_MODEL="${CODEX_MODEL:-deepseek-v4-flash-260425}"
CODEX_BASE_URL="${CODEX_BASE_URL:-https://ark.cn-beijing.volces.com/api/v3}"

: >"$ENV_FILE"

for key in CODEX_API_KEY CODEX_MODEL CODEX_BASE_URL; do
  value="${!key:-}"
  if [ -n "$value" ]; then
    printf "export %s=%q\\n" "$key" "$value" >>"$ENV_FILE"
  fi
done

chmod 600 "$ENV_FILE"
chown gem:gem "$ENV_FILE" 2>/dev/null || true

source_line="[ -f \"\\$HOME/.env\" ] && . \"\\$HOME/.env\""
touch "$BASHRC"
if ! grep -qxF "$source_line" "$BASHRC"; then
  printf "\n%s\n" "$source_line" >>"$BASHRC"
fi
chown gem:gem "$BASHRC" 2>/dev/null || true

export CODEX_API_KEY CODEX_MODEL CODEX_BASE_URL REQ_CODEX_CONFIG_TOML

python3 - <<"PY"
import json
import os
from pathlib import Path

target = Path("/home/gem/.codex/config.toml")
model = os.environ["CODEX_MODEL"]
base_url = os.environ["CODEX_BASE_URL"]
provided_config = os.environ.get("REQ_CODEX_CONFIG_TOML", "")

if provided_config:
    config = provided_config
else:
    config = f"""model_provider = "codex"
model = {json.dumps(model)}
review_model = {json.dumps(model)}
approval_policy = "never"
sandbox_mode = "danger-full-access"
model_reasoning_effort = "medium"
personality = "pragmatic"

[model_providers.codex]
name = "codex"
base_url = {json.dumps(base_url)}
wire_api = "responses"
env_key = "CODEX_API_KEY"
"""

tmp = target.with_name("config.toml.tmp")
tmp.write_text(config)
tmp.replace(target)
PY

chmod 600 "$TARGET"
chown gem:gem "$TARGET" 2>/dev/null || true

echo "status=updated"'''


def _reasoning_levels() -> list[dict[str, str]]:
    return [
        {
            "effort": "low",
            "description": "Fast responses with lighter reasoning",
        },
        {
            "effort": "medium",
            "description": "Balances speed and reasoning depth",
        },
        {
            "effort": "high",
            "description": "Greater reasoning depth",
        },
    ]


def _build_model_catalog_item(model_name: str, spec: ModelSpec) -> dict:
    return {
        "slug": model_name,
        "display_name": model_name,
        "supported_reasoning_levels": _reasoning_levels(),
        "max_context_window": spec.context_window,
        "shell_type": "shell_command",
        "visibility": "list",
        "supported_in_api": True,
        "priority": 100,
        "base_instructions": "",
        "supports_reasoning_summaries": spec.supports_reasoning_summaries,
        "support_verbosity": False,
        "truncation_policy": {"mode": "tokens", "limit": 10000},
        "supports_parallel_tool_calls": False,
        "experimental_supported_tools": [],
    }


def infer_model_spec(model_name: str) -> ModelSpec:
    normalized_model_name = model_name.strip().lower()
    if normalized_model_name == "glm-5.2" or normalized_model_name.startswith(
        "deepseek-v4"
    ):
        context_window = DEFAULT_MODEL_CONTEXT_WINDOW
    else:
        context_window = LIMITED_MODEL_CONTEXT_WINDOW

    return ModelSpec(
        supports_reasoning_summaries=True,
        context_window=context_window,
    )


def model_catalog_context_window(
    model_name: str,
    model_provider: str | ModelProviderType | None = None,
) -> int:
    config = get_model_provider_config_if_known(model_provider)
    spec = config.models.get(model_name) if config else None
    if spec:
        return spec.context_window
    return infer_model_spec(model_name).context_window


def build_codex_model_catalog_json(
    model_name: str,
    model_provider: str | ModelProviderType | None = None,
) -> str:
    resolved_provider = normalize_model_provider(model_provider)
    config = get_model_provider_config(resolved_provider)
    resolved_model_name = resolve_model_name(model_name, resolved_provider)
    deduped_model_names = list(dict.fromkeys((resolved_model_name, *config.models)))
    payload = {
        "models": [
            _build_model_catalog_item(
                name,
                config.models.get(name) or infer_model_spec(name),
            )
            for name in deduped_model_names
        ]
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
