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

from agentkit.utils.logging_config import get_logger
from agentkit.utils.ve_sign import ve_request

logger = get_logger(__name__)


def request(
    name: str,
    header: dict,
    body: dict,
    action: str,
    ak: str,
    sk: str,
    service: str,
    version: str,
    region: str,
    host: str,
    content_type: str = "application/json",
    target_key: str = "",
):
    logger.info(f"Request {name} ...")

    header = header if header else {}

    response = ve_request(
        request_body=body,
        header=header,
        action=action,
        ak=ak,
        sk=sk,
        service=service,
        version=version,
        region=region,
        host=host,
        content_type=content_type,
    )

    try:
        if target_key:
            for key in target_key.split("."):
                response = getattr(response, key, None)
        return response
    except Exception as e:
        logger.error("Request %s failed: %s", name, e, exc_info=True)
        raise
