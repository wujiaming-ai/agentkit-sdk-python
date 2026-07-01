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

"""Offline unit tests for :mod:`agentkit.toolkit.executors.base_executor`.

Covers the pure, side-effect-free helpers on ``BaseExecutor``: error
classification (``_classify_error``), user-friendly error shaping
(``_handle_exception``), strategy-class lookup (``_get_strategy_class``),
preflight-result handling across every ``PreflightMode`` (``_handle_preflight_result``),
and the ``ServiceNotEnabledException`` constructor. No network, docker, or
``.run()`` is touched; the reporter is a hand-rolled recording fake so the
WARN/PROMPT branches can be spied on.
"""

from __future__ import annotations

import pytest

from agentkit.toolkit.executors.base_executor import (
    BaseExecutor,
    ServiceNotEnabledException,
)
from agentkit.toolkit.models import PreflightMode, PreflightResult
from agentkit.toolkit.strategies import (
    CloudStrategy,
    HybridStrategy,
    LocalStrategy,
)


class _FakeReporter:
    """Recording reporter double.

    Captures every message routed through the executor and lets a test dictate
    the ``confirm`` return value, so the WARN and PROMPT preflight branches can
    be asserted without any UI.
    """

    def __init__(self, confirm_return: bool = False) -> None:
        self.confirm_return = confirm_return
        self.info_calls: list[str] = []
        self.warning_calls: list[str] = []
        self.error_calls: list[str] = []
        self.success_calls: list[str] = []
        self.confirm_calls: list[tuple[str, bool]] = []

    def info(self, message: str, **kwargs) -> None:
        self.info_calls.append(message)

    def success(self, message: str, **kwargs) -> None:
        self.success_calls.append(message)

    def warning(self, message: str, **kwargs) -> None:
        self.warning_calls.append(message)

    def error(self, message: str, **kwargs) -> None:
        self.error_calls.append(message)

    def progress(self, message: str, current: int, total: int = 100, **kwargs) -> None:
        pass

    def confirm(self, message: str, default: bool = False, **kwargs) -> bool:
        self.confirm_calls.append((message, default))
        return self.confirm_return


# ---------------------------------------------------------------------------
# ServiceNotEnabledException.__init__
# ---------------------------------------------------------------------------


def test_service_not_enabled_exception_exposes_missing_services_and_auth_url():
    exc = ServiceNotEnabledException(
        missing_services=["cr", "vefaas"],
        auth_url="https://console.example.com/enable",
    )
    assert exc.missing_services == ["cr", "vefaas"]
    assert exc.auth_url == "https://console.example.com/enable"


def test_service_not_enabled_exception_formats_message_with_joined_services_and_url():
    exc = ServiceNotEnabledException(
        missing_services=["cr", "vefaas"],
        auth_url="https://console.example.com/enable",
    )
    assert str(exc) == (
        "Required services not enabled: cr, vefaas. "
        "Please enable them at: https://console.example.com/enable"
    )


def test_service_not_enabled_exception_is_an_exception_subclass():
    exc = ServiceNotEnabledException(missing_services=["cr"], auth_url="url")
    assert isinstance(exc, Exception)


# ---------------------------------------------------------------------------
# _classify_error : exception type -> error code
# ---------------------------------------------------------------------------


def test_classify_error_maps_service_not_enabled_to_service_not_enabled_code():
    ex = BaseExecutor()
    error = ServiceNotEnabledException(missing_services=["cr"], auth_url="url")
    assert ex._classify_error(error) == "SERVICE_NOT_ENABLED"


def test_classify_error_maps_file_not_found_to_file_not_found_code():
    ex = BaseExecutor()
    assert ex._classify_error(FileNotFoundError("missing.yaml")) == "FILE_NOT_FOUND"


def test_classify_error_maps_value_error_to_invalid_config_code():
    ex = BaseExecutor()
    assert ex._classify_error(ValueError("bad value")) == "INVALID_CONFIG"


def test_classify_error_maps_permission_error_to_permission_denied_code():
    ex = BaseExecutor()
    assert ex._classify_error(PermissionError("nope")) == "PERMISSION_DENIED"


