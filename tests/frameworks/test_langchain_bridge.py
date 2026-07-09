import asyncio
from types import SimpleNamespace

import pytest
from google.genai import types

from agentkit.frameworks._common import UnsupportedFrameworkAgentError
from agentkit.frameworks.langchain import LangChainAgentkitBridge


def _ctx(text: str = "hi"):
    return SimpleNamespace(
        invocation_id="invocation",
        branch=None,
        user_content=types.UserContent(parts=[types.Part(text=text)]),
    )


def _event_text(event) -> str:
    return "".join(part.text or "" for part in event.content.parts)


def _collect_events(bridge: LangChainAgentkitBridge):
    async def run():
        events = []
        async for event in bridge._run_async_impl(_ctx()):
            events.append({"partial": event.partial, "text": _event_text(event)})
        return events

    return asyncio.run(run())


def test_streaming_cumulative_chunks_are_emitted_as_deltas():
    class CumulativeRunnable:
        async def astream(self, payload):
            assert payload == {"input": "hi"}
            yield "a"
            yield "ab"
            yield "abc"

    events = _collect_events(
        LangChainAgentkitBridge(CumulativeRunnable(), name="lc_smoke"),
    )

    assert events == [
        {"partial": True, "text": "a"},
        {"partial": True, "text": "b"},
        {"partial": True, "text": "c"},
        {"partial": False, "text": "abc"},
    ]


def test_streaming_falls_back_to_text_input_for_text_only_runnables():
    class TextOnlyRunnable:
        async def astream(self, payload):
            if not isinstance(payload, str):
                raise ValueError("Invalid input type")
            yield f"echo:{payload}"

    events = _collect_events(
        LangChainAgentkitBridge(TextOnlyRunnable(), name="lc_text_only"),
    )

    assert events == [
        {"partial": True, "text": "echo:hi"},
        {"partial": False, "text": "echo:hi"},
    ]


def test_custom_input_key_is_used_for_dict_payloads():
    class QuestionRunnable:
        async def astream(self, payload):
            assert payload == {"question": "hi"}
            yield {"answer": "answer:hi"}

    events = _collect_events(
        LangChainAgentkitBridge(
            QuestionRunnable(),
            name="lc_question",
            input_key="question",
        ),
    )

    assert events == [
        {"partial": True, "text": "answer:hi"},
        {"partial": False, "text": "answer:hi"},
    ]


def test_lcel_prompt_model_parser_chain_is_supported():
    from langchain_core.runnables import RunnableLambda

    class InvokeOnlyChain:
        def __init__(self):
            self.chain = (
                RunnableLambda(lambda payload: payload["input"])
                | RunnableLambda(lambda text: f"lcel answer:{text}")
            )

        def invoke(self, payload):
            return self.chain.invoke(payload)

    events = _collect_events(
        LangChainAgentkitBridge(InvokeOnlyChain(), name="lc_lcel"),
    )

    assert events[-1] == {"partial": False, "text": "lcel answer:hi"}


def test_message_chunks_are_streamed_as_text_deltas():
    from langchain_core.messages import AIMessageChunk

    class ChunkRunnable:
        async def astream(self, payload):
            assert payload == {"input": "hi"}
            yield AIMessageChunk(content="he")
            yield AIMessageChunk(content="hello")

    events = _collect_events(
        LangChainAgentkitBridge(ChunkRunnable(), name="lc_message_chunks"),
    )

    assert events == [
        {"partial": True, "text": "he"},
        {"partial": True, "text": "llo"},
        {"partial": False, "text": "hello"},
    ]


def test_agent_executor_style_output_dict_uses_output_key():
    class AgentExecutorLikeRunnable:
        async def ainvoke(self, payload):
            assert payload == {"input": "hi"}
            return {
                "input": payload["input"],
                "intermediate_steps": [("tool", "observation")],
                "output": "agent executor answer",
            }

    events = _collect_events(
        LangChainAgentkitBridge(AgentExecutorLikeRunnable(), name="lc_agent_executor"),
    )

    assert events == [{"partial": False, "text": "agent executor answer"}]


def test_ainvoke_is_used_when_streaming_is_not_available():
    class AsyncRunnable:
        async def ainvoke(self, payload):
            assert payload == {"input": "hi"}
            return {"answer": "async answer"}

    events = _collect_events(
        LangChainAgentkitBridge(AsyncRunnable(), name="lc_async"),
    )

    assert events == [{"partial": False, "text": "async answer"}]


def test_sync_stream_is_used_when_async_streaming_is_not_available():
    class SyncStreamRunnable:
        def stream(self, payload):
            assert payload == {"input": "hi"}
            yield "sync"
            yield "sync stream"

    events = _collect_events(
        LangChainAgentkitBridge(SyncStreamRunnable(), name="lc_sync_stream"),
    )

    assert events == [
        {"partial": True, "text": "sync"},
        {"partial": True, "text": " stream"},
        {"partial": False, "text": "sync stream"},
    ]


def test_message_input_candidate_is_used_for_message_only_runnables():
    class MessageOnlyRunnable:
        async def ainvoke(self, payload):
            if not isinstance(payload, list):
                raise TypeError("Invalid input type")
            return payload[0].content

    events = _collect_events(
        LangChainAgentkitBridge(MessageOnlyRunnable(), name="lc_messages"),
    )

    assert events == [{"partial": False, "text": "hi"}]


def test_sync_invoke_is_used_when_streaming_is_not_available():
    class SyncRunnable:
        def invoke(self, payload):
            assert payload == {"input": "hi"}
            return SimpleNamespace(text="sync answer")

    events = _collect_events(
        LangChainAgentkitBridge(SyncRunnable(), name="lc_sync"),
    )

    assert events == [{"partial": False, "text": "sync answer"}]


def test_content_blocks_are_converted_to_text():
    class BlockRunnable:
        async def astream(self, payload):
            del payload
            yield SimpleNamespace(content=[{"text": "he"}, {"text": "llo"}])

    events = _collect_events(
        LangChainAgentkitBridge(BlockRunnable(), name="lc_blocks"),
    )

    assert events == [
        {"partial": True, "text": "hello"},
        {"partial": False, "text": "hello"},
    ]


def test_callable_falls_back_to_text_input_for_text_only_functions():
    def text_only(payload):
        if not isinstance(payload, str):
            raise TypeError("Invalid input type")
        return f"call:{payload}"

    events = _collect_events(
        LangChainAgentkitBridge(text_only, name="lc_callable"),
    )

    assert events == [{"partial": False, "text": "call:hi"}]


def test_non_input_shape_type_errors_are_not_retried():
    class BrokenRunnable:
        async def astream(self, payload):
            del payload
            raise TypeError("tool execution failed")
            yield ""

    bridge = LangChainAgentkitBridge(BrokenRunnable(), name="lc_broken")

    with pytest.raises(TypeError, match="tool execution failed"):
        _collect_events(bridge)


def test_stream_errors_after_output_are_not_retried():
    class BrokenAfterOutputRunnable:
        async def astream(self, payload):
            del payload
            yield "partial"
            raise TypeError("Invalid input type")

    bridge = LangChainAgentkitBridge(BrokenAfterOutputRunnable(), name="lc_broken")

    with pytest.raises(TypeError, match="Invalid input type"):
        _collect_events(bridge)


def test_unsupported_entry_raises_actionable_error():
    bridge = LangChainAgentkitBridge(object(), name="lc_bad")

    with pytest.raises(UnsupportedFrameworkAgentError, match="astream, stream, ainvoke, or invoke"):
        _collect_events(bridge)
