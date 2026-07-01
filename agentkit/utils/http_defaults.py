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

"""Single source of truth for HTTP timeout/retry defaults.

Defaults and their controlling environment variables live here so the
HTTP/credential/SDK paths share one consistent, env-tunable configuration.
``AGENTKIT_HTTP_RETRIES=0`` disables retries.
"""

import os

ENV_HTTP_TIMEOUT = "AGENTKIT_HTTP_TIMEOUT"
ENV_HTTP_RETRIES = "AGENTKIT_HTTP_RETRIES"
ENV_STREAM_TIMEOUT = "AGENTKIT_STREAM_TIMEOUT"

DEFAULT_HTTP_TIMEOUT = 30.0
DEFAULT_HTTP_RETRIES = 2
DEFAULT_STREAM_TIMEOUT = 300.0


def http_timeout() -> float:
    """Return the per-request HTTP timeout in seconds (clamped to >= 1.0)."""
    try:
        return max(1.0, float(os.getenv(ENV_HTTP_TIMEOUT, "30")))
    except ValueError:
        return DEFAULT_HTTP_TIMEOUT


def http_retries() -> int:
    """Return the number of HTTP retries (clamped to >= 0; 0 disables retries)."""
    try:
        return max(0, int(os.getenv(ENV_HTTP_RETRIES, "2")))
    except ValueError:
        return DEFAULT_HTTP_RETRIES


def stream_timeout() -> float:
    """Return the streaming-response timeout in seconds (clamped to >= 1.0)."""
    try:
        return max(1.0, float(os.getenv(ENV_STREAM_TIMEOUT, "300")))
    except ValueError:
        return DEFAULT_STREAM_TIMEOUT
