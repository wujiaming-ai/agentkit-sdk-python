import asyncio

import httpx
from fastapi import FastAPI

from agentkit.frameworks.serving.langserve import attach_langserve_compat_routes


def _request(app: FastAPI, method: str, path: str, **kwargs) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def _sse_events(text: str) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    current_event = "message"
    for line in text.splitlines():
        if line.startswith("event: "):
            current_event = line[len("event: ") :]
        elif line.startswith("data: "):
            events.append((current_event, line[len("data: ") :]))
    return events


def test_invoke_accepts_langserve_input_shape_with_custom_input_key():
    class QuestionRunnable:
        async def ainvoke(self, payload):
            assert payload == {"question": "hi"}
            return {"answer": "ok"}

    app = FastAPI()
    attach_langserve_compat_routes(
        app,
        QuestionRunnable(),
        input_key="question",
    )

    response = _request(app, "POST", "/invoke", json={"input": "hi"})

    assert response.status_code == 200
    assert response.json()["output"] == {"answer": "ok"}
    assert response.json()["metadata"]["run_id"]


def test_invoke_accepts_direct_dict_payload_for_existing_callers():
    class DirectDictRunnable:
        def invoke(self, payload):
            assert payload == {"question": "hi"}
            return "direct-ok"

    app = FastAPI()
    attach_langserve_compat_routes(app, DirectDictRunnable(), input_key="question")

    response = _request(app, "POST", "/invoke", json={"question": "hi"})

    assert response.status_code == 200
    assert response.json()["output"] == "direct-ok"


def test_invoke_falls_back_to_default_input_dict_for_lcel_style_chains():
    class DictChain:
        def invoke(self, payload):
            return f"dict:{payload['input']}"

    app = FastAPI()
    attach_langserve_compat_routes(app, DictChain())

    response = _request(app, "POST", "/invoke", json={"input": "hi"})

    assert response.status_code == 200
    assert response.json()["output"] == "dict:hi"


def test_invoke_passes_langserve_config_and_kwargs_when_supported():
    class ConfigurableRunnable:
        async def ainvoke(self, payload, config=None, *, style="plain"):
            assert payload == "hi"
            assert config == {"tags": ["migration"], "metadata": {"tenant": "demo"}}
            assert style == "formal"
            return "configured"

    app = FastAPI()
    attach_langserve_compat_routes(app, ConfigurableRunnable())

    response = _request(
        app,
        "POST",
        "/invoke",
        json={
            "input": "hi",
            "config": {"tags": ["migration"], "metadata": {"tenant": "demo"}},
            "kwargs": {"style": "formal"},
        },
    )

    assert response.status_code == 200
    assert response.json()["output"] == "configured"


def test_invoke_ignores_config_for_simple_callables_that_do_not_accept_it():
    app = FastAPI()
    attach_langserve_compat_routes(app, lambda value: f"simple:{value}")

    response = _request(
        app,
        "POST",
        "/invoke",
        json={"input": "hi", "config": {"tags": ["ignored"]}, "kwargs": {"ignored": True}},
    )

    assert response.status_code == 200
    assert response.json()["output"] == "simple:hi"


def test_invoke_rejects_runnables_without_supported_protocol():
    app = FastAPI()
    attach_langserve_compat_routes(app, object())

    response = _request(app, "POST", "/invoke", json={"input": "hi"})

    assert response.status_code == 422
    assert "ainvoke/invoke" in response.json()["detail"]


