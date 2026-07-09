import asyncio
from types import SimpleNamespace

import pytest
from google.genai import types

pytest.importorskip("langchain_core.messages")

from agentkit.frameworks._common import UnsupportedFrameworkAgentError
from agentkit.frameworks.langgraph import LANGGRAPH_PENDING_INTERRUPT_STATE_KEY, LangGraphAgentkitBridge


def _ctx(text: str = "hi", session_id: str | None = None, state: dict | None = None):
    return SimpleNamespace(
        invocation_id="invocation",
        branch=None,
        user_content=types.UserContent(parts=[types.Part(text=text)]),
        session=SimpleNamespace(id=session_id, state=state if state is not None else {}) if session_id else None,
    )


def _event_text(event) -> str:
    if event.content is None:
        return ""
    return "".join(part.text or "" for part in event.content.parts)


def _collect_events(bridge: LangGraphAgentkitBridge, ctx=None):
    async def run():
        events = []
        async for event in bridge._run_async_impl(ctx or _ctx()):
            actions = getattr(event, "actions", None)
            events.append(
                {
                    "partial": event.partial,
                    "text": _event_text(event),
                    "error_code": getattr(event, "error_code", None),
                    "interrupted": getattr(event, "interrupted", None),
                    "metadata": getattr(event, "custom_metadata", None),
                    "state_delta": dict(actions.state_delta) if actions and actions.state_delta else {},
                }
            )
        return events

    return asyncio.run(run())


def _visible(events):
    return [{"partial": event["partial"], "text": event["text"]} for event in events]


def test_message_stream_suppresses_state_updates():
    class FakeGraph:
        async def astream(self, payload, stream_mode=None):
            assert stream_mode == ["messages", "updates"]
            yield ("updates", {"classify": {"route": "handoff"}})
            yield ("messages", (SimpleNamespace(content="final answer"), {}))

    events = _collect_events(
        LangGraphAgentkitBridge(FakeGraph(), name="lg_messages"),
    )

    assert _visible(events) == [
        {"partial": True, "text": "final answer"},
        {"partial": False, "text": "final answer"},
    ]


def test_message_stream_cumulative_chunks_are_emitted_as_deltas():
    class CumulativeGraph:
        async def astream(self, payload, stream_mode=None):
            del payload, stream_mode
            yield ("messages", (SimpleNamespace(content="a"), {}))
            yield ("messages", (SimpleNamespace(content="ab"), {}))

    events = _collect_events(
        LangGraphAgentkitBridge(CumulativeGraph(), name="lg_cumulative"),
    )

    assert _visible(events) == [
        {"partial": True, "text": "a"},
        {"partial": True, "text": "b"},
        {"partial": False, "text": "ab"},
    ]


def test_update_stream_is_used_when_graph_emits_no_messages():
    class UpdateOnlyGraph:
        async def astream(self, payload, stream_mode=None):
            yield ("updates", {"result": {"answer": "fallback answer"}})

    events = _collect_events(
        LangGraphAgentkitBridge(UpdateOnlyGraph(), name="lg_updates"),
    )

    assert _visible(events) == [
        {"partial": True, "text": "fallback answer"},
        {"partial": False, "text": "fallback answer"},
    ]


def test_v2_stream_parts_are_supported():
    class V2Graph:
        async def astream(self, payload, stream_mode=None, version=None):
            assert stream_mode == ["messages", "updates"]
            assert version == "v2"
            yield {
                "type": "updates",
                "data": {"classify": {"route": "self_serve"}},
            }
            yield {
                "type": "messages",
                "data": (SimpleNamespace(content="v2 answer"), {}),
            }

    events = _collect_events(
        LangGraphAgentkitBridge(V2Graph(), name="lg_v2"),
    )

    assert _visible(events) == [
        {"partial": True, "text": "v2 answer"},
        {"partial": False, "text": "v2 answer"},
    ]


def test_custom_input_key_supports_non_message_workflow_state():
    class WorkflowGraph:
        async def astream(self, payload, stream_mode=None, version=None):
            assert payload == {"question": "hi"}
            assert stream_mode == ["messages", "updates"]
            assert version == "v2"
            yield {
                "type": "updates",
                "data": {"answer": f"workflow:{payload['question']}"},
            }

    events = _collect_events(
        LangGraphAgentkitBridge(
            WorkflowGraph(),
            name="lg_workflow",
            input_key="question",
        ),
    )

    assert _visible(events) == [
        {"partial": True, "text": "workflow:hi"},
        {"partial": False, "text": "workflow:hi"},
    ]


def test_session_id_is_passed_as_langgraph_thread_id_for_streaming():
    class ThreadAwareGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            assert payload == {"question": "hi"}
            assert config == {"configurable": {"thread_id": "session-123"}}
            assert stream_mode == ["messages", "updates"]
            assert version == "v2"
            yield {"type": "updates", "data": {"answer": config["configurable"]["thread_id"]}}

    events = _collect_events(
        LangGraphAgentkitBridge(
            ThreadAwareGraph(),
            name="lg_thread",
            input_key="question",
        ),
        _ctx(session_id="session-123"),
    )

    assert _visible(events) == [
        {"partial": True, "text": "session-123"},
        {"partial": False, "text": "session-123"},
    ]