def test_classify_error_maps_timeout_error_to_timeout_code():
    ex = BaseExecutor()
    assert ex._classify_error(TimeoutError("too slow")) == "TIMEOUT"


def test_classify_error_maps_import_error_to_dependency_missing_code():
    ex = BaseExecutor()
    assert ex._classify_error(ImportError("no module")) == "DEPENDENCY_MISSING"


def test_classify_error_maps_generic_exception_to_unknown_error_code():
    ex = BaseExecutor()
    assert ex._classify_error(RuntimeError("something odd")) == "UNKNOWN_ERROR"


def test_classify_error_treats_service_not_enabled_ahead_of_generic_exception():
    # ServiceNotEnabledException derives from Exception; the isinstance ladder
    # must still classify it as the specific code, not fall through to UNKNOWN.
    ex = BaseExecutor()
    error = ServiceNotEnabledException(missing_services=["cr"], auth_url="url")
    assert ex._classify_error(error) == "SERVICE_NOT_ENABLED"


# ---------------------------------------------------------------------------
# _handle_exception : user-friendly message + matching error_code
# ---------------------------------------------------------------------------


def test_handle_exception_returns_success_false_with_error_and_error_code_keys():
    ex = BaseExecutor()
    result = ex._handle_exception("build", RuntimeError("boom"))
    assert set(result) == {"success", "error", "error_code"}
    assert result["success"] is False


def test_handle_exception_service_not_enabled_keeps_original_message_and_code():
    ex = BaseExecutor()
    error = ServiceNotEnabledException(
        missing_services=["cr"], auth_url="https://enable.example"
    )
    result = ex._handle_exception("deploy", error)
    assert result["error"] == str(error)
    assert result["error_code"] == "SERVICE_NOT_ENABLED"


def test_handle_exception_file_not_found_prefixes_file_not_found_message():
    ex = BaseExecutor()
    result = ex._handle_exception("build", FileNotFoundError("agentkit.yaml"))
    assert result["error"] == "File not found: agentkit.yaml"
    assert result["error_code"] == "FILE_NOT_FOUND"


def test_handle_exception_value_error_prefixes_invalid_configuration_message():
    ex = BaseExecutor()
    result = ex._handle_exception("build", ValueError("missing agent_name"))
    assert result["error"] == "Invalid configuration: missing agent_name"
    assert result["error_code"] == "INVALID_CONFIG"


def test_handle_exception_permission_error_prefixes_permission_denied_message():
    ex = BaseExecutor()
    result = ex._handle_exception("deploy", PermissionError("/etc/thing"))
    assert result["error"] == "Permission denied: /etc/thing"
    assert result["error_code"] == "PERMISSION_DENIED"


def test_handle_exception_timeout_error_prefixes_operation_timeout_message():
    ex = BaseExecutor()
    result = ex._handle_exception("deploy", TimeoutError("waited 600s"))
    assert result["error"] == "Operation timeout: waited 600s"
    assert result["error_code"] == "TIMEOUT"


def test_handle_exception_import_error_prefixes_missing_dependency_message():
    ex = BaseExecutor()
    result = ex._handle_exception("build", ImportError("no docker sdk"))
    assert result["error"] == "Missing dependency: no docker sdk"
    assert result["error_code"] == "DEPENDENCY_MISSING"


def test_handle_exception_generic_exception_uses_raw_message_and_unknown_code():
    ex = BaseExecutor()
    result = ex._handle_exception("destroy", RuntimeError("weird failure"))
    assert result["error"] == "weird failure"
    assert result["error_code"] == "UNKNOWN_ERROR"


def test_handle_exception_error_code_always_matches_classify_error():
    ex = BaseExecutor()
    for error in (
        ServiceNotEnabledException(missing_services=["cr"], auth_url="u"),
        FileNotFoundError("f"),
        ValueError("v"),
        PermissionError("p"),
        TimeoutError("t"),
        ImportError("i"),
        RuntimeError("r"),
    ):
        result = ex._handle_exception("op", error)
        assert result["error_code"] == ex._classify_error(error)


