"""Framework-neutral OpenTelemetry instrumentation for migrated agents."""

from __future__ import annotations

import asyncio
import logging
import time
from types import TracebackType
from typing import Any

from opentelemetry import context as context_api
from opentelemetry import metrics, trace
from opentelemetry.metrics import Meter
from opentelemetry.trace import Span, Status, StatusCode, Tracer

from agentkit.observability.context import safe_detach_context_token

logger = logging.getLogger(__name__)

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


def _event_text(event: Any) -> str:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) or ()
    return "".join(
        text
        for part in parts
        if isinstance((text := getattr(part, "text", None)), str)
    )


def _usage_value(usage: Any, *names: str) -> int | None:
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, int) and value >= 0:
            return value
    return None


class FrameworkTelemetry:
    """Create framework spans and aggregate low-cardinality metrics.

    AgentKit never installs a provider or exporter here. Instruments use the
    process-wide OpenTelemetry providers supplied by the runtime, so native
    framework telemetry and AgentKit telemetry share one trace.
    """

    def __init__(
        self,
        *,
        tracer: Tracer | None = None,
        meter: Meter | None = None,
        clock_ns: Any = time.perf_counter_ns,
    ) -> None:
        self.tracer = tracer or trace.get_tracer("agentkit.frameworks")
        self.meter = meter or metrics.get_meter("agentkit.frameworks")
        self.clock_ns = clock_ns or time.perf_counter_ns
        self.invocations = self.meter.create_counter(
            "agentkit.framework.invocations",
            unit="{invocation}",
            description="Completed framework invocations.",
        )
        self.duration = self.meter.create_histogram(
            "agentkit.framework.invocation.duration",
            unit="s",
            description="Framework invocation duration.",
            explicit_bucket_boundaries_advisory=_DURATION_BUCKETS,
        )
        self.time_to_first_output = self.meter.create_histogram(
            "agentkit.framework.time_to_first_output",
            unit="s",
            description="Time from framework invocation to first user-visible output.",
            explicit_bucket_boundaries_advisory=_DURATION_BUCKETS,
        )
        self.stream_events = self.meter.create_histogram(
            "agentkit.framework.stream.events",
            unit="{event}",
            description="User-visible output events per framework invocation.",
        )
        self.token_usage = self.meter.create_histogram(
            "agentkit.framework.token.usage",
            unit="{token}",
            description="Framework-reported token usage.",
        )

    def start_invocation(
        self,
        *,
        framework: str,
        agent_name: str,
        invocation_id: str | None = None,
        session_id: str | None = None,
        streaming: bool = True,
    ) -> "FrameworkInvocation":
        return FrameworkInvocation(
            telemetry=self,
            framework=framework,
            agent_name=agent_name,
            invocation_id=invocation_id,
            session_id=session_id,
            streaming=streaming,
        )


