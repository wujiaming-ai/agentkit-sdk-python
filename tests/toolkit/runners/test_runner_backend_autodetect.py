from __future__ import annotations

from typing import Any


class _DummyRunner:
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


def test_autodetect_invoke_success_no_probe(monkeypatch) -> None:
    from agentkit.toolkit.runners.base import Runner
    import agentkit.toolkit.runners.base as base_mod

    runner: Runner = _DummyRunner()

    calls: list[tuple[str, Any]] = []

    def _http_post_invoke(endpoint, payload, headers, stream, timeout):
        calls.append((endpoint, payload))
        return True, {"ok": True}

    monkeypatch.setattr(runner, "_http_post_invoke", _http_post_invoke)
    monkeypatch.setattr(
        runner,
        "_detect_adk_backend",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()),
    )
    monkeypatch.setattr(
        base_mod.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()),
    )

    ctx = Runner.InvokeContext(
        base_endpoint="https://x/",
        invoke_endpoint="https://x/invoke",
        headers={},
        is_a2a=False,
    )
    ok, resp, is_stream = runner._invoke_with_adk_compat(
        ctx, payload={"prompt": "hi"}, policy=Runner.TimeoutPolicy()
    )
    assert ok is True
    assert resp == {"ok": True}
    assert is_stream is False
    assert calls == [("https://x/invoke", {"prompt": "hi"})]


def test_autodetect_fallbacks_to_adk_and_caches(monkeypatch) -> None:
    from agentkit.toolkit.runners.base import Runner
    import agentkit.toolkit.runners.base as base_mod

    runner: Runner = _DummyRunner()
    runner._backend_detect_cache_ttl_s = 9999

    adk_detect_calls = {"n": 0}
    sse_calls = {"n": 0}

    def _http_post_invoke(endpoint, payload, headers, stream, timeout):
        if endpoint.endswith("/invoke"):
            return False, "Invocation failed: 404"
        raise AssertionError()

    def _detect_adk_backend(*args, **kwargs):
        adk_detect_calls["n"] += 1
        return True

    def _post_run_sse(*args, **kwargs):
        sse_calls["n"] += 1
        return True, {"ok": True}

    monkeypatch.setattr(runner, "_http_post_invoke", _http_post_invoke)
    monkeypatch.setattr(runner, "_detect_adk_backend", _detect_adk_backend)
    monkeypatch.setattr(runner, "_get_adk_app_name", lambda *a, **k: "app")
    monkeypatch.setattr(runner, "_ensure_adk_session", lambda *a, **k: True)
    monkeypatch.setattr(runner, "_post_run_sse", _post_run_sse)
    monkeypatch.setattr(
        base_mod.requests,
        "get",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError()),
    )

    ctx = Runner.InvokeContext(
        base_endpoint="https://x/",
        invoke_endpoint="https://x/invoke",
        headers={"user_id": "u", "session_id": "s"},
        is_a2a=False,
    )

    ok, _, _ = runner._invoke_with_adk_compat(
        ctx, payload={"prompt": "hi"}, policy=Runner.TimeoutPolicy()
    )
    assert ok is True
    assert adk_detect_calls["n"] == 1
    assert sse_calls["n"] == 1

    ok2, _, _ = runner._invoke_with_adk_compat(
        ctx, payload={"prompt": "hi"}, policy=Runner.TimeoutPolicy()
    )
    assert ok2 is True
    assert adk_detect_calls["n"] == 1
    assert sse_calls["n"] == 2


def test_autodetect_fallbacks_to_a2a_and_caches(monkeypatch) -> None:
    from agentkit.toolkit.runners.base import Runner
    import agentkit.toolkit.runners.base as base_mod

    runner: Runner = _DummyRunner()
    runner._backend_detect_cache_ttl_s = 9999

    get_calls = {"n": 0}
    invoke_calls: list[tuple[str, Any]] = []

    class _Resp:
        status_code = 200

        def json(self):
            return {"name": "a2a-agent", "capabilities": {}}

    def _requests_get(*args, **kwargs):
        get_calls["n"] += 1
        return _Resp()

    def _detect_adk_backend(*args, **kwargs):
        return False

    def _http_post_invoke(endpoint, payload, headers, stream, timeout):
        invoke_calls.append((endpoint, payload))
        if endpoint.endswith("/invoke"):
            return False, "Invocation failed: 404"
        assert endpoint == "https://x"
        assert isinstance(payload, dict)
        assert payload.get("jsonrpc") == "2.0"
        return True, {"ok": True}

    monkeypatch.setattr(base_mod.requests, "get", _requests_get)
    monkeypatch.setattr(runner, "_detect_adk_backend", _detect_adk_backend)
    monkeypatch.setattr(runner, "_http_post_invoke", _http_post_invoke)

    ctx = Runner.InvokeContext(
        base_endpoint="https://x/",
        invoke_endpoint="https://x/invoke",
        headers={"user_id": "u", "session_id": "s"},
        is_a2a=False,
    )

    ok, _, _ = runner._invoke_with_adk_compat(
        ctx, payload={"prompt": "hi"}, policy=Runner.TimeoutPolicy()
    )
    assert ok is True
    assert get_calls["n"] == 1

    ok2, _, _ = runner._invoke_with_adk_compat(
        ctx, payload={"prompt": "hi"}, policy=Runner.TimeoutPolicy()
    )
    assert ok2 is True
    assert get_calls["n"] == 1
    assert any(ep == "https://x" for ep, _ in invoke_calls)
