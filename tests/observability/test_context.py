from __future__ import annotations

from contextvars import ContextVar, copy_context

import agentkit.observability.context as context_mod
from agentkit.observability.context import safe_detach_context_token


def test_safe_detach_resets_token_created_in_current_context():
    value: ContextVar[str | None] = ContextVar("value", default=None)
    token = value.set("active")

    assert safe_detach_context_token(token) is True
    assert value.get() is None


def test_safe_detach_ignores_token_created_in_copied_context(caplog):
    value: ContextVar[str | None] = ContextVar("copied_value", default=None)

    def set_in_context():
        return value.set("active")

    token = copy_context().run(set_in_context)

    assert safe_detach_context_token(token) is False
    assert "Failed to detach" not in caplog.text


def test_safe_detach_logs_unexpected_contextvar_reset_failures(caplog):
    class BrokenVar:
        def reset(self, token):
            del token
            raise RuntimeError("reset failed")

    token = type("Token", (), {"var": BrokenVar()})()

    assert safe_detach_context_token(token) is False
    assert "Failed to detach OpenTelemetry context." in caplog.text


def test_safe_detach_falls_back_to_opentelemetry_detach(monkeypatch, caplog):
    calls = []
    token = object()
    monkeypatch.setattr(context_mod.context_api, "detach", lambda value: calls.append(value))

    assert safe_detach_context_token(token) is True
    assert calls == [token]

    def fail_detach(value):
        del value
        raise RuntimeError("detach failed")

    monkeypatch.setattr(context_mod.context_api, "detach", fail_detach)

    assert safe_detach_context_token(object()) is False
    assert "Failed to detach OpenTelemetry context." in caplog.text