class FrameworkInvocation:
    """One framework invocation span with aggregate streaming measurements."""

    def __init__(
        self,
        *,
        telemetry: FrameworkTelemetry,
        framework: str,
        agent_name: str,
        invocation_id: str | None,
        session_id: str | None,
        streaming: bool,
    ) -> None:
        self.telemetry = telemetry
        self.framework = framework
        self.agent_name = agent_name
        self.invocation_id = invocation_id
        self.session_id = session_id
        self.streaming = streaming
        self.span: Span | None = None
        self._context_token: object | None = None
        self._start_ns = 0
        self._first_output_ns: int | None = None
        self._output_events = 0
        self._input_tokens: int | None = None
        self._output_tokens: int | None = None
        self._outcome = "success"
        self._error_type: str | None = None
        self._finished = False

    def __enter__(self) -> "FrameworkInvocation":
        self._start_ns = self.telemetry.clock_ns()
        attributes: dict[str, str | bool] = {
            "gen_ai.operation.name": "invoke_agent",
            "agentkit.framework.name": self.framework,
            "agentkit.agent.name": self.agent_name,
            "agentkit.streaming": self.streaming,
        }
        if self.invocation_id:
            attributes["agentkit.invocation.id"] = self.invocation_id
        if self.session_id:
            attributes["gen_ai.session.id"] = self.session_id
        self.span = self.telemetry.tracer.start_span(
            f"invoke_agent {self.framework}",
            kind=trace.SpanKind.INTERNAL,
            attributes=attributes,
        )
        self.attach_context()
        return self

    def attach_context(self) -> None:
        if self.span is not None and self._context_token is None:
            self._context_token = context_api.attach(
                trace.set_span_in_context(self.span)
            )

    def detach_context(self) -> None:
        if self._context_token is None:
            return
        safe_detach_context_token(self._context_token)
        self._context_token = None

    def observe_output(self, text: str) -> None:
        if not text:
            return
        self._output_events += 1
        if self._first_output_ns is not None:
            return
        self._first_output_ns = self.telemetry.clock_ns()
        elapsed = (self._first_output_ns - self._start_ns) / 1e9
        if self.span is not None:
            self.span.set_attribute("agentkit.time_to_first_output", elapsed)
            self.span.add_event("agentkit.first_output")

    def observe_event(self, event: Any) -> None:
        self.observe_output(_event_text(event))
        usage = getattr(event, "usage_metadata", None)
        if usage is not None:
            self._input_tokens = _usage_value(
                usage,
                "prompt_token_count",
                "input_token_count",
            )
            self._output_tokens = _usage_value(
                usage,
                "candidates_token_count",
                "output_token_count",
            )
        error_code = getattr(event, "error_code", None)
        if error_code:
            if getattr(event, "interrupted", False):
                self.mark_interrupted(str(error_code))
            else:
                self.mark_failed(str(error_code), getattr(event, "error_message", None))

    def observe_usage(
        self,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        if isinstance(input_tokens, int) and input_tokens >= 0:
            self._input_tokens = input_tokens
        if isinstance(output_tokens, int) and output_tokens >= 0:
            self._output_tokens = output_tokens

    def mark_interrupted(self, reason: str | None = None) -> None:
        self._outcome = "interrupted"
        if self.span is not None:
            attributes = {"error.type": reason} if reason else None
            self.span.add_event("agentkit.interrupted", attributes=attributes)

    def mark_failed(self, error_type: str, message: str | None = None) -> None:
        self._outcome = "error"
        self._error_type = error_type
        if self.span is not None:
            self.span.set_attribute("error.type", error_type)
            self.span.set_status(Status(StatusCode.ERROR, message))

    def _record_exception(self, exception: BaseException) -> None:
        self.mark_failed(type(exception).__name__, str(exception))
        if self.span is not None and isinstance(exception, Exception):
            self.span.record_exception(exception)

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        end_ns = self.telemetry.clock_ns()
        duration = (end_ns - self._start_ns) / 1e9
        attributes: dict[str, str | bool] = {
            "agentkit.framework.name": self.framework,
            "agentkit.operation.outcome": self._outcome,
            "agentkit.streaming": self.streaming,
        }
        if self._error_type:
            attributes["error.type"] = self._error_type
        if self.span is not None:
            self.span.set_attribute("agentkit.operation.outcome", self._outcome)
            self.span.set_attribute("agentkit.stream.event_count", self._output_events)
        try:
            self.telemetry.invocations.add(1, attributes)
            self.telemetry.duration.record(duration, attributes)
            self.telemetry.stream_events.record(self._output_events, attributes)
            if self._first_output_ns is not None:
                self.telemetry.time_to_first_output.record(
                    (self._first_output_ns - self._start_ns) / 1e9,
                    attributes,
                )
            if self._input_tokens is not None:
                self.telemetry.token_usage.record(
                    self._input_tokens,
                    {**attributes, "gen_ai.token.type": "input"},
                )
            if self._output_tokens is not None:
                self.telemetry.token_usage.record(
                    self._output_tokens,
                    {**attributes, "gen_ai.token.type": "output"},
                )
        except Exception:
            logger.warning("Failed to record AgentKit framework telemetry.", exc_info=True)
        finally:
            if self.span is not None:
                try:
                    self.span.end()
                except Exception:
                    logger.warning(
                        "Failed to end AgentKit framework telemetry span.",
                        exc_info=True,
                    )
            if self._context_token is not None:
                try:
                    self.detach_context()
                except Exception:
                    logger.warning(
                        "Failed to detach AgentKit framework telemetry context.",
                        exc_info=True,
                    )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del traceback
        if exc is not None:
            if isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
                self._outcome = "cancelled"
            else:
                self._record_exception(exc)
        self.finish()
        return False


framework_telemetry = FrameworkTelemetry()