def test_invalid_json_is_rejected_before_invocation():
    app = FastAPI()
    attach_langserve_compat_routes(app, lambda value: value)

    response = _request(
        app,
        "POST",
        "/invoke",
        content="{",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Request body must be valid JSON."


def test_batch_falls_back_to_per_item_invoke_when_batch_is_missing():
    class Runnable:
        async def ainvoke(self, payload):
            return f"answer:{payload['question']}"

    app = FastAPI()
    attach_langserve_compat_routes(app, Runnable(), input_key="question")

    response = _request(app, "POST", "/batch", json={"inputs": ["a", {"question": "b"}]})

    assert response.status_code == 200
    assert response.json()["output"] == ["answer:a", "answer:b"]
    assert len(response.json()["metadata"]["run_ids"]) == 2


def test_batch_passes_config_and_kwargs_to_native_batch():
    class BatchRunnable:
        def batch(self, inputs, config=None, *, suffix=""):
            assert config == {"tags": ["batch"]}
            assert suffix == "!"
            return [f"{value}{suffix}" for value in inputs]

    app = FastAPI()
    attach_langserve_compat_routes(app, BatchRunnable())

    response = _request(
        app,
        "POST",
        "/batch",
        json={"inputs": ["a", "b"], "config": {"tags": ["batch"]}, "kwargs": {"suffix": "!"}},
    )

    assert response.status_code == 200
    assert response.json()["output"] == ["a!", "b!"]


def test_batch_rejects_missing_inputs_list():
    app = FastAPI()
    attach_langserve_compat_routes(app, lambda value: value)

    response = _request(app, "POST", "/batch", json={"input": "hi"})

    assert response.status_code == 400
    assert "inputs" in response.json()["detail"]


def test_stream_emits_data_and_end_events():
    class StreamingRunnable:
        async def astream(self, payload):
            assert payload == "hi"
            yield "h"
            yield "hi"

    app = FastAPI()
    attach_langserve_compat_routes(app, StreamingRunnable())

    response = _request(app, "POST", "/stream", json={"input": "hi"})

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[0][0] == "data"
    assert '"h"' in events[0][1]
    assert events[-1] == ("end", "{}")


def test_stream_passes_config_and_kwargs_to_runnable():
    class StreamingRunnable:
        async def astream(self, payload, config=None, *, suffix=""):
            assert payload == "hi"
            assert config == {"tags": ["stream"]}
            assert suffix == "!"
            yield f"{payload}{suffix}"

    app = FastAPI()
    attach_langserve_compat_routes(app, StreamingRunnable())

    response = _request(
        app,
        "POST",
        "/stream",
        json={"input": "hi", "config": {"tags": ["stream"]}, "kwargs": {"suffix": "!"}},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert '"hi!"' in events[0][1]


def test_stream_events_uses_runnable_astream_events_when_available():
    class EventRunnable:
        async def astream_events(self, payload):
            assert payload == "hi"
            yield {"event": "on_chain_start"}

    app = FastAPI()
    attach_langserve_compat_routes(app, EventRunnable())

    response = _request(app, "POST", "/stream_events", json={"input": "hi"})

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert events[0][0] == "data"
    assert "on_chain_start" in events[0][1]
    assert events[-1] == ("end", "{}")


def test_stream_events_passes_config_and_kwargs_to_runnable():
    class EventRunnable:
        async def astream_events(self, payload, config=None, *, suffix=""):
            assert payload == "hi"
            assert config == {"tags": ["events"]}
            assert suffix == "!"
            yield {"event": "on_chain_stream", "data": {"chunk": payload + suffix}}

    app = FastAPI()
    attach_langserve_compat_routes(app, EventRunnable())

    response = _request(
        app,
        "POST",
        "/stream_events",
        json={"input": "hi", "config": {"tags": ["events"]}, "kwargs": {"suffix": "!"}},
    )

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert "hi!" in events[0][1]


def test_stream_events_uses_custom_input_key_candidates():
    class EventRunnable:
        async def astream_events(self, payload):
            if not isinstance(payload, dict):
                raise TypeError("Expected dict")
            yield {"event": "on_chain_stream", "data": {"question": payload["question"]}}

    app = FastAPI()
    attach_langserve_compat_routes(app, EventRunnable(), input_key="question")

    response = _request(app, "POST", "/stream_events", json={"input": "hi"})

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert "hi" in events[0][1]
    assert events[-1] == ("end", "{}")


def test_stream_events_passes_v2_when_langchain_version_requires_it():
    class EventRunnable:
        async def astream_events(self, payload, *, version):
            assert payload == "hi"
            assert version == "v2"
            yield {"event": "on_chain_start", "version": version}

    app = FastAPI()
    attach_langserve_compat_routes(app, EventRunnable())

    response = _request(app, "POST", "/stream_events", json={"input": "hi"})

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert "v2" in events[0][1]


def test_stream_events_falls_back_to_stream_chunks():
    class StreamingRunnable:
        async def astream(self, payload):
            del payload
            yield {"answer": "hello"}

    app = FastAPI()
    attach_langserve_compat_routes(app, StreamingRunnable())

    response = _request(app, "POST", "/stream_events", json={"input": "hi"})

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert "on_chain_stream" in events[0][1]
    assert "hello" in events[0][1]


def test_stream_log_returns_501_when_runnable_cannot_provide_logs():
    app = FastAPI()
    attach_langserve_compat_routes(app, lambda value: value)

    response = _request(app, "POST", "/stream_log", json={"input": "hi"})

    assert response.status_code == 501
    assert "does not synthesize LangServe logs" in response.json()["detail"]


def test_stream_log_uses_custom_input_key_candidates_when_available():
    class LogRunnable:
        async def astream_log(self, payload):
            if not isinstance(payload, dict):
                raise TypeError("Expected dict")
            yield {"op": "add", "path": "/streamed_output/-", "value": payload["question"]}

    app = FastAPI()
    attach_langserve_compat_routes(app, LogRunnable(), input_key="question")

    response = _request(app, "POST", "/stream_log", json={"input": "hi"})

    assert response.status_code == 200
    events = _sse_events(response.text)
    assert "hi" in events[0][1]
    assert events[-1] == ("end", "{}")


def test_langserve_routes_are_promoted_ahead_of_existing_invoke_route():
    app = FastAPI()

    @app.post("/invoke")
    async def existing_invoke():
        return {"old": True}

    attach_langserve_compat_routes(app, lambda value: f"new:{value}")

    response = _request(app, "POST", "/invoke", json={"input": "hi"})

    assert response.status_code == 200
    assert response.json()["output"] == "new:hi"


def test_prefixed_routes_do_not_claim_root_invoke():
    app = FastAPI()

    @app.post("/invoke")
    async def existing_invoke():
        return {"old": True}

    attach_langserve_compat_routes(app, lambda value: f"new:{value}", prefix="/compat")
    assert _request(app, "POST", "/invoke", json={"input": "hi"}).json() == {"old": True}
    assert _request(app, "POST", "/compat/invoke", json={"input": "hi"}).json()["output"] == "new:hi"


def test_langserve_routes_are_promoted_ahead_of_root_mount():
    app = FastAPI()
    root = FastAPI()
    app.mount("/", root)

    attach_langserve_compat_routes(app, lambda value: f"new:{value}", prefix="/compat")

    response = _request(app, "POST", "/compat/invoke", json={"input": "hi"})

    assert response.status_code == 200
    assert response.json()["output"] == "new:hi"
