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

"""AuthSession data-plane id_token: persistence + exp-based / forced refresh."""

import datetime

import jwt
import pytest

from agentkit.auth.errors import AuthError
from agentkit.auth.profile import AuthProfile


def _id_token(exp_delta=3600, sub="u-alice"):
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    return jwt.encode({"sub": sub, "exp": int(now + exp_delta)}, "test-signing-secret-0123456789abcdef", algorithm="HS256")


def _profile():
    return AuthProfile(
        name="default", issuer="https://userpool-x.example.com",
        client_id="c1", role_trn="trn:iam::1:role/r",
    )


def test_session_blob_roundtrips_id_and_access_token():
    from agentkit.auth.session import AuthSession

    s = AuthSession(_profile(), refresh_token="rt", id_token="id-tok", access_token="ac-tok")
    blob = s.to_blob()
    assert blob["id_token"] == "id-tok"
    assert blob["access_token"] == "ac-tok"
    s2 = AuthSession.from_blob(_profile(), blob)
    assert s2._id_token == "id-tok"
    assert s2._access_token == "ac-tok"


def test_valid_id_token_returns_cached_when_fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTKIT_HOME", str(tmp_path))
    import agentkit.auth.session as sess_mod

    def _no_refresh(self, rt):  # must NOT be called for a fresh token
        raise AssertionError("refresh should not run for a still-valid id_token")

    monkeypatch.setattr(sess_mod.OAuthClient, "refresh", _no_refresh)
    tok = _id_token(3600)
    s = sess_mod.AuthSession(_profile(), refresh_token="rt1", id_token=tok)
    s.save()
    assert s.valid_id_token() == tok


def test_valid_id_token_refreshes_when_expired(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTKIT_HOME", str(tmp_path))
    monkeypatch.setattr("agentkit.auth.ssl_trust.harden_default_ssl_context", lambda *a, **k: None)
    import agentkit.auth.session as sess_mod

    tok_new = _id_token(3600, sub="refreshed")
    monkeypatch.setattr(
        sess_mod.OAuthClient, "refresh",
        lambda self, rt: {"id_token": tok_new, "refresh_token": "rt-2", "access_token": "ac-2"},
    )
    s = sess_mod.AuthSession(_profile(), refresh_token="rt1", id_token=_id_token(-10))  # expired
    s.save()
    assert s.valid_id_token() == tok_new
    assert s._refresh_token == "rt-2"  # rotation persisted
    assert s._access_token == "ac-2"
    # the refreshed token is written back to the store
    from agentkit.auth import store
    assert store.load_session("default")["id_token"] == tok_new


def test_valid_id_token_force_refresh_even_when_fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTKIT_HOME", str(tmp_path))
    monkeypatch.setattr("agentkit.auth.ssl_trust.harden_default_ssl_context", lambda *a, **k: None)
    import agentkit.auth.session as sess_mod

    tok_new = _id_token(3600, sub="forced")
    monkeypatch.setattr(sess_mod.OAuthClient, "refresh", lambda self, rt: {"id_token": tok_new})
    s = sess_mod.AuthSession(_profile(), refresh_token="rt1", id_token=_id_token(3600))
    s.save()
    assert s.valid_id_token(force_refresh=True) == tok_new


def test_valid_id_token_refresh_failure_tells_relogin(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTKIT_HOME", str(tmp_path))
    monkeypatch.setattr("agentkit.auth.ssl_trust.harden_default_ssl_context", lambda *a, **k: None)
    import agentkit.auth.session as sess_mod

    def boom(self, rt):
        raise AuthError("token endpoint rejected the request")

    monkeypatch.setattr(sess_mod.OAuthClient, "refresh", boom)
    s = sess_mod.AuthSession(_profile(), refresh_token="rt1", id_token=_id_token(-10))
    s.save()
    with pytest.raises(AuthError) as ei:
        s.valid_id_token()
    msg = (str(ei.value) + " " + (ei.value.hint or "")).lower()
    assert "login" in msg


def test_valid_id_token_no_refresh_token_tells_relogin(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTKIT_HOME", str(tmp_path))
    import agentkit.auth.session as sess_mod

    s = sess_mod.AuthSession(_profile(), refresh_token=None, id_token=_id_token(-10))
    s.save()
    with pytest.raises(AuthError) as ei:
        s.valid_id_token()
    assert "login" in (str(ei.value) + " " + (ei.value.hint or "")).lower()


def test_renew_locked_still_yields_sts_and_now_stores_id_token(monkeypatch, tmp_path):
    # clause 5: STS renewal path is unchanged (still mints STS) and additionally keeps the
    # freshly-issued id_token for the data plane.
    monkeypatch.setenv("AGENTKIT_HOME", str(tmp_path))
    monkeypatch.setattr("agentkit.auth.ssl_trust.harden_default_ssl_context", lambda *a, **k: None)
    import agentkit.auth.session as sess_mod

    tok_new = _id_token(3600, sub="renewed")
    monkeypatch.setattr(
        sess_mod.OAuthClient, "refresh", lambda self, rt: {"id_token": tok_new, "refresh_token": "rt-2"},
    )

    class _Assumed:
        access_key_id = "AK"
        secret_access_key = "SK"
        session_token = "TOK"
        expired_at = None

    monkeypatch.setattr(sess_mod, "assume_role_with_oidc", lambda *a, **k: _Assumed())

    s = sess_mod.AuthSession(_profile(), refresh_token="rt1")
    sts = s.credentials(force_refresh=True)
    assert sts.access_key == "AK" and sts.session_token == "TOK"  # STS still produced
    assert s._id_token == tok_new  # id_token now kept alongside
