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

"""AgentKit CLI - Invoke command implementation."""

from pathlib import Path
from typing import Optional, Any
import base64
import binascii
import json
import os
import typer
from typer.core import TyperGroup
from rich.console import Console
import time
import random
import uuid
from agentkit.toolkit.config import get_config
import logging
from urllib.parse import parse_qs, urlparse

# Note: Avoid importing heavy packages at the top to keep CLI startup fast
logger = logging.getLogger(__name__)
console = Console()


def _sanitize_headers_for_display(headers: dict) -> dict:
    if not isinstance(headers, dict):
        return {}
    redacted_keys = {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "x-auth-token",
        "api-key",
    }
    out: dict = {}
    for k, v in headers.items():
        key = str(k)
        if key.lower() in redacted_keys:
            out[key] = "******"
        else:
            out[key] = v
    return out


def _extract_text_chunks_from_langchain_event(event: dict) -> list[str]:
    """Extract incremental text chunks from LangChain message_to_dict-style events.

    Expected shape (example):
        {"type": "AIMessageChunk", "data": {"content": "今天", ...}}
    """
    if not isinstance(event, dict):
        return []

    event_type = event.get("type")
    data = event.get("data")
    if not isinstance(event_type, str) or not isinstance(data, dict):
        return []

    # Most common streaming types: AIMessageChunk / HumanMessageChunk / ToolMessageChunk
    if not (
        event_type.endswith("MessageChunk")
        or event_type in {"AIMessage", "HumanMessage", "ToolMessage"}
    ):
        return []

    content = data.get("content")
    if content is None:
        return []

    # content can be a string, or a multimodal list like:
    #   [{"type":"text","text":"..."}, ...]
    if isinstance(content, str):
        return [content] if content else []
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str) and item:
                chunks.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str) and text:
                    chunks.append(text)
        return chunks

    return []


def _extract_reasoning_chunks_from_langchain_event(event: dict) -> list[str]:
    """Extract incremental reasoning chunks from LangChain events.

    LangChain emit reasoning in:
        event['data']['additional_kwargs']['reasoning_content']
    while leaving event['data']['content'] empty.
    """
    if not isinstance(event, dict):
        return []

    event_type = event.get("type")
    data = event.get("data")
    if not isinstance(event_type, str) or not isinstance(data, dict):
        return []

    if not (
        event_type.endswith("MessageChunk")
        or event_type in {"AIMessage", "HumanMessage", "ToolMessage"}
    ):
        return []

    additional_kwargs = data.get("additional_kwargs")
    if not isinstance(additional_kwargs, dict):
        return []

    reasoning = additional_kwargs.get("reasoning_content")
    if isinstance(reasoning, str):
        return [reasoning] if reasoning else []
    return []


def _extract_text_chunks_from_adk_event(event: dict) -> list[str]:
    """Extract incremental text chunks from Google ADK/AgentKit streaming events."""
    if not isinstance(event, dict):
        return []

    parts: list[Any] = []
    if isinstance(event.get("parts"), list):
        parts = event.get("parts", [])
    elif isinstance(event.get("message"), dict):
        parts = event["message"].get("parts", [])
    elif isinstance(event.get("content"), dict):
        parts = event["content"].get("parts", [])
    elif isinstance(event.get("status"), dict):
        role = event["status"].get("message", {}).get("role")
        if role == "agent":
            parts = event["status"].get("message", {}).get("parts", [])

    if not isinstance(parts, list) or not parts:
        return []

    chunks: list[str] = []
    for part in parts:
        text: Optional[str] = None
        if isinstance(part, dict) and "text" in part:
            val = part.get("text")
            text = val if isinstance(val, str) else None
        elif isinstance(part, str):
            text = part
        if text:
            chunks.append(text)
    return chunks


def _normalize_stream_event(event: Any) -> Optional[dict]:
    """Normalize an event yielded by InvokeResult.stream() to a dict.

    - Runner normally yields dict (already JSON-decoded).
    - CLI keeps a fallback path for raw SSE strings ("data: {...}").
    """
    if isinstance(event, dict):
        return event
    if isinstance(event, str):
        s = event.strip()
        if not s.startswith("data: "):
            return None
        json_str = s[6:].strip()
        if not json_str:
            return None
        try:
            parsed = json.loads(json_str)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def build_standard_payload(message: Optional[str], payload: Optional[str]) -> dict:
    if message:
        return {"prompt": message}
    else:
        try:
            parsed = json.loads(payload) if isinstance(payload, str) else payload
            console.print(f"[blue]Using custom payload: {parsed}[/blue]")
            return parsed
        except json.JSONDecodeError as e:
            console.print(f"[red]Error: Invalid JSON payload: {e}[/red]")
            raise typer.Exit(1)


