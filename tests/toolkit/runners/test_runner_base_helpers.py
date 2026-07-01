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

"""Offline unit coverage for the pure helper methods on ``Runner``.

These pin the request/response shape helpers on
``agentkit.toolkit.runners.base.Runner`` -- backend classification
(``_is_adk_list_apps_response`` / ``_is_a2a_agent_card_response``), fallback
signature detection (``_should_fallback_to_adk``), the camelCase ADK and
JSON-RPC A2A payload builders, endpoint normalization, and the TTL-based
backend cache. ``_invoke_with_adk_compat`` is exercised elsewhere
(test_runner_backend_autodetect.py) and is intentionally not re-covered here.
"""

from __future__ import annotations

from typing import Any


class _DummyRunner:
    """Concrete stand-in for the abstract ``Runner`` base class.

    Mirrors the pattern in test_runner_backend_autodetect.py: build an
    ``_Impl`` subclass that stubs the abstract methods so ``Runner`` can be
    instantiated to reach its concrete helper methods.
    """

    def __new__(cls):
        from agentkit.toolkit.runners.base import Runner

        class _Impl(Runner):
            def deploy(self, config):
                raise NotImplementedError()

            def destroy(self, config):
                raise NotImplementedError()

            def status(self, config):
                raise NotImplementedError()

            def invoke(self, config, payload, headers=None, stream=None):
                raise NotImplementedError()

        return _Impl()


# ===== _is_a2a =====


class _FakeCommonConfig:
    def __init__(self, agent_type: Any = None, template_type: Any = None) -> None:
        self.agent_type = agent_type
        self.template_type = template_type


def test_is_a2a_true_when_agent_type_contains_a2a_case_insensitive() -> None:
    runner = _DummyRunner()
    assert runner._is_a2a(_FakeCommonConfig(agent_type="A2A")) is True
    assert runner._is_a2a(_FakeCommonConfig(agent_type="my-a2a-agent")) is True


def test_is_a2a_true_when_only_template_type_contains_a2a() -> None:
    runner = _DummyRunner()
    cfg = _FakeCommonConfig(agent_type=None, template_type="a2a-template")
    assert runner._is_a2a(cfg) is True


def test_is_a2a_false_when_neither_field_mentions_a2a() -> None:
    runner = _DummyRunner()
    cfg = _FakeCommonConfig(agent_type="adk", template_type="langgraph")
    assert runner._is_a2a(cfg) is False


def test_is_a2a_false_when_common_config_is_none() -> None:
    runner = _DummyRunner()
    assert runner._is_a2a(None) is False


def test_is_a2a_none_safe_when_both_fields_are_none() -> None:
    runner = _DummyRunner()
    assert runner._is_a2a(_FakeCommonConfig(agent_type=None, template_type=None)) is False


# ===== _is_adk_list_apps_response =====


def test_is_adk_list_apps_response_true_for_list() -> None:
    runner = _DummyRunner()
    assert runner._is_adk_list_apps_response([]) is True
    assert runner._is_adk_list_apps_response(["app_a", "app_b"]) is True


def test_is_adk_list_apps_response_true_for_dict_with_apps_key() -> None:
    runner = _DummyRunner()
    assert runner._is_adk_list_apps_response({"apps": ["x"]}) is True
    # Presence of the key is sufficient regardless of the value.
    assert runner._is_adk_list_apps_response({"apps": None}) is True


def test_is_adk_list_apps_response_false_for_dict_without_apps_key() -> None:
    runner = _DummyRunner()
    assert runner._is_adk_list_apps_response({"name": "x"}) is False


def test_is_adk_list_apps_response_false_for_scalar_types() -> None:
    runner = _DummyRunner()
    assert runner._is_adk_list_apps_response(None) is False
    assert runner._is_adk_list_apps_response("apps") is False
    assert runner._is_adk_list_apps_response(42) is False


# ===== _is_a2a_agent_card_response =====


def test_is_a2a_agent_card_response_true_with_name_and_capabilities_dict() -> None:
    runner = _DummyRunner()
    assert runner._is_a2a_agent_card_response({"name": "a", "capabilities": {}}) is True


def test_is_a2a_agent_card_response_true_with_name_and_skills_list() -> None:
    runner = _DummyRunner()
    assert runner._is_a2a_agent_card_response({"name": "a", "skills": []}) is True


def test_is_a2a_agent_card_response_true_with_name_and_endpoints() -> None:
    runner = _DummyRunner()
    assert runner._is_a2a_agent_card_response({"name": "a", "endpoints": {}}) is True
    assert runner._is_a2a_agent_card_response({"name": "a", "endpoints": []}) is True


def test_is_a2a_agent_card_response_true_with_name_and_protocol_version() -> None:
    runner = _DummyRunner()
    assert (
        runner._is_a2a_agent_card_response({"name": "a", "protocol_version": "1.0"})
        is True
    )
    assert (
        runner._is_a2a_agent_card_response({"name": "a", "protocolVersion": "1.0"})
        is True
    )


