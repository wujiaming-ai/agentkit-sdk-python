from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from google.adk.events import Event
from google.genai import types
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from agentkit.observability.framework import FrameworkTelemetry
import agentkit.observability.framework as framework_mod


def _stack(clock_values=None):
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    values = iter(clock_values) if clock_values is not None else None
    telemetry = FrameworkTelemetry(
        tracer=tracer_provider.get_tracer("test.framework"),
        meter=meter_provider.get_meter("test.framework"),
        clock_ns=(lambda: next(values)) if values is not None else None,
    )
    return telemetry, tracer_provider, exporter, meter_provider, reader


def _metrics(reader):
    data = reader.get_metrics_data()
    return {
        metric.name: metric
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


def test_invocation_records_parent_child_ttft_usage_and_aggregate_events():
    telemetry, provider, exporter, meter_provider, reader = _stack(
        [0, 100_000_000, 500_000_000]
    )
    parent_tracer = provider.get_tracer("test.parent")

    with parent_tracer.start_as_current_span("server") as parent:
        with telemetry.start_invocation(
            framework="langchain",
            agent_name="agent",
            invocation_id="invocation-1",
            session_id="session-1",
        ) as invocation:
            invocation.observe_output("a")
            invocation.observe_output("b")
            invocation.observe_usage(input_tokens=10, output_tokens=4)

    provider.force_flush()
    meter_provider.force_flush()
    spans = {span.name: span for span in exporter.get_finished_spans()}
    framework = spans["invoke_agent langchain"]
    assert framework.parent.span_id == parent.get_span_context().span_id
    assert framework.attributes["agentkit.framework.name"] == "langchain"
    assert framework.attributes["agentkit.time_to_first_output"] == pytest.approx(0.1)
    assert framework.attributes["agentkit.stream.event_count"] == 2
    assert framework.attributes["agentkit.operation.outcome"] == "success"
    assert set(_metrics(reader)) == {
        "agentkit.framework.invocations",
        "agentkit.framework.invocation.duration",
        "agentkit.framework.time_to_first_output",
        "agentkit.framework.stream.events",
        "agentkit.framework.token.usage",
    }


def test_adk_event_error_interrupt_and_usage_are_observed():
    telemetry, provider, exporter, _meter_provider, _reader = _stack()
    usage = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=7,
        candidates_token_count=3,
    )
    event = Event(
        invocation_id="invocation",
        author="agent",
        content=types.Content(role="model", parts=[types.Part(text="answer")]),
        usage_metadata=usage,
    )

    with telemetry.start_invocation(
        framework="adk",
        agent_name="agent",
    ) as invocation:
        invocation.observe_event(event)
        invocation.observe_event(
            Event(
                invocation_id="invocation",
                author="agent",
                interrupted=True,
                error_code="HITL",
                error_message="review",
            )
        )

    provider.force_flush()
    span = next(
        span
        for span in exporter.get_finished_spans()
        if span.name == "invoke_agent adk"
    )
    assert span.attributes["agentkit.operation.outcome"] == "interrupted"


def test_adk_event_non_interrupt_error_and_invalid_usage_are_observed():
    telemetry, provider, exporter, _meter_provider, _reader = _stack()
    event = SimpleNamespace(
        content=None,
        usage_metadata=SimpleNamespace(
            prompt_token_count=-1,
            input_token_count=None,
            candidates_token_count=-1,
            output_token_count=None,
        ),
        error_code="MODEL_ERROR",
        error_message="failed",
        interrupted=False,
    )

    with telemetry.start_invocation(
        framework="adk",
        agent_name="agent",
    ) as invocation:
        invocation.detach_context()
        invocation.observe_event(event)

    provider.force_flush()
    span = next(
        span
        for span in exporter.get_finished_spans()
        if span.name == "invoke_agent adk"
    )
    assert span.status.status_code is trace.StatusCode.ERROR
    assert span.attributes["error.type"] == "MODEL_ERROR"