def build_a2a_payload(
    message: Optional[str], payload: Optional[str], headers: dict
) -> dict:
    parsed = None
    if payload:
        try:
            parsed = json.loads(payload) if isinstance(payload, str) else payload
        except json.JSONDecodeError:
            parsed = None

    if isinstance(parsed, dict) and parsed.get("jsonrpc"):
        console.print("[blue]Using provided JSON-RPC payload for A2A[/blue]")
        return parsed

    if message:
        text = message
    elif parsed is not None:
        text = json.dumps(parsed, ensure_ascii=False)
    else:
        text = payload if payload else ""

    a2a = {
        "jsonrpc": "2.0",
        "method": "message/stream",
        "params": {
            "message": {
                "role": "user",
                "messageId": str(uuid.uuid4()),
                "parts": [{"kind": "text", "text": text}],
            },
            "metadata": headers,
        },
        "id": random.randint(1, 999999),
    }
    return a2a


class InvokeGroup(TyperGroup):
    """Group for ``agentkit invoke`` that keeps the bare-message form working.

    ``invoke`` exposes named subcommands (``run``, ``harness``). When the first
    token is neither a known subcommand nor ``--help``, it is treated as the
    default ``run`` command's arguments, so ``agentkit invoke "hi"`` and
    ``agentkit invoke --runtime-id ...`` behave exactly as before.
    """

    default_cmd_name = "run"

    def parse_args(self, ctx, args):
        if args:
            first = args[0]
            if first != "--help" and first not in self.commands:
                args = [self.default_cmd_name] + args
        return super().parse_args(ctx, args)


invoke_app = typer.Typer(
    cls=InvokeGroup,
    name="invoke",
    help="Send a test request to a deployed Agent (default) or harness.",
    add_completion=False,
)