def test_session_id_is_passed_as_langgraph_thread_id_for_invoke_fallback():
    class ThreadAwareGraph:
        async def ainvoke(self, payload, config=None):
            assert payload == {"question": "hi"}
            assert config == {"configurable": {"thread_id": "session-456"}}
            return {"answer": config["configurable"]["thread_id"]}

    events = _collect_events(
        LangGraphAgentkitBridge(
            ThreadAwareGraph(),
            name="lg_thread_invoke",
            input_key="question",
        ),
        _ctx(session_id="session-456"),
    )

    assert _visible(events) == [{"partial": False, "text": "session-456"}]


def test_langgraph_interrupt_update_is_exposed_as_explicit_event():
    class InterruptingGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            del payload, config, stream_mode, version
            yield {
                "type": "updates",
                "data": {
                    "__interrupt__": (
                        SimpleNamespace(id="interrupt-1", value={"prompt": "approve?"}),
                    )
                },
            }

    events = _collect_events(
        LangGraphAgentkitBridge(InterruptingGraph(), name="lg_interrupt"),
    )

    assert events == [
        {
            "partial": None,
            "text": "",
            "error_code": "LANGGRAPH_INTERRUPT",
            "interrupted": True,
            "metadata": {
                "langgraph_interrupt": [
                    {"id": "interrupt-1", "value": {"prompt": "approve?"}},
                ]
            },
            "state_delta": {},
        }
    ]


def test_langgraph_interrupt_is_recorded_in_agentkit_session_state():
    class InterruptingGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            del payload, stream_mode, version
            yield {
                "type": "updates",
                "data": {
                    "__interrupt__": (
                        SimpleNamespace(id="interrupt-1", value={"prompt": "approve?"}),
                    )
                },
            }

    events = _collect_events(
        LangGraphAgentkitBridge(InterruptingGraph(), name="lg_interrupt"),
        _ctx(session_id="session-1"),
    )

    assert events == [
        {
            "partial": None,
            "text": "",
            "error_code": "LANGGRAPH_INTERRUPT",
            "interrupted": True,
            "metadata": {
                "langgraph_interrupt": [
                    {"id": "interrupt-1", "value": {"prompt": "approve?"}},
                ]
            },
            "state_delta": {
                LANGGRAPH_PENDING_INTERRUPT_STATE_KEY: {
                    "agent": "lg_interrupt",
                    "thread_id": "session-1",
                    "interrupts": [
                        {"id": "interrupt-1", "value": {"prompt": "approve?"}},
                    ],
                }
            },
        }
    ]


def test_pending_interrupt_resumes_with_text_input_and_clears_state():
    from langgraph.types import Command

    class ResumableGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            assert isinstance(payload, Command)
            assert payload.resume == "approved"
            assert config == {"configurable": {"thread_id": "session-1"}}
            assert stream_mode == ["messages", "updates"]
            assert version == "v2"
            yield {"type": "updates", "data": {"answer": f"resumed:{payload.resume}"}}

    state = {
        LANGGRAPH_PENDING_INTERRUPT_STATE_KEY: {
            "agent": "lg_resume",
            "thread_id": "session-1",
            "interrupts": [{"id": "interrupt-1", "value": {"prompt": "approve?"}}],
        }
    }

    events = _collect_events(
        LangGraphAgentkitBridge(ResumableGraph(), name="lg_resume"),
        _ctx("approved", session_id="session-1", state=state),
    )

    assert _visible(events) == [
        {"partial": True, "text": "resumed:approved"},
        {"partial": False, "text": "resumed:approved"},
    ]
    assert events[-1]["state_delta"] == {LANGGRAPH_PENDING_INTERRUPT_STATE_KEY: None}


def test_pending_interrupt_resumes_with_json_input():
    from langgraph.types import Command

    class ResumableGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            del config, stream_mode, version
            assert isinstance(payload, Command)
            assert payload.resume == {"decision": "yes"}
            yield {"type": "updates", "data": {"answer": payload.resume["decision"]}}

    state = {
        LANGGRAPH_PENDING_INTERRUPT_STATE_KEY: {
            "agent": "lg_json_resume",
            "thread_id": "session-1",
            "interrupts": [{"id": "interrupt-1", "value": {"prompt": "approve?"}}],
        }
    }

    events = _collect_events(
        LangGraphAgentkitBridge(ResumableGraph(), name="lg_json_resume"),
        _ctx('{"decision":"yes"}', session_id="session-1", state=state),
    )

    assert _visible(events) == [
        {"partial": True, "text": "yes"},
        {"partial": False, "text": "yes"},
    ]
    assert events[-1]["state_delta"] == {LANGGRAPH_PENDING_INTERRUPT_STATE_KEY: None}


