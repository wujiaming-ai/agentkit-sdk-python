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

"""`_prepare_runtime_config` API-key-name generation is gated on auth type."""

from agentkit.toolkit.config.constants import (
    AUTH_TYPE_CUSTOM_JWT,
    AUTH_TYPE_KEY_AUTH,
    AUTO_CREATE_VE,
)
from agentkit.toolkit.runners.ve_agentkit import (
    VeAgentkitRunnerConfig,
    VeAgentkitRuntimeRunner,
)


def _config(auth_type: str) -> VeAgentkitRunnerConfig:
    # Pin name/role so only the API-key-name branch is exercised.
    return VeAgentkitRunnerConfig(
        runtime_name="rt",
        runtime_role_name="role",
        runtime_apikey_name=AUTO_CREATE_VE,
        runtime_auth_type=auth_type,
    )


def test_custom_jwt_does_not_generate_api_key_name():
    runner = VeAgentkitRuntimeRunner()
    cfg = _config(AUTH_TYPE_CUSTOM_JWT)

    assert runner._prepare_runtime_config(cfg) is True
    # custom_jwt authorizes via JWT, so no API key name is generated.
    assert cfg.runtime_apikey_name == AUTO_CREATE_VE


def test_key_auth_still_generates_api_key_name():
    runner = VeAgentkitRuntimeRunner()
    cfg = _config(AUTH_TYPE_KEY_AUTH)

    assert runner._prepare_runtime_config(cfg) is True
    # key_auth uses the API key name, so it is generated (no longer "Auto").
    assert cfg.runtime_apikey_name not in (AUTO_CREATE_VE, "")
