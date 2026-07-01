# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
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

"""Behavioral coverage for Runner._http_post_invoke -- the core invoke transport
that serves all agent traffic. Each test drives a REAL branch of the transport
by patching agentkit.toolkit.runners.base.requests.post with a hand-rolled
_FakeResponse and asserting on the (ok, payload_or_error) contract, the request
args the transport sent, and (for streaming) the assembled generator output.
"""

from typing import Any, Dict, List, Optional

import pytest


class _DummyRunner:
    """Concrete Runner with abstract methods stubbed, mirroring the seam used by
    tests/toolkit/runners/test_runner_backend_autodetect.py."""

    def __new__(cls):
        from agentkit.toolkit.runners.base import Runner

        class _Impl(Runner):
            def deploy(self, config):  # pragma: no cover - not exercised here
                raise NotImplementedError()

            def destroy(self, config):  # pragma: no cover
                raise NotImplementedError()

            def status(self, config):  # pragma: no cover
                raise NotImplementedError()

            def invoke(self, config, payload, headers=None, stream=None):  # pragma: no cover
                raise NotImplementedError()

        return _Impl()


class _FakeResponse:
    """Mimics the subset of requests.Response that _http_post_invoke touches:
    status_code, headers (Content-Type lookup), .text, .json(), .iter_lines()."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        content_type: str = "application/json",
        text: str = "",
        json_data: Any = None,
        json_error: Optional[Exception] = None,
        lines: Optional[List[str]] = None,
    ) -> None:
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.text = text
        self._json_data = json_data
        self._json_error = json_error
        self._lines = lines or []

    def json(self) -> Any:
        if self._json_error is not None:
            raise self._json_error
        return self._json_data

    def iter_lines(self, decode_unicode: bool = False):  # noqa: ARG002
        for line in self._lines:
            yield line

    def iter_content(self, *args, **kwargs):  # pragma: no cover - unused by transport
        return iter(())


@pytest.fixture
def runner():
    return _DummyRunner()


@pytest.fixture
def patched_post(monkeypatch):
    """Install a controllable fake requests.post at the base module. Returns a
    (set_response, calls) pair: set_response(resp_or_exc) installs the next
    response (or an exception the fake should raise), and calls records the
    kwargs each POST was made with."""
    import agentkit.toolkit.runners.base as base_mod

    state: Dict[str, Any] = {"response": None, "raise": None}
    calls: List[Dict[str, Any]] = []

    def _fake_post(url=None, json=None, headers=None, timeout=None, stream=None, **kwargs):
        calls.append(
            {
                "url": url,
                "json": json,
                "headers": headers,
                "timeout": timeout,
                "stream": stream,
            }
        )
        if state["raise"] is not None:
            raise state["raise"]
        return state["response"]

    monkeypatch.setattr(base_mod.requests, "post", _fake_post)

    def set_response(resp: _FakeResponse) -> None:
        state["response"] = resp
        state["raise"] = None

    def set_raise(exc: Exception) -> None:
        state["raise"] = exc
        state["response"] = None

    return set_response, set_raise, calls


def test_non_200_status_returns_error_string_echoing_status(runner, patched_post):
    set_response, _set_raise, calls = patched_post
    set_response(
        _FakeResponse(status_code=503, text="upstream down", content_type="text/plain")
    )

    ok, payload = runner._http_post_invoke(
        endpoint="https://svc/invoke",
        payload={"prompt": "hi"},
        headers={"authorization": "token"},
        stream=False,
    )

    assert ok is False
    assert isinstance(payload, str)
    assert "503" in payload
    assert "upstream down" in payload
    assert payload.startswith("Invocation failed: 503")
    # The transport must actually have POSTed the given endpoint/payload/headers.
    assert len(calls) == 1
    assert calls[0]["url"] == "https://svc/invoke"
    assert calls[0]["json"] == {"prompt": "hi"}
    assert calls[0]["headers"] == {"authorization": "token"}


def test_streaming_sse_detected_via_content_type_assembles_events(runner, patched_post):
    set_response, _set_raise, _calls = patched_post
    set_response(
        _FakeResponse(
            status_code=200,
            content_type="text/event-stream; charset=utf-8",
            lines=[
                'data: {"delta": "he"}',
                "",
                ": this is an SSE comment line",
                'data: {"delta": "llo"}',
                "data: not-json-should-be-skipped",
                "",
                'data: {"done": true}',
            ],
        )
    )

    ok, gen = runner._http_post_invoke(
        endpoint="https://svc/invoke",
        payload={"prompt": "hi"},
        headers={},
        stream=None,  # auto-detect -> Content-Type says event-stream -> stream
    )

    assert ok is True
    # Generator, not a materialized structure.
    assert not isinstance(gen, (dict, list, str, bytes))
    events = list(gen)
    # Comment line, blank lines, and the un-parseable data line are all dropped.
    assert events == [{"delta": "he"}, {"delta": "llo"}, {"done": True}]


def test_non_stream_json_body_is_parsed_and_returned(runner, patched_post):
    set_response, _set_raise, calls = patched_post
    body = {"result": {"text": "done"}, "usage": {"tokens": 7}}
    set_response(
        _FakeResponse(
            status_code=200,
            content_type="application/json",
            text='{"result": {"text": "done"}, "usage": {"tokens": 7}}',
            json_data=body,
        )
    )

    ok, payload = runner._http_post_invoke(
        endpoint="https://svc/invoke",
        payload={"prompt": "hi"},
        headers={},
        stream=None,  # auto-detect -> non-event-stream content-type -> non-stream JSON
    )

    assert ok is True
    assert payload == body
    # Auto-detect starts in stream mode, so the transport requests a streamed POST
    # and bumps the timeout to at least 300s (line ~166 of base.py).
    assert calls[0]["stream"] is True
    assert calls[0]["timeout"] == 300


def test_sse_masquerade_body_starting_with_data_is_parsed_as_sse(runner, patched_post):
    set_response, _set_raise, _calls = patched_post
    # 200 + non-event-stream Content-Type, but the body is actually SSE. The
    # transport's ~L210 double-check must switch to the fallback SSE parser.
    sse_text = (
        'data: {"delta": "a"}\n'
        "\n"
        'data: {"delta": "b"}\n'
        "data: broken-json\n"
        'data: {"end": 1}\n'
    )
    set_response(
        _FakeResponse(
            status_code=200,
            content_type="application/json",
            text=sse_text,
        )
    )

    ok, gen = runner._http_post_invoke(
        endpoint="https://svc/invoke",
        payload={"prompt": "hi"},
        headers={},
        stream=None,
    )

    assert ok is True
    assert not isinstance(gen, (dict, list, str, bytes))
    events = list(gen)
    # Un-parseable "broken-json" line skipped; blank line skipped.
    assert events == [{"delta": "a"}, {"delta": "b"}, {"end": 1}]


def test_json_parse_error_on_200_body_returns_error_not_exception(runner, patched_post):
    set_response, _set_raise, _calls = patched_post
    # 200, content-type not event-stream, body does NOT start with "data: ",
    # but .json() raises ValueError -> mapped to (False, "Response parsing failed: ...").
    set_response(
        _FakeResponse(
            status_code=200,
            content_type="application/json",
            text="<html>not json</html>",
            json_error=ValueError("Expecting value: line 1 column 1 (char 0)"),
        )
    )

    ok, payload = runner._http_post_invoke(
        endpoint="https://svc/invoke",
        payload={"prompt": "hi"},
        headers={},
        stream=False,  # force non-stream so we hit the JSON parse path directly
    )

    assert ok is False
    assert isinstance(payload, str)
    assert payload.startswith("Response parsing failed:")
    assert "Expecting value" in payload


def test_requests_timeout_is_mapped_to_error_with_actual_timeout(runner, patched_post):
    import requests as real_requests

    _set_response, set_raise, _calls = patched_post
    set_raise(real_requests.exceptions.Timeout("timed out"))

    ok, payload = runner._http_post_invoke(
        endpoint="https://svc/invoke",
        payload={"prompt": "hi"},
        headers={},
        stream=False,
        timeout=42,
    )

    assert ok is False
    assert isinstance(payload, str)
    assert payload.startswith("Request timeout after")
    # stream=False path uses the passed timeout verbatim.
    assert "42 seconds" in payload


def test_requests_request_exception_is_mapped_to_error(runner, patched_post):
    import requests as real_requests

    _set_response, set_raise, _calls = patched_post
    set_raise(real_requests.exceptions.ConnectionError("connection refused"))

    ok, payload = runner._http_post_invoke(
        endpoint="https://svc/invoke",
        payload={"prompt": "hi"},
        headers={},
        stream=False,
    )

    assert ok is False
    assert isinstance(payload, str)
    assert payload.startswith("Request error:")
    assert "connection refused" in payload


def test_forced_stream_true_returns_generator_and_uses_min_300_timeout(runner, patched_post):
    set_response, _set_raise, calls = patched_post
    set_response(
        _FakeResponse(
            status_code=200,
            content_type="text/event-stream",
            lines=['data: {"chunk": 1}', 'data: {"chunk": 2}'],
        )
    )

    ok, gen = runner._http_post_invoke(
        endpoint="https://svc/invoke",
        payload={"prompt": "hi"},
        headers={},
        stream=True,  # explicit stream, not auto-detect
        timeout=10,
    )

    assert ok is True
    events = list(gen)
    assert events == [{"chunk": 1}, {"chunk": 2}]
    # stream=True forces actual_timeout = max(timeout, 300).
    assert calls[0]["stream"] is True
    assert calls[0]["timeout"] == 300