@invoke_app.command("run")
def invoke_command(
    config_file: Optional[Path] = typer.Option(
        None, "--config-file", help="Configuration file"
    ),
    message: str = typer.Argument(None, help="Simple message to send to agent"),
    payload: str = typer.Option(
        None, "--payload", "-p", help="JSON payload to send (advanced option)"
    ),
    headers: str = typer.Option(
        None, "--headers", "-h", help="JSON headers for request (advanced option)"
    ),
    runtime_id: str = typer.Option(
        None, "--runtime-id", "-r", help="Runtime ID for direct invocation"
    ),
    endpoint: str = typer.Option(
        None, "--endpoint", "-e", help="Endpoint URL for direct invocation"
    ),
    region: str = typer.Option(
        None, "--region", help="Region for Runtime lookup (used with --runtime-id)"
    ),
    a2a: bool = typer.Option(
        False,
        "--a2a",
        help="Force A2A JSON-RPC envelope (direct invocation mode only)",
    ),
    show_reasoning: bool = typer.Option(
        False,
        "--show-reasoning",
        help="Print LangChain reasoning_content (if present) during streaming",
    ),
    raw: bool = typer.Option(
        False,
        "--raw",
        help="Print raw streaming events (and raw JSON response) for debugging",
    ),
    apikey: str = typer.Option(
        None, "--apikey", "-ak", help="API key for authentication"
    ),
) -> Any:
    """Send a test request to deployed Agent.

    Examples:
        # Simple message
        agentkit invoke "What is the weather today?"

        # Custom payload
        agentkit invoke --payload '{"prompt": "What is the weather in Hangzhou?"}'

        # With custom headers
        agentkit invoke --payload '{"prompt": "What is the weather in Hangzhou?"}' --headers '{"user_id": "test123"}'
    """
    from agentkit.toolkit.executors import InvokeExecutor
    from agentkit.toolkit.cli.console_reporter import ConsoleReporter

    console.print("[cyan]Invoking agent...[/cyan]")

    # Validate parameters: message and payload cannot be provided simultaneously
    if message and payload:
        console.print(
            "[red]Error: Cannot specify both message and payload. Use either message or --payload.[/red]"
        )
        raise typer.Exit(1)
    # Validate parameters: must provide either message or payload
    if not message and not payload:
        console.print(
            "[red]Error: Must provide either a message or --payload option.[/red]"
        )
        raise typer.Exit(1)

    direct_mode = bool(runtime_id or endpoint)
    if direct_mode and config_file is not None:
        console.print(
            "[red]Error: --config-file cannot be used with --runtime-id/--endpoint.[/red]"
        )
        raise typer.Exit(1)
    if runtime_id and endpoint:
        console.print(
            "[red]Error: Cannot specify both --runtime-id and --endpoint.[/red]"
        )
        raise typer.Exit(1)
    if region and not runtime_id:
        console.print(
            "[red]Error: --region can only be used together with --runtime-id.[/red]"
        )
        raise typer.Exit(1)
    if a2a and not direct_mode:
        console.print(
            "[red]Error: --a2a can only be used with --runtime-id or --endpoint.[/red]"
        )
        raise typer.Exit(1)

    # Process headers
    default_headers = {
        "user_id": "agentkit_user",
        "session_id": "agentkit_sample_session",
    }
    final_headers = default_headers.copy()

    if headers:
        try:
            custom_headers = (
                json.loads(headers) if isinstance(headers, str) else headers
            )
        except json.JSONDecodeError as e:
            console.print(f"[red]Error: Invalid JSON headers: {e}[/red]")
            raise typer.Exit(1)
        if not isinstance(custom_headers, dict):
            console.print(
                '[red]Error: --headers must be a JSON object (e.g. \'{"user_id": "u1"}\').[/red]'
            )
            raise typer.Exit(1)
        final_headers.update(custom_headers)
        console.print(
            f"[blue]Using merged headers: {_sanitize_headers_for_display(final_headers)}[/blue]"
        )
    else:
        console.print(
            f"[blue]Using default headers: {_sanitize_headers_for_display(final_headers)}[/blue]"
        )

    if apikey:
        if runtime_id:
            console.print(
                "[red]Error: --apikey cannot be used together with --runtime-id. "
                "--runtime-id mode resolves the Runtime and infers its auth type automatically: "
                "for API Key (key_auth), omit auth flags and the CLI will fetch and inject the API key; "
                'for JWT/OAuth (custom_jwt), provide an Authorization header via --headers \'{"Authorization":"Bearer <token>"}\'. '
                "If you want to pass an API key manually, use --endpoint together with --apikey.[/red]"
            )
            raise typer.Exit(1)
        final_headers["Authorization"] = f"Bearer {apikey}"

    final_payload = build_standard_payload(message, payload)

    from agentkit.toolkit.context import ExecutionContext

    reporter = ConsoleReporter()
    ExecutionContext.set_reporter(reporter)

    executor = InvokeExecutor(reporter=reporter)

    if direct_mode:
        if endpoint and not apikey and not final_headers.get("Authorization"):
            console.print(
                "[red]Error: --endpoint requires --apikey or an Authorization header provided via --headers.[/red]"
            )
            raise typer.Exit(1)

        direct_common = {
            "agent_name": "direct_invoke",
            "entry_point": "agent.py",
            "launch_type": "cloud",
        }

        is_a2a_payload = isinstance(final_payload, dict) and bool(
            final_payload.get("jsonrpc")
        )
        if a2a:
            final_payload = build_a2a_payload(message, payload, final_headers)
            is_a2a_payload = True

        if is_a2a_payload:
            direct_common["agent_type"] = "a2a"

        direct_cloud: dict[str, Any] = {}
        if region:
            direct_cloud["region"] = region
        if runtime_id:
            direct_cloud["runtime_id"] = runtime_id
        if endpoint:
            direct_cloud["runtime_endpoint"] = endpoint
            if apikey:
                direct_cloud["runtime_apikey"] = apikey
            else:
                direct_cloud["runtime_auth_type"] = "custom_jwt"

        config_dict = {"common": direct_common, "launch_types": {"cloud": direct_cloud}}

        result = executor.execute(
            payload=final_payload,
            config_dict=config_dict,
            headers=final_headers,
            stream=None,
        )
    else:
        config_path = config_file or Path("agentkit.yaml")
        config = get_config(config_path=config_path)
        common_config = config.get_common_config()

        agent_type = getattr(common_config, "agent_type", "") or getattr(
            common_config, "template_type", ""
        )
        is_a2a = isinstance(agent_type, str) and "a2a" in agent_type.lower()

        if is_a2a:
            console.print(
                "[cyan]Detected A2A agent type - constructing A2A JSON-RPC envelope[/cyan]"
            )
            final_payload = build_a2a_payload(message, payload, final_headers)

        result = executor.execute(
            payload=final_payload,
            config_file=str(config_path),
            headers=final_headers,
            stream=None,  # Automatically determined by Runner
        )

    if not result.success:
        console.print(f"[red]❌ Invocation failed: {result.error}[/red]")
        raise typer.Exit(1)
    console.print("[green]✅ Invocation successful[/green]")

    # Get response
    response = result.response

    # Handle streaming response (generator)
    if result.is_streaming:
        console.print("[cyan]📡 Streaming response detected...[/cyan]\n")
        if raw:
            console.print(
                "[yellow]Raw mode enabled: printing raw stream events[/yellow]\n"
            )
        result_list = []
        complete_text = []
        printed_reasoning_header = False
        printed_answer_header = False
        printed_hidden_reasoning_hint = False
        printed_heartbeat = False
        last_heartbeat_ts = time.monotonic()

        for event in result.stream():
            result_list.append(event)

            if raw:
                # Print the event as received (before normalization), to help debugging.
                if isinstance(event, dict):
                    console.print(json.dumps(event, ensure_ascii=False))
                elif isinstance(event, str):
                    console.print(event.rstrip("\n"))
                else:
                    console.print(repr(event))

            normalized = _normalize_stream_event(event)
            if normalized is None:
                continue

            # Handle A2A JSON-RPC wrapper (unwrap to the underlying result payload)
            if normalized.get("jsonrpc") and "result" in normalized:
                result_payload = normalized.get("result")
                normalized = result_payload if isinstance(result_payload, dict) else {}

            # Keep existing partial-event behavior for ADK style streams.
            # (LangChain message events typically don't carry this field.)
            if not normalized.get("partial", True):
                logger.info("Partial event: %s", normalized)
                continue

            # In raw mode, we still keep termination/error handling, but skip
            # extracted text printing to avoid mixing structured debug output.
            if not raw:
                # LangChain: reasoning_content
                reasoning_chunks = _extract_reasoning_chunks_from_langchain_event(
                    normalized
                )
                if reasoning_chunks:
                    if show_reasoning:
                        if not printed_reasoning_header:
                            console.print("[cyan]🧠 Reasoning:[/cyan]")
                            printed_reasoning_header = True
                        for text in reasoning_chunks:
                            console.print(text, end="", style="yellow")
                    else:
                        # Default behavior: do not print reasoning, but keep the CLI responsive
                        # with a one-time hint and a periodic heartbeat.
                        if not printed_hidden_reasoning_hint:
                            console.print(
                                "[cyan]🤔 Model is thinking... (use --show-reasoning to view)[/cyan]"
                            )
                            printed_hidden_reasoning_hint = True
                        now = time.monotonic()
                        if now - last_heartbeat_ts >= 1.5:
                            console.print(".", end="", style="cyan")
                            printed_heartbeat = True
                            last_heartbeat_ts = now

                # Extract and print incremental answer text chunks
                text_chunks: list[str] = []
                text_chunks.extend(
                    _extract_text_chunks_from_langchain_event(normalized)
                )
                if not text_chunks:
                    text_chunks.extend(_extract_text_chunks_from_adk_event(normalized))

                if text_chunks:
                    # If we printed a hidden reasoning hint / heartbeat dots, separate answer on a new line.
                    if printed_hidden_reasoning_hint or printed_heartbeat:
                        console.print("")
                        printed_hidden_reasoning_hint = False
                        printed_heartbeat = False
                    if printed_reasoning_header and not printed_answer_header:
                        console.print("\n[cyan]📝 Answer:[/cyan]")
                        printed_answer_header = True
                    for text in text_chunks:
                        complete_text.append(text)
                        console.print(text, end="", style="green")

            # Display error information in event (if any)
            if "error" in normalized:
                console.print(f"\n[red]Error: {normalized['error']}[/red]")

            # Handle status updates (e.g., final flag or completed status)
            if normalized.get("final") is True:
                break

            status = normalized.get("status")
            if isinstance(status, dict) and status.get("state") == "completed":
                console.print("\n[cyan]Status indicates completed[/cyan]")
                break

        # Display complete response (commented out for now)
        # if complete_text:
        #     console.print("\n\n[cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/cyan]")
        #     console.print(f"[cyan]📝 Complete response:[/cyan] {''.join(complete_text)}")
        console.print("")  # Line break

        return str(result_list)

    # Handle non-streaming response
    console.print("[cyan]📝 Response:[/cyan]")
    if isinstance(response, dict):
        if raw:
            console.print(json.dumps(response, ensure_ascii=False))
        else:
            console.print(json.dumps(response, indent=2, ensure_ascii=False))
    else:
        console.print(response)

    return str(response)


