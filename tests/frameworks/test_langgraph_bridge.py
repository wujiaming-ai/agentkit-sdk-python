import asyncio
from types import SimpleNamespace

import pytest
from google.genai import types

pytest.importorskip("langchain_core.messages")

from agentkit.frameworks._common import UnsupportedFrameworkAgentError
import agentkit.frameworks.langgraph as langgraph_module
from agentkit.frameworks.langgraph import LANGGRAPH_PENDING_INTERRUPT_STATE_KEY, LangGraphAgentkitBridge


def _ctx(
    text: str = "hi",
    session_id: str | None = None,
    state: dict | None = None,
    app_name: str | None = None,
    user_id: str | None = None,
):
    session = None
    if session_id:
        session = SimpleNamespace(
            id=session_id,
            app_name=app_name,
            user_id=user_id,
            state=state if state is not None else {},
        )
    return SimpleNamespace(
        invocation_id="invocation",
        branch=None,
        user_content=types.UserContent(parts=[types.Part(text=text)]),
        session=session,
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


def test_default_stream_uses_final_updates_without_partial_tokens():
    class FakeGraph:
        async def astream(self, payload, stream_mode=None):
            assert stream_mode == "updates"
            yield ("updates", {"route": "handoff"})
            yield ("updates", {"classify": {"route": "handoff"}})
            yield ("updates", {"finalize": {"final": "final answer"}})

    events = _collect_events(
        LangGraphAgentkitBridge(FakeGraph(), name="lg_updates"),
    )

    assert _visible(events) == [{"partial": False, "text": "final answer"}]


def test_default_message_stream_does_not_leak_internal_llm_tokens():
    class FakeGraph:
        async def astream(self, payload, stream_mode=None):
            assert stream_mode == "updates"
            yield ("messages", (SimpleNamespace(content="internal llm token"), {}))
            yield ("updates", {"answer": {"answer": "LG_PROD_OK\nfinal graph answer"}})

    events = _collect_events(
        LangGraphAgentkitBridge(FakeGraph(), name="lg_final_update"),
    )

    assert _visible(events) == [{"partial": False, "text": "LG_PROD_OK\nfinal graph answer"}]


def test_stream_nodes_emit_matching_message_chunks_as_deltas():
    class CumulativeGraph:
        async def astream(self, payload, stream_mode=None, version=None):
            del payload
            assert stream_mode == ["messages", "updates"]
            assert version == "v2"
            yield ("messages", (SimpleNamespace(content="ignore"), {"langgraph_node": "classify"}))
            yield ("messages", (SimpleNamespace(content="a"), {"langgraph_node": "final_answer"}))
            yield ("messages", (SimpleNamespace(content="ab"), {"langgraph_node": "final_answer"}))
            yield ("messages", (SimpleNamespace(content="ab"), {"langgraph_node": "final_answer"}))

    events = _collect_events(
        LangGraphAgentkitBridge(
            CumulativeGraph(),
            name="lg_cumulative",
            stream_nodes=("final_answer",),
        ),
    )

    assert _visible(events) == [
        {"partial": True, "text": "a"},
        {"partial": True, "text": "b"},
        {"partial": False, "text": "ab"},
    ]


def test_stream_nodes_emit_native_langgraph_token_deltas():
    class DeltaGraph:
        async def astream(self, payload, stream_mode=None, version=None):
            del payload
            assert stream_mode == ["messages", "updates"]
            assert version == "v2"
            yield (
                "messages",
                (SimpleNamespace(content="hel"), {"langgraph_node": "chatbot"}),
            )
            yield (
                "messages",
                (SimpleNamespace(content="lo"), {"langgraph_node": "chatbot"}),
            )
            yield (
                "messages",
                (SimpleNamespace(content=""), {"langgraph_node": "chatbot"}),
            )

    events = _collect_events(
        LangGraphAgentkitBridge(
            DeltaGraph(),
            name="lg_delta",
            stream_nodes=("chatbot",),
        ),
    )

    assert _visible(events) == [
        {"partial": True, "text": "hel"},
        {"partial": True, "text": "lo"},
        {"partial": False, "text": "hello"},
    ]


def test_stream_nodes_ignore_message_chunks_without_matching_metadata():
    class MetadataGraph:
        async def astream(self, payload, stream_mode=None, version=None):
            del payload, version
            assert stream_mode == ["messages", "updates"]
            yield ("messages", (SimpleNamespace(content="no metadata"), {}))
            yield {"type": "messages", "data": {"metadata": "bad", "content": "bad metadata"}}
            yield ("messages", (SimpleNamespace(content="wrong node"), {"langgraph_node": "classify"}))
            yield ("updates", {"finalize": {"final": "safe final"}})

    events = _collect_events(
        LangGraphAgentkitBridge(
            MetadataGraph(),
            name="lg_filtered",
            stream_nodes=("final_answer",),
        ),
    )

    assert _visible(events) == [{"partial": False, "text": "safe final"}]


def test_stream_node_string_and_dict_metadata_are_supported():
    class DictMetadataGraph:
        async def astream(self, payload, stream_mode=None, version=None):
            del payload, version
            assert stream_mode == ["messages", "updates"]
            yield {
                "type": "messages",
                "data": {
                    "metadata": {"langgraph_node": "chatbot"},
                    "content": "dict-token",
                },
            }

    events = _collect_events(
        LangGraphAgentkitBridge(
            DictMetadataGraph(),
            name="lg_dict_metadata",
            stream_nodes="chatbot",
        ),
    )

    assert _visible(events) == [
        {"partial": True, "text": "dict-token"},
        {"partial": False, "text": "dict-token"},
    ]


def test_update_stream_is_used_when_graph_emits_no_messages():
    class UpdateOnlyGraph:
        async def astream(self, payload, stream_mode=None):
            assert stream_mode == "updates"
            yield ("updates", {"result": {"answer": "fallback answer"}})

    events = _collect_events(
        LangGraphAgentkitBridge(UpdateOnlyGraph(), name="lg_updates"),
    )

    assert _visible(events) == [{"partial": False, "text": "fallback answer"}]


def test_v2_stream_parts_are_supported():
    class V2Graph:
        async def astream(self, payload, stream_mode=None, version=None):
            assert stream_mode == "updates"
            assert version == "v2"
            yield {
                "type": "updates",
                "data": {"classify": {"route": "self_serve"}},
            }
            yield {
                "type": "updates",
                "data": {"finalize": {"final": "v2 answer"}},
            }

    events = _collect_events(
        LangGraphAgentkitBridge(V2Graph(), name="lg_v2"),
    )

    assert _visible(events) == [{"partial": False, "text": "v2 answer"}]


def test_raw_state_updates_do_not_leak_intermediate_fields():
    class RawUpdateGraph:
        async def astream(self, payload, stream_mode=None, version=None):
            assert stream_mode == "updates"
            assert version == "v2"
            yield {
                "classify_intent": {
                    "intent": "general",
                    "needs_compliance": False,
                    "audience_hint": "",
                }
            }
            yield {"finalize": {"final": "hello", "messages": [SimpleNamespace(content="hello")]}}

    events = _collect_events(
        LangGraphAgentkitBridge(RawUpdateGraph(), name="lg_raw_updates"),
    )

    assert _visible(events) == [{"partial": False, "text": "hello"}]


def test_raw_internal_state_update_without_output_fields_emits_nothing():
    class InternalOnlyGraph:
        async def astream(self, payload, stream_mode=None, version=None):
            del payload, stream_mode, version
            yield {
                "classify_intent": {
                    "intent": "general",
                    "needs_compliance": False,
                    "audience_hint": "",
                }
            }

    events = _collect_events(
        LangGraphAgentkitBridge(InternalOnlyGraph(), name="lg_internal_only"),
    )

    assert _visible(events) == []


def test_internal_content_field_does_not_leak_as_output():
    class InternalContentGraph:
        async def astream(self, payload, stream_mode=None, version=None):
            del payload, stream_mode, version
            yield {"retrieve": {"content": "internal document text"}}
            yield {"finalize": {"final": "public answer"}}

    events = _collect_events(
        LangGraphAgentkitBridge(InternalContentGraph(), name="lg_internal_content"),
    )

    assert _visible(events) == [{"partial": False, "text": "public answer"}]


def test_raw_state_update_with_messages_reducer_outputs_latest_message():
    class RawMessagesGraph:
        async def astream(self, payload, stream_mode=None, version=None):
            del payload, stream_mode, version
            yield {"agent": {"messages": [SimpleNamespace(content="latest answer")]}}

    events = _collect_events(
        LangGraphAgentkitBridge(RawMessagesGraph(), name="lg_raw_messages"),
    )

    assert _visible(events) == [{"partial": False, "text": "latest answer"}]


def test_update_stream_supports_final_output_field():
    class FinalFieldGraph:
        async def astream(self, payload, stream_mode=None, version=None):
            del payload, stream_mode, version
            yield ("updates", {"finalize": {"final": "final field answer"}})

    events = _collect_events(
        LangGraphAgentkitBridge(FinalFieldGraph(), name="lg_final_field"),
    )

    assert _visible(events) == [{"partial": False, "text": "final field answer"}]


def test_raw_langgraph_interrupt_update_is_exposed_as_explicit_event():
    class RawInterruptingGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            del payload, config, stream_mode, version
            yield {
                "__interrupt__": (
                    SimpleNamespace(id="interrupt-raw", value={"prompt": "continue?"}),
                )
            }

    events = _collect_events(
        LangGraphAgentkitBridge(RawInterruptingGraph(), name="lg_raw_interrupt"),
    )

    assert events == [
        {
            "partial": None,
            "text": "",
            "error_code": "LANGGRAPH_INTERRUPT",
            "interrupted": True,
            "metadata": {
                "langgraph_interrupt": [
                    {"id": "interrupt-raw", "value": {"prompt": "continue?"}},
                ]
            },
            "state_delta": {},
        }
    ]


def test_custom_input_key_supports_non_message_workflow_state():
    class WorkflowGraph:
        async def astream(self, payload, stream_mode=None, version=None):
            assert payload == {"question": "hi"}
            assert stream_mode == "updates"
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

    assert _visible(events) == [{"partial": False, "text": "workflow:hi"}]


def test_session_id_is_passed_as_langgraph_thread_id_for_update_streaming():
    class ThreadAwareGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            assert payload == {"question": "hi"}
            assert config == {"configurable": {"thread_id": "session-123"}}
            assert stream_mode == "updates"
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

    assert _visible(events) == [{"partial": False, "text": "session-123"}]


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


def test_graph_factory_receives_runnable_config_for_each_agentkit_session():
    calls = []

    class FactoryGraph:
        def __init__(self, config):
            self.config = config

        async def astream(self, payload, config=None, stream_mode=None, version=None):
            assert config is self.config
            assert stream_mode == "updates"
            assert version == "v2"
            yield {
                "type": "updates",
                "data": {
                    "answer": f"factory:{payload['question']}:{config['configurable']['thread_id']}"
                },
            }

    def build_graph(config):
        calls.append(config)
        return FactoryGraph(config)

    events = _collect_events(
        LangGraphAgentkitBridge(
            build_graph,
            name="lg_factory",
            input_key="question",
            graph_factory=True,
        ),
        _ctx("hello", session_id="thread-1"),
    )

    assert len(calls) == 1
    assert calls[0] == {"configurable": {"thread_id": "thread-1"}}
    assert _visible(events) == [
        {"partial": False, "text": "factory:hello:thread-1"}
    ]


def test_graph_factory_supports_keyword_only_config_and_hashed_thread_id():
    calls = []

    class FactoryGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            del payload, stream_mode, version
            yield {
                "type": "updates",
                "data": {"answer": config["configurable"]["thread_id"]},
            }

    def build_graph(*, config):
        calls.append(config)
        return FactoryGraph()

    events = _collect_events(
        LangGraphAgentkitBridge(build_graph, name="lg_kw_factory", graph_factory=True),
        _ctx(session_id="same-visible-session", app_name="app", user_id="user"),
    )

    assert len(calls) == 1
    thread_id = calls[0]["configurable"]["thread_id"]
    assert thread_id.startswith("agentkit:")
    assert "same-visible-session" not in thread_id
    assert _visible(events) == [{"partial": False, "text": thread_id}]


def test_graph_factory_passes_optional_config_parameter():
    calls = []

    class FactoryGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            del payload, stream_mode, version
            yield {
                "type": "updates",
                "data": {"answer": config["configurable"]["thread_id"]},
            }

    def build_graph(config=None):
        calls.append(config)
        return FactoryGraph()

    events = _collect_events(
        LangGraphAgentkitBridge(
            build_graph,
            name="lg_optional_factory",
            graph_factory=True,
        ),
        _ctx(session_id="optional-config-session"),
    )

    assert calls == [{"configurable": {"thread_id": "optional-config-session"}}]
    assert _visible(events) == [
        {"partial": False, "text": "optional-config-session"}
    ]


def test_graph_factory_passes_config_to_varargs_factory():
    calls = []

    class FactoryGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            del payload, config, stream_mode, version
            yield {"type": "updates", "data": {"answer": "varargs ok"}}

    def build_graph(*args):
        calls.append(args)
        return FactoryGraph()

    events = _collect_events(
        LangGraphAgentkitBridge(build_graph, name="lg_varargs_factory", graph_factory=True),
        _ctx(session_id="varargs-session"),
    )

    assert calls == [({"configurable": {"thread_id": "varargs-session"}},)]
    assert _visible(events) == [{"partial": False, "text": "varargs ok"}]


def test_graph_factory_with_no_config_parameter_is_called_without_args():
    calls = []

    class FactoryGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            del payload, config, stream_mode, version
            yield {"type": "updates", "data": {"answer": "no config ok"}}

    def build_graph(verbose=False):
        calls.append(verbose)
        return FactoryGraph()

    events = _collect_events(
        LangGraphAgentkitBridge(build_graph, name="lg_no_config_factory", graph_factory=True),
        _ctx(session_id="no-config-session"),
    )

    assert calls == [False]
    assert _visible(events) == [{"partial": False, "text": "no config ok"}]


def test_graph_factory_passes_optional_keyword_only_config_parameter():
    calls = []

    class FactoryGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            del payload, config, stream_mode, version
            yield {"type": "updates", "data": {"answer": "kw optional ok"}}

    def build_graph(*, runnable_config=None):
        calls.append(runnable_config)
        return FactoryGraph()

    events = _collect_events(
        LangGraphAgentkitBridge(build_graph, name="lg_kw_optional_factory", graph_factory=True),
        _ctx(session_id="kw-optional-session"),
    )

    assert calls == [{"configurable": {"thread_id": "kw-optional-session"}}]
    assert _visible(events) == [{"partial": False, "text": "kw optional ok"}]


def test_graph_factory_falls_back_to_positional_config_when_signature_unavailable(monkeypatch):
    calls = []

    class FactoryGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            del payload, config, stream_mode, version
            yield {"type": "updates", "data": {"answer": "signature fallback ok"}}

    def build_graph(config):
        calls.append(config)
        return FactoryGraph()

    def fail_signature(factory):
        del factory
        raise ValueError("signature unavailable")

    monkeypatch.setattr(langgraph_module.inspect, "signature", fail_signature)

    events = _collect_events(
        LangGraphAgentkitBridge(build_graph, name="lg_signature_fallback", graph_factory=True),
        _ctx(session_id="signature-session"),
    )

    assert calls == [{"configurable": {"thread_id": "signature-session"}}]
    assert _visible(events) == [{"partial": False, "text": "signature fallback ok"}]


def test_graph_factory_supports_async_factory_returning_graph():
    class FactoryGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            del payload, config, stream_mode, version
            yield {"type": "updates", "data": {"answer": "async factory ok"}}

    async def build_graph(config):
        assert config == {"configurable": {"thread_id": "async-session"}}
        return FactoryGraph()

    events = _collect_events(
        LangGraphAgentkitBridge(build_graph, name="lg_async_factory", graph_factory=True),
        _ctx(session_id="async-session"),
    )

    assert _visible(events) == [{"partial": False, "text": "async factory ok"}]


def test_graph_factory_rejects_extra_required_parameters_with_clear_error():
    def build_graph(config, settings):
        del config, settings

    with pytest.raises(UnsupportedFrameworkAgentError, match="required parameters found: config, settings"):
        _collect_events(
            LangGraphAgentkitBridge(
                build_graph,
                name="lg_bad_factory",
                graph_factory=True,
            ),
            _ctx(session_id="bad-factory"),
        )


def test_graph_factory_flag_requires_callable_entry():
    with pytest.raises(UnsupportedFrameworkAgentError, match="requires a callable entry"):
        _collect_events(
            LangGraphAgentkitBridge(
                object(),
                name="lg_non_callable_factory",
                graph_factory=True,
            ),
            _ctx(session_id="bad-factory"),
        )


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


def test_interrupt_payload_handles_non_json_and_empty_interrupt_values():
    class NonJsonInterruptingGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            del payload, config, stream_mode, version
            yield {
                "type": "updates",
                "data": {
                    "__interrupt__": (
                        SimpleNamespace(id="interrupt-obj", value=object()),
                    )
                },
            }

    events = _collect_events(
        LangGraphAgentkitBridge(NonJsonInterruptingGraph(), name="lg_non_json_interrupt"),
    )

    assert events[0]["error_code"] == "LANGGRAPH_INTERRUPT"
    assert events[0]["metadata"]["langgraph_interrupt"][0]["id"] == "interrupt-obj"
    assert isinstance(events[0]["metadata"]["langgraph_interrupt"][0]["value"], str)

    class EmptyInterruptingGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            del payload, config, stream_mode, version
            yield {"type": "updates", "data": {"__interrupt__": []}}

    empty_events = _collect_events(
        LangGraphAgentkitBridge(EmptyInterruptingGraph(), name="lg_empty_interrupt"),
    )

    assert empty_events[0]["metadata"] == {"langgraph_interrupt": []}


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
            assert stream_mode == "updates"
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

    assert _visible(events) == [{"partial": False, "text": "resumed:approved"}]
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

    assert _visible(events) == [{"partial": False, "text": "yes"}]
    assert events[-1]["state_delta"] == {LANGGRAPH_PENDING_INTERRUPT_STATE_KEY: None}


def test_pending_interrupt_resumes_with_invalid_json_as_plain_text():
    from langgraph.types import Command

    class ResumableGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            del config, stream_mode, version
            assert isinstance(payload, Command)
            assert payload.resume == "{bad-json"
            yield {"type": "updates", "data": {"answer": payload.resume}}

    state = {
        LANGGRAPH_PENDING_INTERRUPT_STATE_KEY: {
            "agent": "lg_invalid_json_resume",
            "thread_id": "session-1",
            "interrupts": [{"id": "interrupt-1", "value": {"prompt": "approve?"}}],
        }
    }

    events = _collect_events(
        LangGraphAgentkitBridge(ResumableGraph(), name="lg_invalid_json_resume"),
        _ctx("{bad-json", session_id="session-1", state=state),
    )

    assert _visible(events) == [{"partial": False, "text": "{bad-json"}]


def test_pending_interrupt_for_other_agent_is_ignored():
    class RegularGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            del config, stream_mode, version
            assert "messages" in payload
            yield {"type": "updates", "data": {"answer": "fresh run"}}

    state = {
        LANGGRAPH_PENDING_INTERRUPT_STATE_KEY: {
            "agent": "different_agent",
            "thread_id": "session-1",
            "interrupts": [{"id": "interrupt-1"}],
        }
    }

    events = _collect_events(
        LangGraphAgentkitBridge(RegularGraph(), name="lg_regular"),
        _ctx("new input", session_id="session-1", state=state),
    )

    assert _visible(events) == [{"partial": False, "text": "fresh run"}]


def test_pending_interrupt_requires_command_support(monkeypatch):
    state = {
        LANGGRAPH_PENDING_INTERRUPT_STATE_KEY: {
            "agent": "lg_resume_no_command",
            "thread_id": "session-1",
            "interrupts": [{"id": "interrupt-1"}],
        }
    }
    monkeypatch.setattr(langgraph_module, "Command", None)

    with pytest.raises(UnsupportedFrameworkAgentError, match="HITL resume requires"):
        _collect_events(
            LangGraphAgentkitBridge(object(), name="lg_resume_no_command"),
            _ctx("approved", session_id="session-1", state=state),
        )


def test_pending_interrupt_without_final_text_clears_state():
    from langgraph.types import Command

    class EmptyResumeGraph:
        async def astream(self, payload, config=None, stream_mode=None, version=None):
            del config, stream_mode, version
            assert isinstance(payload, Command)
            yield {"type": "updates", "data": {}}

    state = {
        LANGGRAPH_PENDING_INTERRUPT_STATE_KEY: {
            "agent": "lg_empty_resume",
            "thread_id": "session-1",
            "interrupts": [{"id": "interrupt-1"}],
        }
    }

    events = _collect_events(
        LangGraphAgentkitBridge(EmptyResumeGraph(), name="lg_empty_resume"),
        _ctx("approved", session_id="session-1", state=state),
    )

    assert events == [
        {
            "partial": None,
            "text": "",
            "error_code": None,
            "interrupted": None,
            "metadata": None,
            "state_delta": {LANGGRAPH_PENDING_INTERRUPT_STATE_KEY: None},
        }
    ]


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

    assert _visible(second) == [{"partial": False, "text": "approved:yes:deploy"}]
    assert second[-1]["state_delta"] == {LANGGRAPH_PENDING_INTERRUPT_STATE_KEY: None}


def test_real_checkpointer_history_is_scoped_by_app_user_and_session():
    from typing import Annotated, TypedDict

    from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, START, StateGraph
    from langgraph.graph.message import add_messages

    class State(TypedDict):
        messages: Annotated[list[AnyMessage], add_messages]
        answer: str

    def write_answer(state: State):
        human_messages = [
            message.content
            for message in state["messages"]
            if isinstance(message, HumanMessage)
        ]
        answer = "|".join(human_messages)
        return {"messages": [AIMessage(content=answer)], "answer": answer}

    builder = StateGraph(State)
    builder.add_node("write_answer", write_answer)
    builder.add_edge(START, "write_answer")
    builder.add_edge("write_answer", END)
    graph = builder.compile(checkpointer=InMemorySaver())

    class SyncOnlyGraph:
        def stream(self, payload, **kwargs):
            yield from graph.stream(payload, **kwargs)

    bridge = LangGraphAgentkitBridge(SyncOnlyGraph(), name="lg_scoped_history")

    user_one = _collect_events(
        bridge,
        _ctx(
            "USER_ONE_MARKER",
            session_id="shared-session",
            app_name="app",
            user_id="user-one",
        ),
    )
    user_two = _collect_events(
        bridge,
        _ctx(
            "USER_TWO_MARKER",
            session_id="shared-session",
            app_name="app",
            user_id="user-two",
        ),
    )
    user_one_again = _collect_events(
        bridge,
        _ctx(
            "USER_ONE_SECOND",
            session_id="shared-session",
            app_name="app",
            user_id="user-one",
        ),
    )

    assert _visible(user_one)[-1] == {"partial": False, "text": "USER_ONE_MARKER"}
    assert _visible(user_two)[-1] == {"partial": False, "text": "USER_TWO_MARKER"}
    assert _visible(user_one_again)[-1] == {
        "partial": False,
        "text": "USER_ONE_MARKER|USER_ONE_SECOND",
    }


def test_real_command_updates_from_compiled_graph_are_supported():
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command

    class State(TypedDict):
        question: str
        answer: str

    def route(state: State):
        return Command(goto="write_answer", update={"answer": f"command:{state['question']}"})

    def write_answer(state: State):
        return {"answer": f"{state['answer']}:done"}

    builder = StateGraph(State)
    builder.add_node("route", route)
    builder.add_node("write_answer", write_answer)
    builder.add_edge(START, "route")
    builder.add_edge("write_answer", END)
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

    assert _visible(events) == [{"partial": False, "text": "command:hi:done"}]


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
            assert stream_mode == "updates"
            assert version == "v2"
            yield {"type": "updates", "data": {"answer": "sync graph"}}

    events = _collect_events(
        LangGraphAgentkitBridge(SyncStreamGraph(), name="lg_sync_stream"),
    )

    assert _visible(events) == [{"partial": False, "text": "sync graph"}]


def test_sync_stream_retries_legacy_signature_and_raises_after_emitting():
    class LegacySyncGraph:
        def __init__(self):
            self.calls = 0

        def stream(self, payload):
            self.calls += 1
            assert "messages" in payload
            yield SimpleNamespace(content="legacy sync")

    legacy_graph = LegacySyncGraph()
    events = _collect_events(
        LangGraphAgentkitBridge(legacy_graph, name="lg_legacy_sync"),
    )

    assert legacy_graph.calls == 1
    assert _visible(events) == [{"partial": False, "text": "legacy sync"}]

    class BrokenAfterEmitGraph:
        def stream(self, payload, stream_mode=None, version=None):
            del payload, stream_mode, version
            yield {"type": "updates", "data": {"answer": "before boom"}}
            raise TypeError("sync node failed")

    with pytest.raises(TypeError, match="sync node failed"):
        _collect_events(
            LangGraphAgentkitBridge(BrokenAfterEmitGraph(), name="lg_sync_broken"),
        )


def test_sync_stream_raises_last_signature_error_when_all_attempts_fail_before_emit():
    class IncompatibleSyncGraph:
        def __init__(self):
            self.calls = 0

        def stream(self, payload, **kwargs):
            del payload, kwargs
            self.calls += 1
            raise TypeError(f"signature mismatch {self.calls}")
            yield None

    graph = IncompatibleSyncGraph()

    with pytest.raises(TypeError, match="signature mismatch 3"):
        _collect_events(LangGraphAgentkitBridge(graph, name="lg_bad_sync_signature"))

    assert graph.calls == 3


def test_stream_mode_fallback_only_handles_call_signature_errors():
    class LegacyGraph:
        async def astream(self, payload):
            yield SimpleNamespace(content="legacy stream")

    events = _collect_events(
        LangGraphAgentkitBridge(LegacyGraph(), name="lg_legacy"),
    )

    assert _visible(events) == [{"partial": False, "text": "legacy stream"}]


def test_stream_runtime_type_errors_are_not_retried_as_legacy_signature():
    class BrokenGraph:
        async def astream(self, payload, stream_mode=None):
            del payload, stream_mode
            raise TypeError("node execution failed")
            yield None

    bridge = LangGraphAgentkitBridge(BrokenGraph(), name="lg_broken")

    with pytest.raises(TypeError, match="node execution failed"):
        _collect_events(bridge)


def test_async_stream_runtime_type_error_after_emit_is_not_retried():
    class BrokenAfterEmitGraph:
        async def astream(self, payload, stream_mode=None, version=None):
            del payload, stream_mode, version
            yield {"type": "updates", "data": {"answer": "before boom"}}
            raise TypeError("async node failed")

    bridge = LangGraphAgentkitBridge(BrokenAfterEmitGraph(), name="lg_async_broken")

    with pytest.raises(TypeError, match="async node failed"):
        _collect_events(bridge)


def test_unsupported_entry_raises_actionable_error():
    bridge = LangGraphAgentkitBridge(object(), name="lg_bad")

    with pytest.raises(UnsupportedFrameworkAgentError, match="compiled graph-like"):
        _collect_events(bridge)
