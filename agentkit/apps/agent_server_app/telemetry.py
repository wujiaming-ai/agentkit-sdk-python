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

from __future__ import annotations

import asyncio
from contextvars import ContextVar, Token
from dataclasses import dataclass
import logging
import time
from typing import Any, Mapping, Optional

from opentelemetry import context as context_api
from opentelemetry import metrics, propagate, trace
from opentelemetry.metrics import Meter
from opentelemetry.trace import Span, SpanKind, Status, StatusCode, Tracer

from agentkit.apps.utils import safe_serialize_to_json_string
from agentkit.observability.context import safe_detach_context_token

_INVOKE_PATH_SUFFIXES = ("/run_sse", "/run", "/invoke", "/invocations")
_RECORDED_HEADERS = {
    "content-type",
    "traceparent",
    "tracestate",
    "user-agent",
    "x-request-id",
    "x-amzn-bedrock-agentcore-runtime-request-id",
    "x-amzn-bedrock-agentcore-runtime-session-id",
}

_DURATION_BUCKETS = [
    0.01,
    0.02,
    0.04,
    0.08,
    0.16,
    0.32,
    0.64,
    1.28,
    2.56,
    5.12,
    10.24,
    20.48,
    40.96,
    81.92,
    163.84,
]

logger = logging.getLogger("agentkit." + __name__)


def _is_invoke_path(path: str) -> bool:
    return any(path == suffix or path.endswith(suffix) for suffix in _INVOKE_PATH_SUFFIXES)


def _safe_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        key.lower(): value
        for key, value in headers.items()
        if key.lower() in _RECORDED_HEADERS
    }


def _span_is_valid(span: Span) -> bool:
    try:
        return span.get_span_context().is_valid and span.is_recording()
    except Exception:
        return False


def _set_attribute_if_recording(span: Span, key: str, value: Any) -> None:
    if _span_is_valid(span):
        span.set_attribute(key, value)


def _set_status_if_recording(span: Span, status: Status) -> None:
    if _span_is_valid(span):
        span.set_status(status)


def exception_message(exception: BaseException) -> str:
    message = str(exception)
    return message if message else type(exception).__name__


@dataclass
class RequestTelemetryState:
    span: Span
    path: str
    method: str
    start_ns: int
    owns_span: bool
    span_context_token: object | None = None
    state_context_token: Token["RequestTelemetryState | None"] | None = None
    status_code: int | None = None
    exception: BaseException | None = None
    finished: bool = False