def build_harness_overrides(
    system_prompt: Optional[str],
    model_name: Optional[str],
    tools: Optional[str],
    skills: Optional[str],
    runtime: Optional[str],
    registry_space_id: Optional[str] = None,
    registry_top_k: Optional[int] = None,
    registry_endpoint: Optional[str] = None,
    registry_region: Optional[str] = None,
) -> dict:
    """Collect the non-null fields for the harness app's ``HarnessOverrides``.

    Field names/shapes match AgentKit's ``HarnessOverrides`` model:
    ``model_name`` (string), ``tools`` / ``skills`` as comma-separated strings,
    ``system_prompt``, ``runtime``, and optional registry overrides. Only the
    keys present here are applied server-side (``model_fields_set``); unset
    fields keep the deployed harness's values.
    """
    overrides: dict[str, Any] = {}
    if system_prompt is not None:
        overrides["system_prompt"] = system_prompt
    if model_name is not None:
        overrides["model_name"] = model_name
    if tools is not None:
        overrides["tools"] = tools
    if skills is not None:
        overrides["skills"] = skills
    if runtime is not None:
        overrides["runtime"] = runtime
    if registry_space_id is not None:
        overrides["registry_space_id"] = registry_space_id
    if registry_top_k is not None:
        overrides["registry_top_k"] = registry_top_k
    if registry_endpoint is not None:
        overrides["registry_endpoint"] = registry_endpoint
    if registry_region is not None:
        overrides["registry_region"] = registry_region
    return overrides


_INVOKE_REGISTRY_QUERY_ALIASES = {
    "space_id": "registry_space_id",
    "registry_space_id": "registry_space_id",
    "space_name": "registry_space_name",
    "top_k": "registry_top_k",
    "registry_top_k": "registry_top_k",
    "endpoint": "registry_endpoint",
    "registry_endpoint": "registry_endpoint",
    "region": "registry_region",
    "registry_region": "registry_region",
}
_INVOKE_REGISTRY_INT_KEYS = {"registry_top_k"}


