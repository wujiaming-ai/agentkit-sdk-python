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

"""Offline route + wiring guards for ``AgentkitSimpleApp``.

``AgentkitSimpleApp`` is a no-arg Starlette application exposing five routes
(``/ping``, ``/health``, ``/readiness``, ``/liveness``, ``/invoke``). These
tests drive it through ``starlette.testclient.TestClient`` (which pumps the
async handlers synchronously) and never touch a socket -- ``.run()`` is never
called. They pin:

* the JSON shape of the three probe endpoints and their ``service`` field,
* ``/ping`` returning 404 until a ping function is registered and
  ``{"status": <value>}`` afterwards,
* the zero-argument guard inside ``AgentkitSimpleApp.ping``, and
* the ``entrypoint`` wiring onto ``invoke_handler.func``.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from agentkit.apps.simple_app.simple_app import AgentkitSimpleApp


@pytest.fixture()
def app() -> AgentkitSimpleApp:
    return AgentkitSimpleApp()


@pytest.fixture()
def client(app: AgentkitSimpleApp) -> TestClient:
    return TestClient(app)


def test_health_endpoint_returns_healthy_status_and_agent_service(client: TestClient):
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["service"] == "agent-service"
    assert isinstance(body["timestamp"], (int, float))


def test_readiness_endpoint_returns_success_status_and_agent_service(client: TestClient):
    response = client.get("/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["service"] == "agent-service"
    assert isinstance(body["timestamp"], (int, float))


def test_liveness_endpoint_returns_success_status_and_agent_service(client: TestClient):
    response = client.get("/liveness")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["service"] == "agent-service"
    assert isinstance(body["timestamp"], (int, float))


def test_all_probe_endpoints_report_the_same_service_identifier(client: TestClient):
    services = {
        client.get(path).json()["service"]
        for path in ("/health", "/readiness", "/liveness")
    }
    assert services == {"agent-service"}


def test_ping_returns_404_before_any_ping_function_is_registered(client: TestClient):
    response = client.get("/ping")

    assert response.status_code == 404


def test_ping_returns_registered_function_result_wrapped_in_status(
    app: AgentkitSimpleApp, client: TestClient
):
    app.ping(lambda: "pong")

    response = client.get("/ping")

    assert response.status_code == 200
    assert response.json() == {"status": "pong"}


def test_ping_wraps_a_non_string_return_value_verbatim_under_status(
    app: AgentkitSimpleApp, client: TestClient
):
    # PingHandler.handle wraps the raw return value in {"status": result}
    # directly (it does not route through _format_ping_status), so a plain
    # int survives as-is.
    app.ping(lambda: 1)

    response = client.get("/ping")

    assert response.status_code == 200
    assert response.json() == {"status": 1}


def test_ping_accepts_a_zero_argument_function(app: AgentkitSimpleApp):
    def health() -> str:
        return "ok"

    returned = app.ping(health)

    # ping() returns the function unchanged (decorator style) and wires it up.
    assert returned is health
    assert app.ping_handler.func is health


def test_ping_rejects_a_function_that_declares_parameters(app: AgentkitSimpleApp):
    def health(request):  # noqa: ANN001 - shape under test
        return "ok"

    with pytest.raises(TypeError) as excinfo:
        app.ping(health)

    assert "health" in str(excinfo.value)
    # The rejected function must not be wired onto the handler.
    assert app.ping_handler.func is None


def test_ping_rejects_a_lambda_that_takes_a_parameter(app: AgentkitSimpleApp):
    with pytest.raises(TypeError):
        app.ping(lambda x: x)

    assert app.ping_handler.func is None


def test_entrypoint_wires_the_function_onto_invoke_handler_and_returns_it(
    app: AgentkitSimpleApp,
):
    def agent(payload):  # noqa: ANN001 - shape under test
        return payload

    returned = app.entrypoint(agent)

    assert returned is agent
    assert app.invoke_handler.func is agent


def test_fresh_app_has_no_entrypoint_or_ping_function_registered(
    app: AgentkitSimpleApp,
):
    assert app.invoke_handler.func is None
    assert app.ping_handler.func is None