# ---------------------------------------------------------------------------
# _get_strategy_class : launch_type -> strategy class / ValueError
# ---------------------------------------------------------------------------


def test_get_strategy_class_returns_local_strategy_for_local():
    ex = BaseExecutor()
    assert ex._get_strategy_class("local") is LocalStrategy


def test_get_strategy_class_returns_cloud_strategy_for_cloud():
    ex = BaseExecutor()
    assert ex._get_strategy_class("cloud") is CloudStrategy


def test_get_strategy_class_returns_hybrid_strategy_for_hybrid():
    ex = BaseExecutor()
    assert ex._get_strategy_class("hybrid") is HybridStrategy


def test_get_strategy_class_raises_value_error_listing_available_strategies_for_unknown():
    ex = BaseExecutor()
    with pytest.raises(ValueError) as excinfo:
        ex._get_strategy_class("serverless")
    message = str(excinfo.value)
    assert "serverless" in message
    assert "local" in message
    assert "cloud" in message
    assert "hybrid" in message


# ---------------------------------------------------------------------------
# _handle_preflight_result : behaviour across every PreflightMode
# ---------------------------------------------------------------------------


def test_handle_preflight_result_returns_true_when_result_passed_regardless_of_mode():
    ex = BaseExecutor(reporter=_FakeReporter())
    passed = PreflightResult(passed=True, missing_services=[])
    # A passing result short-circuits before mode is even consulted.
    assert ex._handle_preflight_result(passed, PreflightMode.FAIL) is True


def test_handle_preflight_result_skip_mode_returns_true_without_reporting():
    reporter = _FakeReporter()
    ex = BaseExecutor(reporter=reporter)
    result = PreflightResult(
        passed=False, missing_services=["cr"], auth_url="https://enable.example"
    )
    assert ex._handle_preflight_result(result, PreflightMode.SKIP) is True
    assert reporter.warning_calls == []
    assert reporter.info_calls == []


def test_handle_preflight_result_warn_mode_returns_true_and_reports_message_and_url():
    reporter = _FakeReporter()
    ex = BaseExecutor(reporter=reporter)
    result = PreflightResult(
        passed=False, missing_services=["cr"], auth_url="https://enable.example"
    )
    assert ex._handle_preflight_result(result, PreflightMode.WARN) is True
    assert reporter.warning_calls == [result.message]
    assert reporter.info_calls == ["Enable services at: https://enable.example"]


def test_handle_preflight_result_fail_mode_raises_service_not_enabled_with_details():
    ex = BaseExecutor(reporter=_FakeReporter())
    result = PreflightResult(
        passed=False,
        missing_services=["cr", "vefaas"],
        auth_url="https://enable.example",
    )
    with pytest.raises(ServiceNotEnabledException) as excinfo:
        ex._handle_preflight_result(result, PreflightMode.FAIL)
    assert excinfo.value.missing_services == ["cr", "vefaas"]
    assert excinfo.value.auth_url == "https://enable.example"


def test_handle_preflight_result_prompt_mode_delegates_to_reporter_confirm_true():
    reporter = _FakeReporter(confirm_return=True)
    ex = BaseExecutor(reporter=reporter)
    result = PreflightResult(
        passed=False, missing_services=["cr"], auth_url="https://enable.example"
    )
    assert ex._handle_preflight_result(result, PreflightMode.PROMPT) is True
    # The warning + info are emitted before the prompt, and confirm drives the result.
    assert reporter.warning_calls == [result.message]
    assert reporter.info_calls == ["Enable services at: https://enable.example"]
    assert reporter.confirm_calls == [
        ("Continue without enabling services?", False)
    ]


def test_handle_preflight_result_prompt_mode_returns_false_when_reporter_declines():
    reporter = _FakeReporter(confirm_return=False)
    ex = BaseExecutor(reporter=reporter)
    result = PreflightResult(
        passed=False, missing_services=["cr"], auth_url="https://enable.example"
    )
    assert ex._handle_preflight_result(result, PreflightMode.PROMPT) is False
    assert reporter.confirm_calls == [
        ("Continue without enabling services?", False)
    ]