def _parse_harness_registry_override(value: Optional[str]) -> dict[str, Any]:
    """Parse ``--registry`` into one-time harness registry overrides.

    Supported forms:
    - agentkit://a2a-registry?space_id=xxx&top_k=3&region=cn-beijing
    - https://... (treated as registry_endpoint; recognized query params are
      also extracted when present)
    """
    if value is None:
        return {}

    raw = value.strip()
    if not raw:
        return {}

    parsed = urlparse(raw)
    overrides: dict[str, Any] = {}

    query = {
        key.replace("-", "_"): values[-1]
        for key, values in parse_qs(parsed.query, keep_blank_values=False).items()
        if values and values[-1] != ""
    }

    if parsed.scheme == "agentkit":
        if parsed.netloc != "a2a-registry" or parsed.path not in {"", "/"}:
            raise ValueError(
                "Unsupported registry URI. Use "
                '`agentkit://a2a-registry?space_id=xxx&top_k=3` or an http(s) URL.'
            )
    elif parsed.scheme in {"http", "https"}:
        overrides["registry_endpoint"] = raw
    else:
        raise ValueError(
            "Unsupported registry value. Use "
            '`agentkit://a2a-registry?space_id=xxx&top_k=3` or an http(s) URL.'
        )

    unknown = sorted(set(query) - set(_INVOKE_REGISTRY_QUERY_ALIASES))
    if unknown and parsed.scheme == "agentkit":
        raise ValueError(
            f"Unsupported registry query param(s): {', '.join(unknown)}. "
            f"Known: {', '.join(sorted(_INVOKE_REGISTRY_QUERY_ALIASES))}"
        )

    for key, raw_value in query.items():
        if key not in _INVOKE_REGISTRY_QUERY_ALIASES:
            continue
        target = _INVOKE_REGISTRY_QUERY_ALIASES[key]
        if target in _INVOKE_REGISTRY_INT_KEYS:
            try:
                overrides[target] = int(str(raw_value))
            except ValueError as exc:
                raise ValueError(
                    f"Registry param `{key}` must be an integer, got {raw_value!r}."
                ) from exc
        else:
            overrides[target] = raw_value

    return overrides


def _resolve_harness_registry_space_name(
    overrides: dict[str, Any],
    *,
    registry_endpoint: Optional[str],
    registry_region: Optional[str],
) -> None:
    if overrides.get("registry_space_id"):
        overrides.pop("registry_space_name", None)
        return

    space_name = overrides.pop("registry_space_name", None)
    if not space_name:
        return

    from agentkit.toolkit.cli.cli_add import (
        _default_agentkit_endpoint,
        _resolve_a2a_space_id_by_name,
    )

    resolved_region = (
        registry_region
        or overrides.get("registry_region")
        or os.getenv("AGENTKIT_REGION")
        or os.getenv("VOLCENGINE_REGION")
        or "cn-beijing"
    )
    resolved_endpoint = (
        registry_endpoint
        or overrides.get("registry_endpoint")
        or _default_agentkit_endpoint(str(resolved_region))
    )
    overrides["registry_space_id"] = _resolve_a2a_space_id_by_name(
        str(space_name),
        endpoint=str(resolved_endpoint),
        region=str(resolved_region),
    )


def _merge_harness_registry_overrides(
    *,
    registry: Optional[str],
    registry_space_id: Optional[str],
    registry_space_name: Optional[str],
    registry_top_k: Optional[int],
    registry_endpoint: Optional[str],
    registry_region: Optional[str],
) -> dict[str, Any]:
    overrides = _parse_harness_registry_override(registry)
    if registry_space_id is not None:
        overrides["registry_space_id"] = registry_space_id
    if registry_space_name is not None:
        overrides["registry_space_name"] = registry_space_name
    if registry_top_k is not None:
        overrides["registry_top_k"] = registry_top_k
    if registry_endpoint is not None:
        overrides["registry_endpoint"] = registry_endpoint
    if registry_region is not None:
        overrides["registry_region"] = registry_region
    _resolve_harness_registry_space_name(
        overrides,
        registry_endpoint=registry_endpoint,
        registry_region=registry_region,
    )
    return overrides


# Fixed ADK app name for the run_sse path. The harness loader serves its single
# agent under any app name, so a stable constant keeps the CLI decoupled from the
# deployed HARNESS_NAME.
_HARNESS_RUN_SSE_APP = "harness"


def _user_id_from_token(token: str) -> str | None:
    """Return the OIDC ``sub`` claim from a JWT bearer token, else ``None``.

    A custom_jwt harness is called with an OIDC id_token whose ``sub`` is the
    authenticated user's stable id — use it as the run's user_id so sessions are
    tied to the real identity. A key_auth token is an opaque api key (not a JWT,
    no ``sub``), so this returns ``None`` and the caller falls back to a random id.
    """
    parts = token.split(".")
    if len(parts) != 3:
        return None  # not a JWT (e.g. a key_auth api key)
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)  # restore base64 padding
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, binascii.Error):
        return None  # malformed JWT payload → fall back to random
    sub = claims.get("sub")
    return sub if isinstance(sub, str) and sub else None


