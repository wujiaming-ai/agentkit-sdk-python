# Copyright (c) 2025 Beijing Volcano Engine Technology Co., Ltd. and/or its affiliates.
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

from typing import Callable

from agentkit.apps.agent_server_app.telemetry import telemetry


def _headers(scope: dict) -> dict[str, str]:
    return {
        key.decode("latin-1").lower(): value.decode("latin-1")
        for key, value in scope.get("headers", ())
    }


def _server_address(scope: dict) -> str | None:
    server = scope.get("server")
    if isinstance(server, (tuple, list)) and server:
        return str(server[0])
    return None


class AgentkitTelemetryHTTPMiddleware:
    """OTel-compatible ASGI request instrumentation.

    The middleware enriches an existing auto-instrumented HTTP span when one is
    current. Otherwise it creates a SERVER span using the configured global
    propagator, so W3C Trace Context and custom ``OTEL_PROPAGATORS`` settings
    remain authoritative.
    """

    def __init__(self, app: Callable):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        method = scope.get("method", "")
        path = scope.get("path", "")
        state = telemetry.start_server_request(
            method=method,
            path=path,
            headers=_headers(scope),
            server_address=_server_address(scope),
        )
        finished = False

        def finish(*, exception: BaseException | None = None) -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            telemetry.finish_server_request(state, exception=exception)

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                status = message.get("status")
                if isinstance(status, int):
                    state.status_code = status
            await send(message)
            if (
                message.get("type") == "http.response.body"
                and not message.get("more_body", False)
            ):
                finish()

        try:
            return await self.app(scope, receive, send_wrapper)
        except BaseException as exc:
            finish(exception=exc)
            raise
        finally:
            finish()
