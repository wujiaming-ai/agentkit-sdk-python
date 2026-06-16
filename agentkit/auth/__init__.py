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

"""agentkit.auth — passwordless / SSO authentication for AgentKit.

A self-contained, dependency-free library (Python stdlib only) that lets a CLI
or any developer's program obtain Volcengine credentials in two ways:

* **AK/SK** — long-lived access keys from env / config (the existing behaviour).
* **SSO (OAuth 2.0 / OIDC)** — interactive browser login against a UserPool
  (federating Feishu / ByteDance-SSO / any OIDC IdP), exchanged for **short-lived
  STS credentials** via ``AssumeRoleWithOIDC``. No AK/SK ever touches disk.

The library is intentionally decoupled from the rest of the AgentKit SDK: it has
no import dependency on ``agentkit.client`` / ``agentkit.platform`` and can be
vendored or used on its own::

    from agentkit.auth import login, load_session, AuthProfile

    profile = AuthProfile(
        name="my-pool",
        issuer="https://userpool-<uid>.userpool.auth.id.cn-beijing.volces.com",
        client_id="<PUBLIC_CLI_CLIENT_ID>",
        role_trn="trn:iam::<ACCOUNT_ID>:role/<ROLE_NAME>",
        provider_trn="trn:iam::<ACCOUNT_ID>:oidc-provider/<PROVIDER_NAME>",
        region="cn-beijing",
    )
    session = login(profile)            # opens a browser, returns an AuthSession
    creds = session.credentials()        # -> StsCredentials(ak, sk, token, expires_at)

The SDK wires this into its credential-resolution chain (see
``agentkit.platform.configuration``) so that ``agentkit login`` once makes every
subsequent command work with the resulting STS credentials — and an expired STS
session auto-refreshes from the cached OIDC refresh token without a new browser
round-trip.
"""

from __future__ import annotations

from agentkit.auth.errors import AuthError, NetworkError, SsoError
from agentkit.auth.oauth import OAuthClient, run_loopback_login
from agentkit.auth.profile import (
    AuthProfile,
    active_profile_name,
    address_to_profile_name,
    clear_active_profile,
    list_profiles,
    load_profile,
    save_profile,
    set_active_profile,
)
from agentkit.auth.providers import (
    AkSkCredentialProvider,
    CredentialProvider,
    SsoStsCredentialProvider,
)
from agentkit.auth.resolve import discover_cli_config, resolve_profile
from agentkit.auth.session import AuthSession, StsCredentials
from agentkit.auth.sso import login, load_session, logout, whoami
from agentkit.auth.sts import assume_role_with_oidc, get_caller_identity

__all__ = [
    "AuthError",
    "AuthProfile",
    "AuthSession",
    "AkSkCredentialProvider",
    "CredentialProvider",
    "NetworkError",
    "OAuthClient",
    "SsoError",
    "SsoStsCredentialProvider",
    "StsCredentials",
    "active_profile_name",
    "address_to_profile_name",
    "assume_role_with_oidc",
    "clear_active_profile",
    "discover_cli_config",
    "get_caller_identity",
    "list_profiles",
    "load_profile",
    "load_session",
    "login",
    "logout",
    "resolve_profile",
    "run_loopback_login",
    "save_profile",
    "set_active_profile",
    "whoami",
]