def _harness_run_sse(
    *,
    base_url: str,
    token: str,
    prompt: str,
    session_id: str,
    overrides: dict,
    raw: bool,
) -> Any:
    """Invoke a deployed harness via the ADK ``/run_sse`` endpoint (streaming).

    app_name is the fixed ``"harness"``; user_id is the JWT ``sub`` when the token
    is an OIDC id_token, else a random id; session_id is the caller's. When
    ``overrides`` is non-empty it is sent as the ``harness`` field so the runtime
    streams a spawned (overridden) agent; otherwise the base agent.
    """
    import requests

    app_name = _HARNESS_RUN_SSE_APP
    sub = _user_id_from_token(token)
    user_id = sub or f"u-{uuid.uuid4().hex[:12]}"
    user_id_origin = "jwt sub" if sub else "random"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    console.print(
        f"[blue]run_sse: app_name={app_name}, user_id={user_id} ({user_id_origin}), "
        f"session_id={session_id}[/blue]"
    )

    # ADK /run_sse requires an existing session; create it (ignore "exists").
    session_url = f"{base_url}/apps/{app_name}/users/{user_id}/sessions/{session_id}"
    try:
        sr = requests.post(session_url, json={}, headers=headers, timeout=60)
    except requests.RequestException as e:
        console.print(f"[red]❌ Session create failed: {e}[/red]")
        raise typer.Exit(1)
    if sr.status_code not in (200, 400, 409):
        console.print(
            f"[red]❌ Session create HTTP {sr.status_code}: {sr.text[:300]}[/red]"
        )
        raise typer.Exit(1)

    body: dict[str, Any] = {
        "app_name": app_name,
        "user_id": user_id,
        "session_id": session_id,
        "new_message": {"role": "user", "parts": [{"text": prompt}]},
        "streaming": True,
    }
    if overrides:
        body["harness"] = overrides
        console.print(f"[blue]Using one-time overrides: {overrides}[/blue]")
    try:
        resp = requests.post(
            f"{base_url}/run_sse",
            json=body,
            headers=headers,
            timeout=300,
            stream=True,
        )
    except requests.RequestException as e:
        console.print(f"[red]❌ run_sse request failed: {e}[/red]")
        raise typer.Exit(1)
    if resp.status_code != 200:
        console.print(
            f"[red]❌ run_sse HTTP {resp.status_code}: {resp.text[:500]}[/red]"
        )
        raise typer.Exit(1)

    def _answer_text(event: dict) -> str:
        # Only the answer text; the model's "thought" (reasoning) parts stay hidden
        # behind the thinking spinner.
        parts = (event.get("content") or {}).get("parts") or []
        return "".join(
            p["text"]
            for p in parts
            if isinstance(p, dict) and p.get("text") and not p.get("thought")
        )

    streamed = []
    final_answer = ""
    answer_open = False  # first answer token seen → spinner stopped, reply started
    # A spinner covers the wait (model latency + reasoning) until the answer starts.
    status = None if raw else console.status("thinking", spinner="dots")
    if status is not None:
        status.start()
    try:
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            if raw:
                print(line)  # builtin print: no rich wrapping/markup on raw JSON
                continue
            event = _normalize_stream_event(line)
            if not isinstance(event, dict):
                continue
            if event.get("error"):
                if status is not None and not answer_open:
                    status.stop()
                console.print(f"\n[red]Error: {event['error']}[/red]")
                continue
            answer = _answer_text(event)
            # `partial=False` is the final aggregate (repeats everything) — keep it
            # as a fallback but don't print it; partial events stream the deltas.
            if event.get("partial") is False:
                final_answer = answer or final_answer
                continue
            if answer:
                if not answer_open:
                    if status is not None:
                        status.stop()  # leave the thinking phase
                    console.print("")  # blank line before the reply
                    answer_open = True
                console.print(answer, end="", style="green")
                streamed.append(answer)
    finally:
        if status is not None and not answer_open:
            status.stop()

    if not streamed and final_answer:
        console.print("")
        console.print(final_answer, end="", style="green")
    console.print("")
    return "".join(streamed) or final_answer


