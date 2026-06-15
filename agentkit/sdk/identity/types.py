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

# Request/response models for the InboundAuthConfig APIs.
#
# ``CreateInboundAuthConfig`` mirrors the published OpenAPI definition
# (Version 2025-10-30). ``ListInboundAuthConfigs`` and
# ``DeleteInboundAuthConfig`` follow the standard Volcengine list/delete
# conventions; adjust the field names here if the published specs differ.

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class IdentityBaseModel(BaseModel):
    """AgentKit auto-generated base model"""

    model_config = {"populate_by_name": True, "arbitrary_types_allowed": True}


# Data Types
class ApiKeyMetadata(IdentityBaseModel):
    location: Optional[str] = Field(default=None, alias="Location")
    parameter_name: Optional[str] = Field(default=None, alias="ParameterName")


class ApiKeyAuthConfig(IdentityBaseModel):
    api_key_name: str = Field(..., alias="ApiKeyName")
    api_key: Optional[str] = Field(default=None, alias="ApiKey")
    api_key_metadata: Optional[list[ApiKeyMetadata]] = Field(
        default=None, alias="ApiKeyMetadata"
    )
    expiry_timestamp: Optional[int] = Field(default=None, alias="ExpiryTimestamp")


class JwtAuthConfig(IdentityBaseModel):
    discovery_url: str = Field(..., alias="DiscoveryUrl")
    allowed_audiences: Optional[list[str]] = Field(
        default=None, alias="AllowedAudiences"
    )
    allowed_clients: Optional[list[str]] = Field(default=None, alias="AllowedClients")


# CreateInboundAuthConfig - Request
class CreateInboundAuthConfigRequest(IdentityBaseModel):
    auth_type: str = Field(..., alias="AuthType")
    api_key_auth_configs: Optional[list[ApiKeyAuthConfig]] = Field(
        default=None, alias="ApiKeyAuthConfigs"
    )
    jwt_auth_config: Optional[JwtAuthConfig] = Field(
        default=None, alias="JwtAuthConfig"
    )
    config_name: Optional[str] = Field(default=None, alias="ConfigName")
    description: Optional[str] = Field(default=None, alias="Description")
    instance_id: Optional[str] = Field(default=None, alias="InstanceId")


# CreateInboundAuthConfig - Response
class CreateInboundAuthConfigResponse(IdentityBaseModel):
    inbound_auth_config_id: Optional[str] = Field(
        default=None, alias="InboundAuthConfigId"
    )
    trn: Optional[str] = Field(default=None, alias="Trn")
    config_name: Optional[str] = Field(default=None, alias="ConfigName")
    description: Optional[str] = Field(default=None, alias="Description")
    auth_type: Optional[str] = Field(default=None, alias="AuthType")
    jwt_auth_config: Optional[JwtAuthConfig] = Field(
        default=None, alias="JwtAuthConfig"
    )
    api_key_auth_configs: Optional[list[ApiKeyAuthConfig]] = Field(
        default=None, alias="ApiKeyAuthConfigs"
    )
    instance_id: Optional[str] = Field(default=None, alias="InstanceId")
    created_at: Optional[str] = Field(default=None, alias="CreatedAt")
    updated_at: Optional[str] = Field(default=None, alias="UpdatedAt")


# ListInboundAuthConfigs - Request
class ListInboundAuthConfigsRequest(IdentityBaseModel):
    instance_id: Optional[str] = Field(default=None, alias="InstanceId")
    max_results: Optional[int] = Field(default=None, alias="MaxResults")
    next_token: Optional[str] = Field(default=None, alias="NextToken")


# ListInboundAuthConfigs - Response
class InboundAuthConfigForList(IdentityBaseModel):
    inbound_auth_config_id: Optional[str] = Field(
        default=None, alias="InboundAuthConfigId"
    )
    trn: Optional[str] = Field(default=None, alias="Trn")
    config_name: Optional[str] = Field(default=None, alias="ConfigName")
    description: Optional[str] = Field(default=None, alias="Description")
    auth_type: Optional[str] = Field(default=None, alias="AuthType")
    jwt_auth_config: Optional[JwtAuthConfig] = Field(
        default=None, alias="JwtAuthConfig"
    )
    api_key_auth_configs: Optional[list[ApiKeyAuthConfig]] = Field(
        default=None, alias="ApiKeyAuthConfigs"
    )
    instance_id: Optional[str] = Field(default=None, alias="InstanceId")
    created_at: Optional[str] = Field(default=None, alias="CreatedAt")
    updated_at: Optional[str] = Field(default=None, alias="UpdatedAt")


class ListInboundAuthConfigsResponse(IdentityBaseModel):
    inbound_auth_configs: Optional[list[InboundAuthConfigForList]] = Field(
        default=None, alias="InboundAuthConfigs"
    )
    next_token: Optional[str] = Field(default=None, alias="NextToken")


# DeleteInboundAuthConfig - Request
class DeleteInboundAuthConfigRequest(IdentityBaseModel):
    inbound_auth_config_id: str = Field(..., alias="InboundAuthConfigId")


# DeleteInboundAuthConfig - Response
class DeleteInboundAuthConfigResponse(IdentityBaseModel):
    inbound_auth_config_id: Optional[str] = Field(
        default=None, alias="InboundAuthConfigId"
    )
