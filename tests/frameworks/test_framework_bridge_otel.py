from __future__ import annotations

import asyncio
from types import SimpleNamespace

from google.genai import types
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from agentkit.frameworks import agentcore as agentcore_module
from agentkit.frameworks import langchain as langchain_module
from agentkit.frameworks import langgraph as langgraph_module
from agentkit.frameworks import strands as strands_module
from agentkit.frameworks.agentcore import BedrockAgentCoreAgentkitBridge
from agentkit.frameworks.langchain import LangChainAgentkitBridge
from agentkit.frameworks.langgraph import LangGraphAgentkitBridge
from agentkit.frameworks.strands import StrandsAgentkitBridge
from agentkit.observability.framework import FrameworkTelemetry


def _ctx(text: str = "hi"):
    return SimpleNamespace(
        invocation_id="invocation-1",
        branch=None,
        user_content=types.UserContent(parts=[types.Part(text=text)]),
        session=SimpleNamespace(id="session-1", app_name="app", user_id="user"),
    )


def _stack(monkeypatch):
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader])
    framework_telemetry = FrameworkTelemetry(
        tracer=tracer_provider.get_tracer("test.framework.bridge"),
        meter=meter_provider.get_meter("test.framework.bridge"),
    )
    for module in (
        agentcore_module,
        langchain_module,
        langgraph_module,
        strands_module,
    ):
        monkeypatch.setattr(module, "framework_telemetry", framework_telemetry)
    return tracer_provider, exporter, meter_provider, reader


async def _collect(bridge, ctx=None):
    events = []
    async for event in bridge._run_async_impl(ctx or _ctx()):
        events.append(event)
    return events


def _metric_names(reader: InMemoryMetricReader) -> set[str]:
    data = reader.get_metrics_data()
    if data is None:
        return set()
    return {
        metric.name
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
    }


def _assert_framework_child_span(exporter, parent, framework: str) -> None:
    spans = {span.name: span for span in exporter.get_finished_spans()}
    span = spans[f"invoke_agent {framework}"]
    assert span.parent.span_id == parent.get_span_context().span_id
    assert span.context.trace_id == parent.get_span_context().trace_id
    assert span.attributes["agentkit.framework.name"] == framework
    assert span.attributes["gen_ai.session.id"] == "session-1"
    assert span.attributes["agentkit.operation.outcome"] == "success"


def test_langchain_bridge_creates_child_framework_span(monkeypatch):
    tracer_provider, exporter, meter_provider, reader = _stack(monkeypatch)

    class Runnable:
        async def astream(self, payload):
            assert payload == {"input": "hi"}
            yield "hello"

    tracer = tracer_provider.get_tracer("test.parent")
    with tracer.start_as_current_span("server") as parent:
        events = asyncio.run(_collect(LangChainAgentkitBridge(Runnable())))

    assert events[-1].partial is False
    tracer_provider.force_flush()
    meter_provider.force_flush()
    _assert_framework_child_span(exporter, parent, "langchain")
    assert "agentkit.framework.invocation.duration" in _metric_names(reader)


def test_langgraph_bridge_creates_child_framework_span(monkeypatch):
    tracer_provider, exporter, meter_provider, _reader = _stack(monkeypatch)

    class Graph:
        async def astream(self, payload, stream_mode=None, version=None):
            assert "messages" in payload
            assert stream_mode == "updates"
            assert version == "v2"
            yield ("updates", {"answer": "graph answer"})

    tracer = tracer_provider.get_tracer("test.parent")
    with tracer.start_as_current_span("server") as parent:
        events = asyncio.run(_collect(LangGraphAgentkitBridge(Graph())))

    assert events[-1].partial is False
    tracer_provider.force_flush()
    _assert_framework_child_span(exporter, parent, "langgraph")


def test_strands_bridge_creates_child_framework_span(monkeypatch):
    tracer_provider, exporter, meter_provider, reader = _stack(monkeypatch)

    class Agent:
        async def stream_async(self, prompt, invocation_state=None, idempotency_token=None):
            assert prompt == "hi"
            assert invocation_state["agentkit"]["session_id"] == "session-1"
            assert idempotency_token == "invocation-1"
            yield {"data": "he"}
            yield {
                "result": SimpleNamespace(
                    stop_reason="end_turn",
                    message={"role": "assistant", "content": [{"text": "hello"}]},
                    structured_output=None,
                    interrupts=[],
                )
            }

    tracer = tracer_provider.get_tracer("test.parent")
    with tracer.start_as_current_span("server") as parent:
        events = asyncio.run(_collect(StrandsAgentkitBridge(Agent())))

    assert events[-1].partial is False
    tracer_provider.force_flush()
    meter_provider.force_flush()
    _assert_framework_child_span(exporter, parent, "strands")
    assert "agentkit.framework.stream.events" in _metric_names(reader)


def test_agentcore_bridge_creates_child_framework_span(monkeypatch):
    tracer_provider, exporter, meter_provider, reader = _stack(monkeypatch)

    class AgentCoreLikeApp:
        def __init__(self):
            self.handlers = {"main": self.invoke}

        async def invoke(self, payload, context=None):
            assert payload == {"prompt": "hi"}
            assert context.session_id == "session-1"
            yield {"type": "response.output_text.delta", "delta": "hi"}

    tracer = tracer_provider.get_tracer("test.parent")
    with tracer.start_as_current_span("server") as parent:
        events = asyncio.run(
            _collect(BedrockAgentCoreAgentkitBridge(AgentCoreLikeApp()))
        )

    assert events[-1].partial is False
    tracer_provider.force_flush()
    meter_provider.force_flush()
    _assert_framework_child_span(exporter, parent, "agentcore")
    assert "agentkit.framework.time_to_first_output" in _metric_names(reader)