def test_real_interrupt_resume_from_compiled_graph_is_supported():
    from typing import TypedDict

    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import interrupt

    class State(TypedDict, total=False):
        question: str
        answer: str

    def ask(state: State):
        resume = interrupt({"prompt": f"approve {state['question']}?"})
        return {"answer": f"approved:{resume['decision']}:{state['question']}"}

    builder = StateGraph(State)
    builder.add_node("ask", ask)
    builder.add_edge(START, "ask")
    builder.add_edge("ask", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    class SyncOnlyGraph:
        def stream(self, payload, **kwargs):
            yield from graph.stream(payload, **kwargs)

    bridge = LangGraphAgentkitBridge(SyncOnlyGraph(), name="lg_real_hitl", input_key="question")
    session_state: dict = {}

    first = _collect_events(bridge, _ctx("deploy", session_id="thread-1", state=session_state))
    assert first[0]["error_code"] == "LANGGRAPH_INTERRUPT"
    assert first[0]["state_delta"][LANGGRAPH_PENDING_INTERRUPT_STATE_KEY]["thread_id"] == "thread-1"
    session_state.update(first[0]["state_delta"])

    second = _collect_events(
        bridge,
        _ctx('{"decision":"yes"}', session_id="thread-1", state=session_state),
    )

    assert _visible(second) == [
        {"partial": True, "text": "approved:yes:deploy"},
        {"partial": False, "text": "approved:yes:deploy"},
    ]
    assert second[-1]["state_delta"] == {LANGGRAPH_PENDING_INTERRUPT_STATE_KEY: None}


def test_real_command_updates_from_compiled_graph_are_supported():
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command

    class State(TypedDict):
        question: str
        answer: str

    def route(state: State):
        return Command(goto="answer", update={"answer": f"command:{state['question']}"})

    def answer(state: State):
        return {"answer": f"{state['answer']}:done"}

    builder = StateGraph(State)
    builder.add_node("route", route)
    builder.add_node("answer", answer)
    builder.add_edge(START, "route")
    builder.add_edge("answer", END)
    graph = builder.compile()

    class SyncOnlyGraph:
        def stream(self, payload, **kwargs):
            yield from graph.stream(payload, **kwargs)

    events = _collect_events(
        LangGraphAgentkitBridge(
            SyncOnlyGraph(),
            name="lg_command",
            input_key="question",
        ),
    )

    assert _visible(events) == [
        {"partial": True, "text": "command:hi:done"},
        {"partial": False, "text": "command:hi:done"},
    ]


def test_ainvoke_is_used_when_streaming_is_not_available():
    class AsyncGraph:
        async def ainvoke(self, payload):
            assert "messages" in payload
            return {"messages": [SimpleNamespace(content="async graph answer")]}

    events = _collect_events(
        LangGraphAgentkitBridge(AsyncGraph(), name="lg_async"),
    )

    assert _visible(events) == [{"partial": False, "text": "async graph answer"}]


def test_sync_invoke_is_used_when_streaming_is_not_available():
    class SyncGraph:
        def invoke(self, payload):
            assert "messages" in payload
            return {"answer": "sync graph answer"}

    events = _collect_events(
        LangGraphAgentkitBridge(SyncGraph(), name="lg_sync"),
    )

    assert _visible(events) == [{"partial": False, "text": "sync graph answer"}]


def test_sync_stream_is_used_when_async_streaming_is_not_available():
    class SyncStreamGraph:
        def stream(self, payload, stream_mode=None, version=None):
            assert "messages" in payload
            assert stream_mode == ["messages", "updates"]
            assert version == "v2"
            yield {"type": "messages", "data": (SimpleNamespace(content="sync graph"), {})}

    events = _collect_events(
        LangGraphAgentkitBridge(SyncStreamGraph(), name="lg_sync_stream"),
    )

    assert _visible(events) == [
        {"partial": True, "text": "sync graph"},
        {"partial": False, "text": "sync graph"},
    ]


def test_stream_mode_fallback_only_handles_call_signature_errors():
    class LegacyGraph:
        async def astream(self, payload):
            yield SimpleNamespace(content="legacy stream")

    events = _collect_events(
        LangGraphAgentkitBridge(LegacyGraph(), name="lg_legacy"),
    )

    assert _visible(events) == [
        {"partial": True, "text": "legacy stream"},
        {"partial": False, "text": "legacy stream"},
    ]


def test_stream_runtime_type_errors_are_not_retried_as_legacy_signature():
    class BrokenGraph:
        async def astream(self, payload, stream_mode=None):
            del payload, stream_mode
            raise TypeError("node execution failed")
            yield None

    bridge = LangGraphAgentkitBridge(BrokenGraph(), name="lg_broken")

    with pytest.raises(TypeError, match="node execution failed"):
        _collect_events(bridge)


def test_unsupported_entry_raises_actionable_error():
    bridge = LangGraphAgentkitBridge(object(), name="lg_bad")

    with pytest.raises(UnsupportedFrameworkAgentError, match="compiled graph-like"):
        _collect_events(bridge)