@invoke_app.command("harness")
def harness_command(
    name: str = typer.Argument(
        ..., help="Deployed harness name (looked up in the harness.json registry)."
    ),
    message: str = typer.Argument(..., help="Prompt to send to the harness"),
    directory: str = typer.Option(
        ".", "--directory", help="Directory holding the harness.json registry."
    ),
    user_id: str = typer.Option(
        "agentkit_user", "--user-id", help="user_id for the run."
    ),
    session_id: str = typer.Option(
        None,
        "--session-id",
        help="session_id for the run (random if unset).",
    ),
    max_llm_calls: int = typer.Option(
        None,
        "--max-llm-calls",
        help="Override max LLM calls for this single invocation.",
    ),
    system_prompt: str = typer.Option(
        None,
        "--system-prompt",
        help="Override the harness system prompt for this invocation.",
    ),
    model_name: str = typer.Option(
        None,
        "--model-name",
        help="Override the harness model name for this invocation.",
    ),
    tools: str = typer.Option(
        None, "--tools", help="Override harness tools (comma-separated) for this call."
    ),
    skills: str = typer.Option(
        None,
        "--skills",
        help="Override harness skills (comma-separated) for this call.",
    ),
    runtime: str = typer.Option(
        None, "--runtime", help="Override the harness runtime backend for this call."
    ),
    registry_space_id: str = typer.Option(
        None,
        "--registry-space-id",
        help="Override the A2A registry space id for this invocation.",
    ),
    registry_space_name: str = typer.Option(
        None,
        "--registry-space-name",
        help="Override the A2A registry space name for this invocation.",
    ),
    registry: str = typer.Option(
        None,
        "--registry",
        help=(
            "Override A2A registry for this invocation. Accepts "
            "`agentkit://a2a-registry?space_id=xxx&top_k=3` or an http(s) URL."
        ),
    ),
    registry_top_k: int = typer.Option(
        None,
        "--registry-top-k",
        help="Override the number of A2A AgentCards to retrieve for this invocation.",
    ),
    registry_endpoint: str = typer.Option(
        None,
        "--registry-endpoint",
        help="Override the A2A registry OpenAPI endpoint for this invocation.",
    ),
    registry_region: str = typer.Option(
        None,
        "--registry-region",
        help="Override the A2A registry OpenAPI region for this invocation.",
    ),
    apikey: str = typer.Option(
        None,
        "--apikey",
        "-ak",
        help="Bearer token override (e.g. OAuth JWT for custom_jwt harnesses).",
    ),
    raw: bool = typer.Option(
        False, "--raw", help="Print the raw response (InvokeHarnessResponse / SSE)."
    ),
    protocol: str = typer.Option(
        "run_sse",
        "--protocol",
        help="Transport: 'run_sse' (ADK /run_sse, default) or 'invoke' (POST /harness/invoke).",
    ),
) -> Any:
    """Invoke a deployed harness by name (resolved via the harness.json registry).

    ``agentkit deploy --harness <name>`` records each deployed harness in a
    ``harness.json`` registry; this command looks the name up there and POSTs to
    the harness runtime's ``/harness/invoke`` endpoint. Run it from the same
    directory you deployed in, or pass --directory.

    Per-call overrides are one-time: they apply only to this invocation and are
    not persisted. Only the flags you pass are sent; unset fields keep the
    deployed harness's values (tools/skills are added incrementally).

    Examples:
        # Invoke a deployed harness
        agentkit invoke harness my-harness "What is 2+2?"

        # Per-call overrides
        agentkit invoke harness my-harness --system-prompt "Be terse." "What is 2+2?"
        agentkit invoke harness my-harness --max-llm-calls 10 "Plan a trip."
        agentkit invoke harness my-harness --registry-space-id as-xxx "Find an agent."
    """
    import requests
    from agentkit.toolkit.cli.cli_add import _A2ARegisterError
    from agentkit.toolkit.harness import load_harness_registry

    console.print("[cyan]Invoking harness...[/cyan]")

    try:
        registry_overrides = _merge_harness_registry_overrides(
            registry=registry,
            registry_space_id=registry_space_id,
            registry_space_name=registry_space_name,
            registry_top_k=registry_top_k,
            registry_endpoint=registry_endpoint,
            registry_region=registry_region,
        )
    except _A2ARegisterError as e:
        console.print(f"[red]Error: failed to resolve A2A space name: {e}[/red]")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    registry = load_harness_registry(directory)
    entry = registry.get(name)
    if not isinstance(entry, dict) or not entry.get("url"):
        registry_path = Path(directory).resolve() / "harness.json"
        console.print(
            f"[red]Error: harness '{name}' not found in registry {registry_path}. "
            f"Deploy it first with `agentkit deploy --harness {name}`.[/red]"
        )
        raise typer.Exit(1)

    if protocol not in ("invoke", "run_sse"):
        console.print(
            f"[red]Error: --protocol must be 'invoke' or 'run_sse', got '{protocol}'.[/red]"
        )
        raise typer.Exit(1)

    base_url = entry["url"].rstrip("/")

    # Inbound credential selection is auth-type aware — the registry records how each
    # harness was deployed (harness/deploy.py _record_harness): a custom_jwt harness is
    # {auth_type:"custom_jwt", no "key"}; a key_auth harness is {"key": ...}.
    #   1. --apikey always wins (explicit manual override).
    #   2. custom_jwt harness → the `agentkit login` id_token (OIDC JWT, auto-refreshed) —
    #      the data plane's JWT path; refresh failure errors (re-login), never silent.
    #   3. key_auth harness → the static "key"; a key_auth authorizer would reject a JWT,
    #      so we never force the id_token onto it.
    # Both transports (invoke and run_sse) share this resolution.
    from agentkit.auth.errors import AuthError
    from agentkit.auth.sso import load_session

    is_jwt_harness = entry.get("auth_type") == "custom_jwt" or not entry.get("key")
    auth_session = None  # set only when the login id_token is the credential (enables 401 refresh)
    token = apikey or ""
    if not token and is_jwt_harness:
        try:
            auth_session = load_session()
        except Exception:
            auth_session = None
        if auth_session is not None:
            try:
                token = auth_session.valid_id_token()
            except AuthError as e:
                console.print(f"[red]❌ {e}[/red]")
                if getattr(e, "hint", None):
                    console.print(f"[yellow]{e.hint}[/yellow]")
                raise typer.Exit(1)
    if not token:
        token = entry.get("key") or ""

    # No session given → mint a random one (both transports behave identically;
    # creating it is idempotent).
    session_id = session_id or f"s-{uuid.uuid4().hex[:12]}"

    if protocol == "run_sse":
        # run_sse supports the same overrides (sent as the `harness` field); only
        # --max-llm-calls is invoke-only (not part of the ADK run_sse request).
        if max_llm_calls is not None:
            console.print(
                "[yellow]Note: --max-llm-calls is ignored with --protocol "
                "run_sse.[/yellow]"
            )
        return _harness_run_sse(
            base_url=base_url,
            token=token,
            prompt=message,
            session_id=session_id,
            overrides=build_harness_overrides(
                system_prompt,
                model_name,
                tools,
                skills,
                runtime,
                registry_space_id=registry_overrides.get("registry_space_id"),
                registry_top_k=registry_overrides.get("registry_top_k"),
                registry_endpoint=registry_overrides.get("registry_endpoint"),
                registry_region=registry_overrides.get("registry_region"),
            ),
            raw=raw,
        )

    invoke_url = base_url + "/harness/invoke"
    req_headers = {"Content-Type": "application/json"}
    if token:
        req_headers["Authorization"] = f"Bearer {token}"

    run_agent_request: dict[str, Any] = {
        "user_id": user_id,
        "session_id": session_id,
    }
    if max_llm_calls is not None:
        run_agent_request["max_llm_calls"] = max_llm_calls

    body: dict[str, Any] = {
        "prompt": message,
        "harness_name": name,
        "run_agent_request": run_agent_request,
    }
    overrides = build_harness_overrides(
        system_prompt,
        model_name,
        tools,
        skills,
        runtime,
        registry_overrides.get("registry_space_id"),
        registry_overrides.get("registry_top_k"),
        registry_overrides.get("registry_endpoint"),
        registry_overrides.get("registry_region"),
    )
    if overrides:
        body["harness"] = overrides
        console.print(f"[blue]Using one-time overrides: {overrides}[/blue]")

    try:
        resp = requests.post(invoke_url, json=body, headers=req_headers, timeout=300)
        # If the harness rejects a locally-valid JWT (clock skew, mid-flight rotation,
        # server-side revocation), force one refresh and retry exactly once.
        if resp.status_code == 401 and auth_session is not None and not apikey:
            try:
                token = auth_session.valid_id_token(force_refresh=True)
            except AuthError as e:
                console.print(f"[red]❌ session refresh failed: {e}[/red]")
                if getattr(e, "hint", None):
                    console.print(f"[yellow]{e.hint}[/yellow]")
                raise typer.Exit(1)
            req_headers["Authorization"] = f"Bearer {token}"
            resp = requests.post(invoke_url, json=body, headers=req_headers, timeout=300)
    except requests.RequestException as e:
        console.print(f"[red]❌ Request to {invoke_url} failed: {e}[/red]")
        raise typer.Exit(1)

    if resp.status_code != 200:
        console.print(
            f"[red]❌ Harness returned HTTP {resp.status_code}: {resp.text}[/red]"
        )
        raise typer.Exit(1)

    try:
        data = resp.json()
    except ValueError:
        console.print(f"[red]❌ Non-JSON response: {resp.text}[/red]")
        raise typer.Exit(1)

    # The harness returns failures (unsupported tool, skill load failure, or a
    # runtime error) in `error`; surface it verbatim instead of as normal output.
    error = data.get("error") if isinstance(data, dict) else None

    if raw:
        console.print(json.dumps(data, ensure_ascii=False, indent=2))
        if error:
            raise typer.Exit(1)
        return str(data)

    if error:
        console.print(f"[red]❌ Harness error: {error}[/red]")
        raise typer.Exit(1)

    console.print("[green]✅ Invocation successful[/green]")
    if data.get("overwrite"):
        console.print("[yellow](served with one-time overrides)[/yellow]")
    console.print("[cyan]📝 Response:[/cyan]")
    console.print(data.get("output", ""))
    return str(data.get("output", ""))