def test_is_a2a_agent_card_response_false_when_name_present_but_no_signal_field() -> None:
    runner = _DummyRunner()
    assert runner._is_a2a_agent_card_response({"name": "a"}) is False


def test_is_a2a_agent_card_response_false_when_name_missing_or_empty() -> None:
    runner = _DummyRunner()
    # Signal fields present but no valid name -> False.
    assert runner._is_a2a_agent_card_response({"capabilities": {}}) is False
    assert runner._is_a2a_agent_card_response({"name": "", "capabilities": {}}) is False
    assert runner._is_a2a_agent_card_response({"name": 123, "capabilities": {}}) is False


def test_is_a2a_agent_card_response_false_for_non_dict() -> None:
    runner = _DummyRunner()
    assert runner._is_a2a_agent_card_response(None) is False
    assert runner._is_a2a_agent_card_response(["name"]) is False


def test_is_a2a_agent_card_response_false_when_signal_fields_wrong_types() -> None:
    runner = _DummyRunner()
    # capabilities must be dict, skills must be list, protocol_version must be str.
    data = {
        "name": "a",
        "capabilities": ["not-a-dict"],
        "skills": {"not": "a-list"},
        "protocol_version": 1.0,
    }
    assert runner._is_a2a_agent_card_response(data) is False


# ===== _should_fallback_to_adk =====


def test_should_fallback_to_adk_true_for_404_signatures() -> None:
    runner = _DummyRunner()
    assert runner._should_fallback_to_adk("Invocation failed: 404") is True
    assert runner._should_fallback_to_adk("HTTP 404 Not Found") is True


def test_should_fallback_to_adk_true_for_405_signatures() -> None:
    runner = _DummyRunner()
    assert runner._should_fallback_to_adk("Invocation failed: 405") is True
    assert runner._should_fallback_to_adk("got 405 method not allowed") is True


def test_should_fallback_to_adk_false_for_other_errors_and_empty() -> None:
    runner = _DummyRunner()
    assert runner._should_fallback_to_adk("Invocation failed: 500") is False
    assert runner._should_fallback_to_adk("connection refused") is False
    assert runner._should_fallback_to_adk("") is False
    # None is tolerated (coerced to "") and yields False.
    assert runner._should_fallback_to_adk(None) is False


def test_should_fallback_to_adk_false_when_404_lacks_surrounding_spaces() -> None:
    runner = _DummyRunner()
    # The bare-code check requires " 404 " with spaces on both sides and the
    # prefix check requires the exact "Invocation failed: 404" start, so a
    # substring like "err404code" matches neither.
    assert runner._should_fallback_to_adk("err404code") is False


# ===== _build_adk_run_sse_payload =====


def test_build_adk_run_sse_payload_uses_camelcase_and_prompt_text() -> None:
    runner = _DummyRunner()
    req = runner._build_adk_run_sse_payload(
        app_name="my_app",
        headers={"user_id": "u1", "session_id": "s1"},
        original_payload={"prompt": "hello"},
    )
    assert req == {
        "appName": "my_app",
        "userId": "u1",
        "sessionId": "s1",
        "newMessage": {"role": "user", "parts": [{"text": "hello"}]},
        "streaming": True,
    }


def test_build_adk_run_sse_payload_falls_back_to_x_prefixed_headers() -> None:
    runner = _DummyRunner()
    req = runner._build_adk_run_sse_payload(
        app_name="app",
        headers={"x-user-id": "xu", "x-session-id": "xs"},
        original_payload={"prompt": "hi"},
    )
    assert req["userId"] == "xu"
    assert req["sessionId"] == "xs"


def test_build_adk_run_sse_payload_uses_defaults_when_headers_missing() -> None:
    runner = _DummyRunner()
    req = runner._build_adk_run_sse_payload(
        app_name="app", headers={}, original_payload={"prompt": "hi"}
    )
    assert req["userId"] == "agentkit_user"
    assert req["sessionId"] == "agentkit_sample_session"


def test_build_adk_run_sse_payload_json_dumps_fallback_when_no_prompt() -> None:
    runner = _DummyRunner()
    req = runner._build_adk_run_sse_payload(
        app_name="app", headers={}, original_payload={"foo": "bar"}
    )
    # No string "prompt" key -> the whole payload is JSON-serialized as the text.
    assert req["newMessage"]["parts"][0]["text"] == '{"foo": "bar"}'


def test_build_adk_run_sse_payload_json_dumps_fallback_when_prompt_not_a_string() -> None:
    runner = _DummyRunner()
    req = runner._build_adk_run_sse_payload(
        app_name="app", headers={}, original_payload={"prompt": 123}
    )
    # Non-string prompt is ignored; falls back to serializing the payload.
    assert req["newMessage"]["parts"][0]["text"] == '{"prompt": 123}'