def test_failure_records_exception_and_reraises():
    telemetry, provider, exporter, _meter_provider, _reader = _stack()
    failure = ValueError("bad input")

    with pytest.raises(ValueError) as raised:
        with telemetry.start_invocation(
            framework="langgraph",
            agent_name="agent",
        ):
            raise failure

    assert raised.value is failure
    provider.force_flush()
    span = next(
        span
        for span in exporter.get_finished_spans()
        if span.name == "invoke_agent langgraph"
    )
    assert span.status.status_code is trace.StatusCode.ERROR
    assert span.attributes["error.type"] == "ValueError"
    assert span.attributes["agentkit.operation.outcome"] == "error"


def test_cancellation_is_not_converted_or_marked_as_framework_error():
    telemetry, provider, exporter, _meter_provider, _reader = _stack()

    with pytest.raises(asyncio.CancelledError):
        with telemetry.start_invocation(
            framework="strands",
            agent_name="agent",
        ):
            raise asyncio.CancelledError()

    provider.force_flush()
    span = next(
        span
        for span in exporter.get_finished_spans()
        if span.name == "invoke_agent strands"
    )
    assert span.status.status_code is trace.StatusCode.UNSET
    assert span.attributes["agentkit.operation.outcome"] == "cancelled"
    assert "error.type" not in span.attributes


def test_finish_is_idempotent_and_context_can_move_to_stream_task():
    telemetry, provider, exporter, _meter_provider, _reader = _stack()
    invocation = telemetry.start_invocation(
        framework="agentcore",
        agent_name="agent",
    )
    invocation.detach_context()
    invocation.__enter__()
    invocation.detach_context()

    async def consume():
        invocation.attach_context()
        assert trace.get_current_span() is invocation.span
        invocation.finish()
        invocation.finish()

    asyncio.run(consume())
    provider.force_flush()
    assert [
        span.name
        for span in exporter.get_finished_spans()
        if span.name == "invoke_agent agentcore"
    ] == ["invoke_agent agentcore"]


def test_telemetry_export_failure_is_logged_and_does_not_break_invocation(caplog):
    class BrokenInstrument:
        def add(self, *args, **kwargs):
            raise RuntimeError("export failed")

        def record(self, *args, **kwargs):
            raise RuntimeError("export failed")

    telemetry, provider, exporter, _meter_provider, _reader = _stack()
    telemetry.invocations = BrokenInstrument()
    telemetry.duration = BrokenInstrument()
    telemetry.stream_events = BrokenInstrument()

    with telemetry.start_invocation(
        framework="langchain",
        agent_name="agent",
    ):
        pass

    provider.force_flush()
    assert "Failed to record AgentKit framework telemetry" in caplog.text
    span = next(
        span
        for span in exporter.get_finished_spans()
        if span.name == "invoke_agent langchain"
    )
    assert span.attributes["agentkit.operation.outcome"] == "success"


def test_span_end_failure_is_logged_and_does_not_escape(caplog):
    class BrokenSpan:
        def __init__(self):
            self.attributes = {}
            self.events = []

        def set_attribute(self, key, value):
            self.attributes[key] = value

        def add_event(self, name, attributes=None):
            self.events.append((name, attributes))

        def set_status(self, status):
            self.status = status

        def record_exception(self, exception):
            self.exception = exception

        def end(self):
            raise RuntimeError("end failed")

    class FakeTracer:
        def __init__(self):
            self.span = BrokenSpan()

        def start_span(self, *args, **kwargs):
            del args, kwargs
            return self.span

    telemetry, _provider, _exporter, _meter_provider, _reader = _stack()
    fake_tracer = FakeTracer()
    telemetry.tracer = fake_tracer

    with telemetry.start_invocation(framework="langchain", agent_name="agent"):
        pass

    assert fake_tracer.span.attributes["agentkit.operation.outcome"] == "success"
    assert "Failed to end AgentKit framework telemetry span." in caplog.text


def test_context_detach_failure_is_logged_and_does_not_escape(monkeypatch, caplog):
    telemetry, _provider, _exporter, _meter_provider, _reader = _stack()

    def fail_detach(token):
        del token
        raise RuntimeError("detach failed")

    monkeypatch.setattr(framework_mod, "safe_detach_context_token", fail_detach)

    with telemetry.start_invocation(framework="langgraph", agent_name="agent"):
        pass

    assert "Failed to detach AgentKit framework telemetry context." in caplog.text
