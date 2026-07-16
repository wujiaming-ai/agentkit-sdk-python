from __future__ import annotations

import asyncio
import json

import pytest
from opentelemetry import context as context_api
from opentelemetry import trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from agentkit.apps.agent_server_app import middleware as middleware_mod
from agentkit.apps.agent_server_app.middleware import AgentkitTelemetryHTTPMiddleware
from agentkit.apps.agent_server_app.telemetry import Telemetry

TRACE_ID = int("0123456789abcdef0123456789abcdef", 16)
PARENT_SPAN_ID = int("0123456789abcdef", 16)
TRACEPARENT = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"


def _telemetry_stack(monkeypatch):
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    telemetry = Telemetry(
        tracer=tracer_provider.get_tracer("test.server"),
        meter=meter_provider.get_meter("test.server"),
    )
    monkeypatch.setattr(middleware_mod, "telemetry", telemetry)
    return telemetry, tracer_provider, span_exporter, meter_provider, metric_reader


async def _request(app, *, headers=()):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await AgentkitTelemetryHTTPMiddleware(app)(
        {
            "type": "http",
            "method": "POST",
            "path": "/run_sse",
            "server": ("agent.example", 443),
            "headers": list(headers),
        },
        receive,
        send,
    )
    return sent


def _metric_names(reader: InMemoryMetricReader) -> set[str]:
    data = reader.get_metrics_data()
    return {
        metric.name
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


def test_w3c_parent_child_trace_metrics_and_header_redaction(monkeypatch):
    telemetry, tracer_provider, exporter, meter_provider, reader = _telemetry_stack(
        monkeypatch
    )
    child_tracer = tracer_provider.get_tracer("test.child")

    async def app(scope, receive, send):
        del scope, receive
        telemetry.set_invocation_context(session_id="session-1")
        with child_tracer.start_as_current_span("framework-child"):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

    asyncio.run(
        _request(
            app,
            headers=[
                (b"traceparent", TRACEPARENT.encode()),
                (b"content-type", b"application/json"),
                (b"authorization", b"Bearer top-secret"),
                (b"cookie", b"session=secret"),
                (b"x-api-key", b"secret-key"),
                (b"workloadaccesstoken", b"secret-workload-token"),
            ],
        )
    )
    tracer_provider.force_flush()
    meter_provider.force_flush()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    server = spans["POST /run_sse"]
    child = spans["framework-child"]
    assert server.context.trace_id == TRACE_ID
    assert server.parent.span_id == PARENT_SPAN_ID
    assert child.context.trace_id == TRACE_ID
    assert child.parent.span_id == server.context.span_id
    assert server.attributes["http.response.status_code"] == 200
    assert server.attributes["gen_ai.session.id"] == "session-1"
    recorded_headers = json.loads(server.attributes["gen_ai.request.headers"])
    assert recorded_headers == {
        "content-type": "application/json",
        "traceparent": TRACEPARENT,
    }
    assert "agentkit.server.request.duration" in _metric_names(reader)
    assert "agentkit.server.requests" in _metric_names(reader)
    assert "agentkit_runtime_operation_latency" in _metric_names(reader)


def test_existing_auto_instrumented_span_is_enriched_without_duplicate(monkeypatch):
    telemetry, tracer_provider, exporter, _meter_provider, _reader = _telemetry_stack(
        monkeypatch
    )
    tracer = tracer_provider.get_tracer("test.auto")

    async def app(scope, receive, send):
        del scope, receive
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def run():
        with tracer.start_as_current_span(
            "auto-http",
            kind=trace.SpanKind.SERVER,
        ):
            await _request(app)

    asyncio.run(run())
    tracer_provider.force_flush()

    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["auto-http"]
    assert spans[0].attributes["http.response.status_code"] == 204
    assert spans[0].attributes["agentkit.operation.outcome"] == "success"


def test_ended_current_span_is_not_reused_as_request_span(monkeypatch):
    _telemetry, tracer_provider, exporter, _meter_provider, _reader = _telemetry_stack(
        monkeypatch
    )
    tracer = tracer_provider.get_tracer("test.ended")
    ended_span = tracer.start_span("ended-auto-http", kind=trace.SpanKind.SERVER)
    token = context_api.attach(trace.set_span_in_context(ended_span))
    ended_span.end()

    async def app(scope, receive, send):
        del scope, receive
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    try:
        asyncio.run(_request(app))
    finally:
        context_api.detach(token)
    tracer_provider.force_flush()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert "POST /run_sse" in spans
    assert spans["POST /run_sse"].attributes["agentkit.operation.outcome"] == "success"


def test_exception_sets_error_status_and_keeps_original_exception(monkeypatch):
    _telemetry, tracer_provider, exporter, _meter_provider, _reader = (
        _telemetry_stack(monkeypatch)
    )
    failure = RuntimeError("framework failed")

    async def app(scope, receive, send):
        del scope, receive, send
        raise failure

    with pytest.raises(RuntimeError) as raised:
        asyncio.run(_request(app))
    assert raised.value is failure
    tracer_provider.force_flush()

    span = exporter.get_finished_spans()[0]
    assert span.status.status_code is trace.StatusCode.ERROR
    assert span.attributes["error.type"] == "RuntimeError"
    assert span.attributes["agentkit.operation.outcome"] == "error"