def test_build_adk_run_sse_payload_maps_state_delta_to_camelcase() -> None:
    runner = _DummyRunner()
    req = runner._build_adk_run_sse_payload(
        app_name="app",
        headers={},
        original_payload={"prompt": "hi", "state_delta": {"k": "v"}},
    )
    assert req["stateDelta"] == {"k": "v"}


def test_build_adk_run_sse_payload_omits_state_delta_when_absent() -> None:
    runner = _DummyRunner()
    req = runner._build_adk_run_sse_payload(
        app_name="app", headers={}, original_payload={"prompt": "hi"}
    )
    assert "stateDelta" not in req


# ===== _build_a2a_jsonrpc_payload =====


def test_build_a2a_jsonrpc_payload_passthrough_when_already_jsonrpc() -> None:
    runner = _DummyRunner()
    original = {"jsonrpc": "2.0", "method": "custom", "params": {}, "id": 7}
    result = runner._build_a2a_jsonrpc_payload(original, headers={"h": "v"})
    # Returned object is the same dict, untouched.
    assert result is original


def test_build_a2a_jsonrpc_payload_builds_message_stream_envelope(monkeypatch) -> None:
    import agentkit.toolkit.runners.base as base_mod

    runner = _DummyRunner()

    class _FixedUUID:
        def __str__(self) -> str:
            return "fixed-message-id"

    monkeypatch.setattr(base_mod.uuid, "uuid4", lambda: _FixedUUID())
    monkeypatch.setattr(base_mod.random, "randint", lambda a, b: 4242)

    headers = {"user_id": "u", "session_id": "s"}
    result = runner._build_a2a_jsonrpc_payload({"prompt": "hey"}, headers=headers)

    assert result == {
        "jsonrpc": "2.0",
        "method": "message/stream",
        "params": {
            "message": {
                "role": "user",
                "messageId": "fixed-message-id",
                "parts": [{"kind": "text", "text": "hey"}],
            },
            "metadata": headers,
        },
        "id": 4242,
    }


def test_build_a2a_jsonrpc_payload_json_dumps_fallback_when_no_prompt(monkeypatch) -> None:
    import agentkit.toolkit.runners.base as base_mod

    runner = _DummyRunner()

    class _FixedUUID:
        def __str__(self) -> str:
            return "mid"

    monkeypatch.setattr(base_mod.uuid, "uuid4", lambda: _FixedUUID())
    monkeypatch.setattr(base_mod.random, "randint", lambda a, b: 1)

    result = runner._build_a2a_jsonrpc_payload({"foo": "bar"}, headers={})
    text = result["params"]["message"]["parts"][0]["text"]
    assert text == '{"foo": "bar"}'


# ===== _normalize_base_endpoint =====


def test_normalize_base_endpoint_strips_trailing_slashes() -> None:
    runner = _DummyRunner()
    assert runner._normalize_base_endpoint("https://x/") == "https://x"
    assert runner._normalize_base_endpoint("https://x///") == "https://x"


def test_normalize_base_endpoint_leaves_clean_url_untouched() -> None:
    runner = _DummyRunner()
    assert runner._normalize_base_endpoint("https://x/path") == "https://x/path"


def test_normalize_base_endpoint_handles_empty_string() -> None:
    runner = _DummyRunner()
    assert runner._normalize_base_endpoint("") == ""


# ===== _get_cached_backend / _set_cached_backend =====


def test_cached_backend_roundtrip_and_key_normalization() -> None:
    runner = _DummyRunner()
    runner._backend_detect_cache_ttl_s = 9999
    # Trailing slashes normalize to the same key.
    runner._set_cached_backend("https://x/", "adk")
    assert runner._get_cached_backend("https://x") == "adk"
    assert runner._get_cached_backend("https://x///") == "adk"


def test_get_cached_backend_returns_none_when_unset() -> None:
    runner = _DummyRunner()
    assert runner._get_cached_backend("https://never-set") is None


def test_get_cached_backend_evicts_expired_entry(monkeypatch) -> None:
    import agentkit.toolkit.runners.base as base_mod

    runner = _DummyRunner()
    runner._backend_detect_cache_ttl_s = 100

    clock = {"t": 1000.0}
    monkeypatch.setattr(base_mod.time, "monotonic", lambda: clock["t"])

    runner._set_cached_backend("https://x", "a2a")
    # Still within TTL.
    clock["t"] = 1000.0 + 100
    assert runner._get_cached_backend("https://x") == "a2a"

    # Advance strictly past the TTL -> entry is evicted and None is returned.
    clock["t"] = 1000.0 + 100 + 1
    assert runner._get_cached_backend("https://x") is None
    # Eviction removed the key from the underlying cache dict.
    assert "https://x" not in runner._backend_detect_cache
