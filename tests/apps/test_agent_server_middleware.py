from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentkit.apps.agent_server_app import middleware as middleware_mod
from agentkit.apps.agent_server_app.middleware import (
    AgentkitTelemetryHTTPMiddleware,
    _headers,
    _server_address,
)


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def _send_recorder():
    messages = []

    async def send(message):
        messages.append(message)

    return send, messages


@pytest.fixture
def fake_telemetry(monkeypatch):
    fake = MagicMock()
    state = SimpleNamespace(status_code=None)
    fake.start_server_request.return_value = state
    monkeypatch.setattr(middleware_mod, "telemetry", fake)
    return fake, state


def test_header_and_server_helpers_handle_missing_and_latin1_values():
    assert _headers({}) == {}
    assert _headers({"headers": [(b"X-Name", "caf\xe9".encode("latin-1"))]}) == {
        "x-name": "caf\xe9"
    }
    assert _server_address({}) is None
    assert _server_address({"server": None}) is None
    assert _server_address({"server": []}) is None
    assert _server_address({"server": ("127.0.0.1", 8000)}) == "127.0.0.1"


def test_non_http_scope_is_passed_through_without_telemetry(fake_telemetry):
    fake, _state = fake_telemetry
    seen = {}

    async def app(scope, receive, send):
        seen.update(scope=scope, receive=receive, send=send)
        return "ok"

    scope = {"type": "lifespan"}
    send, _messages = _send_recorder()
    result = asyncio.run(
        AgentkitTelemetryHTTPMiddleware(app)(scope, _receive, send)
    )

    assert result == "ok"
    assert seen == {"scope": scope, "receive": _receive, "send": send}
    fake.assert_not_called()


def test_http_request_passes_all_headers_to_global_propagation_layer(fake_telemetry):
    fake, _state = fake_telemetry

    async def app(scope, receive, send):
        del scope, receive
        await send({"type": "http.response.body", "body": b"ok"})

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/run_sse",
        "server": ("agent.example", 443),
        "headers": [
            (b"traceparent", b"00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"),
            (b"authorization", b"Bearer secret"),
        ],
    }
    send, _messages = _send_recorder()
    asyncio.run(AgentkitTelemetryHTTPMiddleware(app)(scope, _receive, send))

    fake.start_server_request.assert_called_once_with(
        method="POST",
        path="/run_sse",
        headers={
            "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
            "authorization": "Bearer secret",
        },
        server_address="agent.example",
    )


def test_response_status_and_final_body_finish_exactly_once(fake_telemetry):
    fake, state = fake_telemetry
    finish_counts = []

    async def app(scope, receive, send):
        del scope, receive
        await send({"type": "http.response.start", "status": 202, "headers": []})
        finish_counts.append(fake.finish_server_request.call_count)
        await send({"type": "http.response.body", "body": b"a", "more_body": True})
        finish_counts.append(fake.finish_server_request.call_count)
        await send({"type": "http.response.body", "body": b"b"})

    scope = {"type": "http", "method": "GET", "path": "/stream"}
    send, messages = _send_recorder()
    asyncio.run(AgentkitTelemetryHTTPMiddleware(app)(scope, _receive, send))

    assert state.status_code == 202
    assert finish_counts == [0, 0]
    assert [message["type"] for message in messages] == [
        "http.response.start",
        "http.response.body",
        "http.response.body",
    ]
    fake.finish_server_request.assert_called_once_with(state, exception=None)


def test_middleware_finishes_when_app_returns_without_body(fake_telemetry):
    fake, state = fake_telemetry

    async def app(scope, receive, send):
        del scope, receive, send
        return "done"

    send, _messages = _send_recorder()
    result = asyncio.run(
        AgentkitTelemetryHTTPMiddleware(app)(
            {"type": "http", "method": "GET", "path": "/health"},
            _receive,
            send,
        )
    )

    assert result == "done"
    fake.finish_server_request.assert_called_once_with(state, exception=None)


@pytest.mark.parametrize("exception", [RuntimeError("boom"), asyncio.CancelledError()])
def test_exception_and_cancellation_are_recorded_and_reraised(
    fake_telemetry,
    exception,
):
    fake, state = fake_telemetry

    async def app(scope, receive, send):
        del scope, receive, send
        raise exception

    send, _messages = _send_recorder()
    with pytest.raises(type(exception)) as raised:
        asyncio.run(
            AgentkitTelemetryHTTPMiddleware(app)(
                {"type": "http", "method": "POST", "path": "/run"},
                _receive,
                send,
            )
        )

    assert raised.value is exception
    fake.finish_server_request.assert_any_call(state, exception=exception)


def test_send_failure_is_recorded_as_request_failure(fake_telemetry):
    fake, state = fake_telemetry
    failure = ConnectionError("client disconnected")

    async def app(scope, receive, send):
        del scope, receive
        await send({"type": "http.response.body", "body": b"ok"})

    async def send(_message):
        raise failure

    with pytest.raises(ConnectionError, match="client disconnected"):
        asyncio.run(
            AgentkitTelemetryHTTPMiddleware(app)(
                {"type": "http", "method": "POST", "path": "/run_sse"},
                _receive,
                send,
            )
        )

    fake.finish_server_request.assert_any_call(state, exception=failure)
