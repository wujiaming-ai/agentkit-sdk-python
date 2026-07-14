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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentkit.apps.a2a_app.a2a_app import AgentkitA2aApp
    from agentkit.apps.agent_server_app.agent_server_app import AgentkitAgentServerApp
    from agentkit.apps.langgraph_server_app.langgraph_server_app import (
        AgentkitLangGraphServerApp,
    )
    from agentkit.apps.mcp_app.mcp_app import AgentkitMCPApp
    from agentkit.apps.simple_app.simple_app import AgentkitSimpleApp


def __getattr__(
    name,
) -> (
    type["AgentkitA2aApp"]
    | type["AgentkitMCPApp"]
    | type["AgentkitSimpleApp"]
    | type["AgentkitAgentServerApp"]
    | type["AgentkitLangGraphServerApp"]
):
    if name == "AgentkitA2aApp":
        from agentkit.apps.a2a_app.a2a_app import AgentkitA2aApp

        return AgentkitA2aApp
    if name == "AgentkitMCPApp":
        from agentkit.apps.mcp_app.mcp_app import AgentkitMCPApp

        return AgentkitMCPApp
    if name == "AgentkitSimpleApp":
        from agentkit.apps.simple_app.simple_app import AgentkitSimpleApp

        return AgentkitSimpleApp
    if name == "AgentkitAgentServerApp":
        from agentkit.apps.agent_server_app.agent_server_app import (
            AgentkitAgentServerApp,
        )

        return AgentkitAgentServerApp
    if name == "AgentkitLangGraphServerApp":
        from agentkit.apps.langgraph_server_app.langgraph_server_app import (
            AgentkitLangGraphServerApp,
        )

        return AgentkitLangGraphServerApp
    raise AttributeError(f"module 'agentkit.apps' has no attribute '{name}'")


__all__ = [
    "AgentkitA2aApp",
    "AgentkitMCPApp",
    "AgentkitSimpleApp",
    "AgentkitAgentServerApp",
    "AgentkitLangGraphServerApp",
]
