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

from typing import Dict

from agentkit.client import BaseAgentkitClient
from agentkit.sdk.identity.types import (
    CreateInboundAuthConfigRequest,
    CreateInboundAuthConfigResponse,
    DeleteInboundAuthConfigRequest,
    DeleteInboundAuthConfigResponse,
    ListInboundAuthConfigsRequest,
    ListInboundAuthConfigsResponse,
)


class AgentkitIdentityClient(BaseAgentkitClient):
    """AgentKit Identity / Inbound Auth Config Service."""

    API_ACTIONS: Dict[str, str] = {
        "CreateInboundAuthConfig": "CreateInboundAuthConfig",
        "ListInboundAuthConfigs": "ListInboundAuthConfigs",
        "DeleteInboundAuthConfig": "DeleteInboundAuthConfig",
    }

    def __init__(
        self,
        access_key: str = "",
        secret_key: str = "",
        region: str = "",
        session_token: str = "",
    ) -> None:
        super().__init__(
            access_key=access_key,
            secret_key=secret_key,
            region=region,
            session_token=session_token,
            service_name="identity",
        )

    def create_inbound_auth_config(
        self, request: CreateInboundAuthConfigRequest
    ) -> CreateInboundAuthConfigResponse:
        return self._invoke_api(
            api_action="CreateInboundAuthConfig",
            request=request,
            response_type=CreateInboundAuthConfigResponse,
        )

    def list_inbound_auth_configs(
        self, request: ListInboundAuthConfigsRequest
    ) -> ListInboundAuthConfigsResponse:
        return self._invoke_api(
            api_action="ListInboundAuthConfigs",
            request=request,
            response_type=ListInboundAuthConfigsResponse,
        )

    def delete_inbound_auth_config(
        self, request: DeleteInboundAuthConfigRequest
    ) -> DeleteInboundAuthConfigResponse:
        return self._invoke_api(
            api_action="DeleteInboundAuthConfig",
            request=request,
            response_type=DeleteInboundAuthConfigResponse,
        )
