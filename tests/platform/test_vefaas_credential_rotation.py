from __future__ import annotations

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

import json
import os
import types

from agentkit.client.base_service_client import BaseServiceClient
from agentkit.platform.configuration import VolcConfiguration


class _Req:
    def model_dump(self, *, by_alias: bool = True, exclude_none: bool = True):
        return {}


class _Resp:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _DummyClient(BaseServiceClient):
    API_ACTIONS = {"TestAction": "TestAction"}

    def __init__(self, *, platform_config: VolcConfiguration):
        super().__init__(
            service="agentkit",
            service_name="dummy",
            platform_config=platform_config,
        )


def test_base_service_client_refreshes_vefaas_credentials_on_rotation(
    clean_env, mock_global_config, monkeypatch, tmp_path
):
    cred_file = tmp_path / "credential"

    monkeypatch.setattr(
        "agentkit.platform.configuration.VEFAAS_IAM_CREDENTIAL_PATH", str(cred_file)
    )

    cred_file.write_text(
        json.dumps(
            {
                "access_key_id": "ak1",
                "secret_access_key": "sk1",
                "session_token": "t1",
                "expired_time": "2999-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    cfg = VolcConfiguration()
    c = _DummyClient(platform_config=cfg)

    observed = []

    def _json(self, api, params, body):
        observed.append(
            (
                self.service_info.credentials.ak,
                self.service_info.credentials.sk,
                self.service_info.credentials.session_token,
            )
        )
        return json.dumps({"ResponseMetadata": {}, "Result": {}})

    c.json = types.MethodType(_json, c)

    c._invoke_api("TestAction", _Req(), _Resp)
    assert observed[-1] == ("ak1", "sk1", "t1")

    old_mtime_ns = cred_file.stat().st_mtime_ns
    cred_file.write_text(
        json.dumps(
            {
                "access_key_id": "ak2",
                "secret_access_key": "sk2",
                "session_token": "t2",
                "expired_time": "2999-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    os.utime(
        cred_file,
        ns=(old_mtime_ns + 1_000_000_000, old_mtime_ns + 1_000_000_000),
    )

    c._invoke_api("TestAction", _Req(), _Resp)
    assert observed[-1] == ("ak2", "sk2", "t2")
    assert c.access_key == "ak2"
    assert c.secret_key == "sk2"
    assert c.session_token == "t2"
