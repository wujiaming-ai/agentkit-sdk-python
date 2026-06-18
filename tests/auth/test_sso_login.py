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

"""sso.login(): the OIDC id_token / access_token are persisted to the session store."""

import datetime

import jwt

from agentkit.auth.profile import AuthProfile


def _id_token(exp_delta=3600, sub="u"):
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    return jwt.encode(
        {"sub": sub, "exp": int(now + exp_delta)},
        "test-signing-secret-0123456789abcdef", algorithm="HS256",
    )


class _Assumed:
    access_key_id = "AK"
    secret_access_key = "SK"
    session_token = "TOK"
    expired_at = None


def test_login_persists_id_and_access_token(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTKIT_HOME", str(tmp_path))
    import agentkit.auth.sso as sso

    tok = _id_token()
    monkeypatch.setattr(
        sso, "run_loopback_login",
        lambda client, **k: {"id_token": tok, "access_token": "at-1", "refresh_token": "rt-1"},
    )
    monkeypatch.setattr(sso, "assume_role_with_oidc", lambda *a, **k: _Assumed())
    monkeypatch.setattr(sso, "get_caller_identity", lambda *a, **k: {"AccountId": "123"})

    prof = AuthProfile(
        name="default", issuer="https://up.example.com", client_id="c1", role_trn="trn:iam::1:role/r",
    )
    sess = sso.login(prof, harden_ssl=False)

    # in-memory session carries the OIDC tokens (contract clause 1)
    assert sess._id_token == tok
    assert sess._access_token == "at-1"
    # and they are written to the persistent store (the real id_token, not an access_token fallback)
    from agentkit.auth import store
    blob = store.load_session("default")
    assert blob["id_token"] == tok
    assert blob["access_token"] == "at-1"
    assert blob["refresh_token"] == "rt-1"
