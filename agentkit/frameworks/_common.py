"""Shared helpers for framework-to-ADK adapters."""

from __future__ import annotations

import inspect
import json
from typing import Any

from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types


class FrameworkBridgeError(RuntimeError):
    """Base error raised by AgentKit framework adapters."""


class UnsupportedFrameworkAgentError(FrameworkBridgeError):
    """Raised when an entry object does not expose a supported agent protocol."""


def user_text(ctx: InvocationContext) -> str:
    content = ctx.user_content
    if content is None or not content.parts:
        return ""
    texts: list[str] = []
    for part in content.parts:
        text = getattr(part, "text", None)
        if text:
            texts.append(text)
    return "\n".join(texts)


def json_text(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("text", "content", "output", "answer"):
            if key in content:
                text = content_to_text(content[key])
                if text:
                    return text
        return json_text(content)
    if isinstance(content, list):
        return "".join(content_to_text(item) for item in content)
    text = getattr(content, "text", None)
    if isinstance(text, str):
        return text
    return str(content)


def chunk_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    content = getattr(value, "content", None)
    if content is not None:
        return content_to_text(content)
    text = getattr(value, "text", None)
    if isinstance(text, str):
        return text
    if isinstance(value, dict):
        for key in ("output", "answer", "text", "content"):
            if key in value:
                text = chunk_to_text(value[key])
                if text:
                    return text
        messages = value.get("messages")
        if isinstance(messages, (list, tuple)) and messages:
            return chunk_to_text(messages[-1])
        for nested in value.values():
            text = chunk_to_text(nested)
            if text:
                return text
        return json_text(value)
    if isinstance(value, (list, tuple)):
        for item in reversed(value):
            text = chunk_to_text(item)
            if text:
                return text
        return ""
    return str(value)


def adk_event(
    ctx: InvocationContext,
    author: str,
    text: str,
    *,
    partial: bool,
) -> Event:
    return Event(
        invocation_id=ctx.invocation_id,
        author=author,
        branch=ctx.branch,
        partial=partial,
        content=types.Content(role="model", parts=[types.Part(text=text)]),
    )


async def maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def is_input_shape_error(exc: Exception) -> bool:
    message = str(exc)
    name = exc.__class__.__name__
    return (
        name == "InvalidUpdateError"
        or "Invalid input type" in message
        or "Must be a PromptValue, str, or list of BaseMessages" in message
        or "Expected dict" in message
        or "string indices must be integers" in message
        or "'str' object is not subscriptable" in message
        or "Input to ChatPromptTemplate is missing variables" in message
    )


def chunk_delta(accumulated: str, text: str) -> str:
    if not accumulated:
        return text
    if text.startswith(accumulated):
        return text[len(accumulated) :]
    return text
