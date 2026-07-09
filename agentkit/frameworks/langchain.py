"""LangChain adapter for AgentKit apps."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from pydantic import PrivateAttr

from agentkit.frameworks._common import (
    UnsupportedFrameworkAgentError,
    adk_event,
    chunk_delta,
    chunk_to_text,
    is_input_shape_error,
    maybe_await,
    user_text,
)


try:
    from langchain_core.messages import HumanMessage
except ImportError:  # pragma: no cover - depends on optional packages.
    HumanMessage = None  # type: ignore[assignment]


class LangChainAgentkitBridge(BaseAgent):
    """Adapt a LangChain Runnable or callable to AgentKit's ADK runtime boundary."""

    _runnable: Any = PrivateAttr()
    _input_key: str = PrivateAttr(default="input")

    def __init__(
        self,
        runnable: Any,
        *,
        name: str = "langchain_agent",
        description: str = "LangChain agent adapted for AgentKit runtime",
        input_key: str = "input",
    ) -> None:
        super().__init__(name=name, description=description)
        self._runnable = runnable
        self._input_key = input_key

    def _input_candidates(self, payload: dict[str, Any], text_input: str) -> list[Any]:
        candidates: list[Any] = [payload, text_input]
        if HumanMessage is not None:
            message = HumanMessage(content=text_input)
            candidates.extend(([message], {"messages": [message]}))
        return candidates

    async def _call_once(self, payload: dict[str, Any], text_input: str) -> Any:
        candidates = self._input_candidates(payload, text_input)
        ainvoke = getattr(self._runnable, "ainvoke", None)
        if callable(ainvoke):
            last_error: Exception | None = None
            for candidate in candidates:
                try:
                    return await ainvoke(candidate)
                except Exception as exc:
                    if not is_input_shape_error(exc):
                        raise
                    last_error = exc
            if last_error is not None:
                raise last_error

        invoke = getattr(self._runnable, "invoke", None)
        if callable(invoke):
            last_error = None
            for candidate in candidates:
                try:
                    return invoke(candidate)
                except Exception as exc:
                    if not is_input_shape_error(exc):
                        raise
                    last_error = exc
            if last_error is not None:
                raise last_error

        if callable(self._runnable):
            last_error = None
            for candidate in candidates:
                try:
                    return await maybe_await(self._runnable(candidate))
                except Exception as exc:
                    if not is_input_shape_error(exc):
                        raise
                    last_error = exc
            if last_error is not None:
                raise last_error

        raise UnsupportedFrameworkAgentError(
            "LangChain entry must be a Runnable-like object exposing "
            "astream, stream, ainvoke, or invoke, or a callable that accepts the user input."
        )

    async def _stream_chunks(self, payload: dict[str, Any], text_input: str):
        astream = getattr(self._runnable, "astream", None)
        if callable(astream):
            last_error: Exception | None = None
            for candidate in self._input_candidates(payload, text_input):
                emitted = False
                try:
                    async for chunk in astream(candidate):
                        emitted = True
                        yield chunk
                    return
                except Exception as exc:
                    if emitted:
                        raise
                    if not is_input_shape_error(exc):
                        raise
                    last_error = exc
            if last_error is not None:
                raise last_error
            return

        stream = getattr(self._runnable, "stream", None)
        if callable(stream):
            last_error = None
            for candidate in self._input_candidates(payload, text_input):
                emitted = False
                try:
                    for chunk in stream(candidate):
                        emitted = True
                        yield chunk
                    return
                except Exception as exc:
                    if emitted:
                        raise
                    if not is_input_shape_error(exc):
                        raise
                    last_error = exc
            if last_error is not None:
                raise last_error

    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        text_input = user_text(ctx)
        payload = {self._input_key: text_input}
        accumulated_text = ""
        has_output = False
        last_text = ""
        streamed = False

        async for chunk in self._stream_chunks(payload, text_input):
            streamed = True
            text = chunk_to_text(chunk)
            if not text:
                continue
            delta = chunk_delta(accumulated_text, text)
            if not delta:
                continue
            accumulated_text += delta
            has_output = True
            last_text = accumulated_text
            yield adk_event(ctx, self.name, delta, partial=True)

        if not streamed:
            result = await self._call_once(payload, text_input)
            last_text = chunk_to_text(result)

        final_text = accumulated_text if has_output else last_text
        if final_text:
            yield adk_event(ctx, self.name, final_text, partial=False)