class Telemetry:
    def __init__(
        self,
        *,
        tracer: Tracer | None = None,
        meter: Meter | None = None,
        clock_ns: Any = time.perf_counter_ns,
    ) -> None:
        self.tracer = tracer or trace.get_tracer("agentkit.agent_server_app")
        self.meter = meter or metrics.get_meter("agentkit.agent_server_app")
        self.clock_ns = clock_ns
        self._request_state: ContextVar[RequestTelemetryState | None] = ContextVar(
            "agentkit_request_telemetry_state",
            default=None,
        )
        # Keep the published legacy metric while adding OTel-style instruments.
        self.latency_histogram = self.meter.create_histogram(
            name="agentkit_runtime_operation_latency",
            description="operation latency",
            unit="s",
            explicit_bucket_boundaries_advisory=_DURATION_BUCKETS,
        )
        self.request_duration = self.meter.create_histogram(
            name="agentkit.server.request.duration",
            description="AgentKit invocation request duration.",
            unit="s",
            explicit_bucket_boundaries_advisory=_DURATION_BUCKETS,
        )
        self.request_count = self.meter.create_counter(
            name="agentkit.server.requests",
            description="Completed AgentKit invocation requests.",
            unit="{request}",
        )

    def start_server_request(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str],
        server_address: str | None = None,
    ) -> RequestTelemetryState:
        current_span = trace.get_current_span()
        owns_span = not _span_is_valid(current_span)
        span_context_token = None
        if owns_span:
            parent_context = propagate.extract(carrier=headers)
            span = self.tracer.start_span(
                f"{method} {path}",
                context=parent_context,
                kind=SpanKind.SERVER,
            )
            span_context_token = context_api.attach(trace.set_span_in_context(span))
        else:
            span = current_span

        state = RequestTelemetryState(
            span=span,
            path=path,
            method=method,
            start_ns=self.clock_ns(),
            owns_span=owns_span,
            span_context_token=span_context_token,
        )
        state.state_context_token = self._request_state.set(state)
        self.trace_agent_server(
            func_name=f"{method} {path}",
            span=span,
            headers=dict(headers),
            text="",
        )
        span.set_attribute("http.request.method", method)
        span.set_attribute("url.path", path)
        span.set_attribute("http.route", path)
        if server_address:
            span.set_attribute("server.address", server_address)
        return state

    def current_request(self) -> RequestTelemetryState | None:
        return self._request_state.get()

    def set_invocation_context(
        self,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        invocation_id: str | None = None,
    ) -> None:
        span = trace.get_current_span()
        if not hasattr(span, "set_attribute"):
            return
        if session_id:
            _set_attribute_if_recording(span, "gen_ai.session.id", session_id)
        if user_id:
            _set_attribute_if_recording(span, "enduser.id", user_id)
        if invocation_id:
            _set_attribute_if_recording(span, "agentkit.invocation.id", invocation_id)

    def record_current_exception(self, exception: BaseException) -> None:
        state = self.current_request()
        if state is not None:
            state.exception = exception
            self.handle_exception(state.span, exception)
            return
        self.handle_exception(trace.get_current_span(), exception)

    def finish_server_request(
        self,
        state: RequestTelemetryState,
        *,
        status_code: int | None = None,
        exception: BaseException | None = None,
    ) -> None:
        if state.finished:
            return
        state.finished = True
        if status_code is not None:
            state.status_code = status_code
        if exception is not None:
            state.exception = exception
        exception = state.exception
        if exception is not None:
            self.handle_exception(state.span, exception)
        elif state.status_code is not None and state.status_code >= 500:
            _set_status_if_recording(state.span, Status(StatusCode.ERROR))

        if state.status_code is not None:
            _set_attribute_if_recording(
                state.span,
                "http.response.status_code",
                state.status_code,
            )

        outcome = "success"
        if isinstance(exception, (asyncio.CancelledError, GeneratorExit)):
            outcome = "cancelled"
        elif exception is not None or (state.status_code or 0) >= 500:
            outcome = "error"
        attributes: dict[str, str] = {
            "http.request.method": state.method,
            "http.route": state.path,
            "agentkit.operation.outcome": outcome,
        }
        if exception is not None:
            attributes["error.type"] = type(exception).__name__
        duration = (self.clock_ns() - state.start_ns) / 1e9
        if _is_invoke_path(state.path):
            try:
                self.request_count.add(1, attributes=attributes)
                self.request_duration.record(duration, attributes=attributes)
                legacy_attributes = {
                    "gen_ai_operation_name": "invoke_agent",
                    "gen_ai_operation_type": "agent_server",
                }
                if exception is not None:
                    status = getattr(exception, "status_code", None)
                    legacy_attributes["error_type"] = (
                        f"{type(exception).__name__}_{status}"
                        if status
                        else type(exception).__name__
                    )
                self.latency_histogram.record(duration, legacy_attributes)
            except Exception:
                logger.warning("Failed to record AgentKit server metrics.", exc_info=True)

        _set_attribute_if_recording(state.span, "agentkit.operation.outcome", outcome)
        if state.owns_span:
            state.span.end()
        if state.span_context_token is not None:
            safe_detach_context_token(state.span_context_token)
            state.span_context_token = None
        if state.state_context_token is not None:
            try:
                self._request_state.reset(state.state_context_token)
            except ValueError:
                # Streaming implementations may finish in a copied Context.
                self._request_state.set(None)
            state.state_context_token = None

    def trace_agent_server(
        self,
        func_name: str,
        span: Span,
        headers: dict,
        text: str,
    ) -> None:
        del text
        span.set_attribute("gen_ai.system", "agentkit")
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("gen_ai.span.kind", "agent_server")
        span.set_attribute("gen_ai.operation.type", "agent_server")
        span.set_attribute("agentkit.server.operation", func_name)

        safe_headers = _safe_headers(headers)
        if safe_headers:
            span.set_attribute(
                "gen_ai.request.headers",
                safe_serialize_to_json_string(safe_headers),
            )
        session_id = headers.get("session_id") or headers.get(
            "x-amzn-bedrock-agentcore-runtime-session-id"
        )
        if session_id:
            span.set_attribute("gen_ai.session.id", session_id)

    def trace_agent_server_finish(
        self,
        path: str,
        func_result: str,
        exception: Optional[Exception],
    ) -> None:
        del func_result
        active = self.current_request()
        if active is not None:
            if exception is not None:
                self.record_current_exception(exception)
            return

        # Backward-compatible behavior for callers outside the ASGI middleware.
        span = trace.get_current_span()
        if not span or not span.is_recording():
            return
        attributes = {
            "gen_ai_operation_name": "invoke_agent",
            "gen_ai_operation_type": "agent_server",
        }
        if exception:
            self.handle_exception(span, exception)
            status = getattr(exception, "status_code", None)
            attributes["error_type"] = (
                f"{exception.__class__.__name__}_{status}"
                if status
                else exception.__class__.__name__
            )
        if hasattr(span, "start_time") and _is_invoke_path(path):
            duration = (time.time_ns() - span.start_time) / 1e9  # type: ignore[attr-defined]
            self.latency_histogram.record(duration, attributes)
        span.end()

    @staticmethod
    def handle_exception(span: Span, exception: BaseException) -> None:
        if not span or not span.is_recording():
            return
        _set_status_if_recording(
            span,
            Status(
                status_code=StatusCode.ERROR,
                description=f"{type(exception).__name__}: {exception}",
            ),
        )
        _set_attribute_if_recording(span, "error.type", type(exception).__name__)
        if isinstance(exception, Exception):
            span.record_exception(exception)


telemetry = Telemetry()
