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

"""Deterministic (network-free) tests for :mod:`agentkit.auth`.

The live SSO→STS browser path is validated end-to-end out of band; here we cover
the persistence, profile, session-refresh, provider and credential-chain wiring.
"""

from __future__ import annotations

import datetime
import json
import os
import stat

import pytest

from agentkit.auth import AuthProfile
from agentkit.auth._redact import mask, redact
from agentkit.auth._sigv4 import sign_headers
from agentkit.auth.oauth import generate_pkce_pair
from agentkit.auth.profile import list_profiles, load_profile, save_profile
from agentkit.auth.providers import AkSkCredentialProvider, SsoStsCredentialProvider
from agentkit.auth.session import AuthSession, StsCredentials
from agentkit.auth.store import clear_session, load_session, save_session


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTKIT_HOME", str(tmp_path))
    return tmp_path


def _profile() -> AuthProfile:
    return AuthProfile(
        name="t",
        issuer="https://userpool-x.userpool.auth.id.cn-beijing.volces.com",
        client_id="cid",
        role_trn="trn:iam::123:role/r",
        provider_trn="trn:iam::123:oidc-provider/p",
    )


# --- profile -----------------------------------------------------------------
def test_profile_roundtrip(home):
    save_profile(_profile())
    assert "t" in list_profiles()
    loaded = load_profile("t")
    assert loaded.client_id == "cid"
    assert loaded.role_trn == "trn:iam::123:role/r"


def test_profile_validation():
    with pytest.raises(Exception):
        AuthProfile(name="x", issuer="http://insecure", client_id="c", role_trn="r").validate()
    with pytest.raises(Exception):
        AuthProfile(name="x", issuer="https://ok", client_id="", role_trn="r").validate()


# --- pkce / sigv4 / redact ---------------------------------------------------
def test_pkce_pair_shape():
    v, c = generate_pkce_pair()
    assert 80 <= len(v) <= 90 and len(c) == 43
    assert "=" not in v and "=" not in c


def test_sigv4_includes_session_token():
    h = sign_headers(
        "POST", "sts.volcengineapi.com",
        {"Action": "GetCallerIdentity", "Version": "2018-01-01"}, b"",
        access_key="AKx", secret_key="sk", service="sts", region="cn-beijing",
        session_token="STStok",
    )
    assert h["X-Security-Token"] == "STStok"
    assert h["Authorization"].startswith("HMAC-SHA256 Credential=AKx/")
    assert "x-security-token" in h["Authorization"]  # signed header is committed


def test_redact_scrubs_secrets():
    out = redact('{"access_token":"abc123","secret_access_key":"xyz789"}')
    assert "abc123" not in out and "xyz789" not in out
    assert mask("ck-979e4f3c20eed4cc").endswith("d4cc")


# --- store -------------------------------------------------------------------
def test_store_roundtrip_and_perms(home):
    save_session("t", {"refresh_token": "rt", "sts": {"access_key": "AK"}})
    blob = load_session("t")
    assert blob and blob["sts"]["access_key"] == "AK"
    # session file is 0600
    path = home / "auth" / "sessions" / "t.json"
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, oct(mode)
    assert clear_session("t")
    assert load_session("t") is None


# --- session -----------------------------------------------------------------
def test_session_blob_roundtrip_and_expiry(home):
    far = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)
    sts = StsCredentials("AK", "SK", "TOK", far, account_id="123")
    sess = AuthSession(_profile(), refresh_token="rt", sts=sts)
    sess.save()
    blob = load_session("t")
    restored = AuthSession.from_blob(_profile(), blob)
    creds = restored.credentials()  # not expired → no network
    assert creds.access_key == "AK" and creds.session_token == "TOK"
    assert not creds.is_expired()


def test_session_expired_without_refresh_raises(home):
    past = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
    sts = StsCredentials("AK", "SK", "TOK", past)
    sess = AuthSession(_profile(), refresh_token=None, sts=sts)
    assert sts.is_expired()
    with pytest.raises(Exception):
        sess.credentials()  # expired + no refresh token


# --- providers ---------------------------------------------------------------
def test_aksk_provider_from_env(monkeypatch):
    monkeypatch.setenv("VOLCENGINE_ACCESS_KEY", "AKx")
    monkeypatch.setenv("VOLCENGINE_SECRET_KEY", "SKx")
    monkeypatch.setenv("VOLCENGINE_SESSION_TOKEN", "TOKx")
    r = AkSkCredentialProvider().resolve()
    assert r and r.access_key == "AKx" and r.session_token == "TOKx" and r.source == "aksk"


def test_sso_provider_resolves_cached_session(home):
    save_profile(_profile())
    far = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)
    AuthSession(_profile(), refresh_token="rt",
                sts=StsCredentials("AKs", "SKs", "TOKs", far)).save()
    r = SsoStsCredentialProvider("t").resolve()
    assert r and r.access_key == "AKs" and r.source == "sso-sts"


def test_sso_provider_none_when_not_logged_in(home):
    assert SsoStsCredentialProvider("nope").resolve() is None


# --- active-profile pointer (the SDK-chain keystone) -------------------------
def test_active_profile_pointer_precedence(home, monkeypatch):
    from agentkit.auth.profile import (
        active_profile_name,
        clear_active_profile,
        set_active_profile,
    )

    monkeypatch.delenv("AGENTKIT_AUTH_PROFILE", raising=False)
    assert active_profile_name() == "default"          # nothing set
    set_active_profile("my-pool.example.com")
    assert active_profile_name() == "my-pool.example.com"  # pointer
    monkeypatch.setenv("AGENTKIT_AUTH_PROFILE", "override")
    assert active_profile_name() == "override"          # env wins over pointer
    monkeypatch.delenv("AGENTKIT_AUTH_PROFILE")
    clear_active_profile()
    assert active_profile_name() == "default"           # cleared


def test_address_to_profile_name():
    from agentkit.auth.profile import address_to_profile_name

    assert address_to_profile_name("https://my-pool.example.com/path?x=1") == "my-pool.example.com"
    assert address_to_profile_name("userpool-abc.userpool.auth.id.cn-beijing.volces.com") \
        == "userpool-abc.userpool.auth.id.cn-beijing.volces.com"


def test_sdk_chain_finds_session_via_pointer_no_env(home, monkeypatch):
    """The keystone: login sets the pointer; a later process resolves the session
    with NO env var and NO profile argument."""
    from agentkit.auth.profile import save_profile, set_active_profile

    monkeypatch.delenv("AGENTKIT_AUTH_PROFILE", raising=False)
    prof = AuthProfile(name="pool.example.com", issuer="https://userpool-x.userpool.auth.id.cn-beijing.volces.com",
                       client_id="cid", role_trn="trn:iam::1:role/r", provider_trn="trn:iam::1:oidc-provider/p")
    save_profile(prof)
    far = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=2)
    AuthSession(prof, refresh_token="rt", sts=StsCredentials("AKp", "SKp", "TOKp", far)).save()
    set_active_profile("pool.example.com")
    # SsoStsCredentialProvider(None) — exactly how configuration.py calls it
    r = SsoStsCredentialProvider(None).resolve()
    assert r and r.access_key == "AKp" and r.source == "sso-sts"


# --- address resolution (well-known) ----------------------------------------
import http.server  # noqa: E402
import threading  # noqa: E402


def _serve_wellknown(doc, status=200):
    body = (json.dumps(doc).encode() if doc is not None else b"not found")

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/.well-known/agentkit-cli" and doc is not None:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_resolve_profile_from_wellknown(home):
    from agentkit.auth.resolve import resolve_profile

    doc = {
        "issuer": "https://userpool-z.userpool.auth.id.cn-beijing.volces.com",
        "client_id": "examplecli01", "role_trn": "trn:iam::100000000000:role/agentkit_cli_role",
        "provider_trn": "trn:iam::100000000000:oidc-provider/agentkit_cli_oidc", "region": "cn-beijing",
    }
    srv, port = _serve_wellknown(doc)
    try:
        prof = resolve_profile(f"http://127.0.0.1:{port}", harden_ssl=False)
        assert prof.client_id == "examplecli01"
        assert prof.role_trn == "trn:iam::100000000000:role/agentkit_cli_role"
        assert prof.transport == "sts"
        assert prof.name == f"127.0.0.1-{port}"  # ':' is replaced for a filesystem-safe name
        assert prof.address == f"http://127.0.0.1:{port}"
    finally:
        srv.shutdown()


def test_resolve_profile_camelcase_getauthconfig(home, monkeypatch):
    """Falls back to /api/auth/config with camelCase Result fields."""
    from agentkit.auth import resolve as R

    def fake_fetch(url, timeout):
        if url.endswith("/.well-known/agentkit-cli"):
            return None  # not served
        return {"Result": {"userPoolIssuer": "https://userpool-q.userpool.auth.id.cn-beijing.volces.com",
                           "cliClientId": "cid2", "roleTrn": "trn:iam::9:role/r",
                           "providerTrn": "trn:iam::9:oidc-provider/p", "region": "cn-beijing"}}

    monkeypatch.setattr(R, "_fetch_json", fake_fetch)  # auto-restored after the test
    prof = R.resolve_profile("https://pool.example.com", harden_ssl=False)
    assert prof.client_id == "cid2" and prof.role_trn == "trn:iam::9:role/r"


def test_resolve_profile_incomplete_hard_fails(home):
    from agentkit.auth.resolve import resolve_profile

    srv, port = _serve_wellknown({"issuer": "https://x.userpool.auth.id.cn-beijing.volces.com"})  # missing client/role
    try:
        with pytest.raises(Exception):
            resolve_profile(f"http://127.0.0.1:{port}", harden_ssl=False)
    finally:
        srv.shutdown()


def test_resolve_profile_from_local_file(home, tmp_path):
    """Offline path: `agentkit login ./discovery.json` — no hosted URL, no network."""
    from agentkit.auth.resolve import resolve_profile

    doc = {
        "issuer": "https://userpool-00000000.userpool.auth.id.cn-beijing.volces.com",
        "client_id": "examplecli01", "role_trn": "trn:iam::100000000000:role/agentkit_cli_role",
        "provider_trn": "trn:iam::100000000000:oidc-provider/agentkit_cli_oidc", "region": "cn-beijing",
    }
    f = tmp_path / "agentkit-cli.json"
    f.write_text(json.dumps(doc))
    prof = resolve_profile(str(f), harden_ssl=False)
    assert prof.client_id == "examplecli01"
    assert prof.issuer.startswith("https://userpool-00000000")
    # name is keyed to the UserPool host (stable across runs)
    assert prof.name == "userpool-00000000.userpool.auth.id.cn-beijing.volces.com"


def test_split_base_url():
    from agentkit.auth.credential_hosting import split_base_url

    assert split_base_url("https://ark.example.com/api/plan/v3") == (
        "https://ark.example.com", "/api/plan/v3")
    assert split_base_url("https://api.partner.com/v1") == ("https://api.partner.com", "/v1")
    assert split_base_url("https://ark.example.com") == ("https://ark.example.com", "/")
    # a schemeless host is tolerated and split correctly
    assert split_base_url("ark.example.com/v3") == ("https://ark.example.com", "/v3")


def test_set_tool_env_merges_existing_envs():
    from agentkit.auth.credential_hosting import set_tool_env

    class _FakeApi:
        def __init__(self):
            self.updated = None

        def call(self, service, action, version, body):
            if action == "GetTool":
                return {"ToolId": body["ToolId"],
                        "Envs": [{"Key": "A", "Value": "1"}, {"Key": "ARK_API_KEY", "Value": "old"}]}
            if action == "UpdateTool":
                self.updated = body
            return {}

    api = _FakeApi()
    set_tool_env(api, "t-x", {"CODEX_BASE_URL": "http://gw/v3", "ARK_API_KEY": "ck-new"})
    envs = {e["Key"]: e["Value"] for e in api.updated["Envs"]}
    assert api.updated["ToolId"] == "t-x"
    assert envs["A"] == "1"                          # pre-existing env preserved
    assert envs["ARK_API_KEY"] == "ck-new"           # overridden
    assert envs["CODEX_BASE_URL"] == "http://gw/v3"  # added


def test_session_path_rejects_traversal():
    import pytest
    from agentkit.auth import store

    for bad in ("../evil", "a/b", "..", ".", "x/../y", "/abs", "", "a b"):
        with pytest.raises(ValueError):
            store._session_path(bad)
    p = store._session_path("my-pool.example.com")
    assert p.name == "my-pool.example.com.json"
    assert p.parent == store.sessions_dir().resolve()


def test_derive_region_handles_numbered_regions():
    from agentkit.auth.resolve import _derive_region

    assert _derive_region("x.cn-beijing.volces.com") == "cn-beijing"
    assert _derive_region("x.ap-southeast-1.volces.com") == "ap-southeast-1"
    assert _derive_region("x.us-east-1.example.com") == "us-east-1"
    assert _derive_region("no-region-here") is None


class _FakeApigApi:
    """Records api.call invocations; returns an Id for Create*, {} otherwise."""

    def __init__(self):
        self.calls = []

    def call(self, service, action, version, body):
        self.calls.append((action, body))
        return {"Id": "id-" + action} if action.startswith("Create") else {}

    def call_ok(self, service, action, version, body, **kw):
        self.calls.append((action, body))
        return {}


def _binds(api):
    import json
    return {b["PluginName"]: json.loads(b["PluginConfig"]) if b.get("PluginConfig") else {}
            for a, b in api.calls if a == "CreatePluginBinding"}


def test_build_gateway_line_ticket_default_unchanged():
    from agentkit.auth import credential_hosting as ch
    api = _FakeApigApi()
    out = ch.build_gateway_line(api, "gw-1", provider_name="p", relay_function_id="fn", service_name="p")
    binds = _binds(api)
    assert "wasm-key-auth" in binds and "wasm-upstream-identity" in binds
    assert "wasm-jwt-auth" not in binds
    assert out["ticket"].startswith("ck-")


def test_build_gateway_line_jwt_mode():
    from agentkit.auth import credential_hosting as ch
    api = _FakeApigApi()
    out = ch.build_gateway_line(api, "gw-1", provider_name="p", relay_function_id="fn", service_name="p",
                                auth_mode="jwt", allow_unenforced_jwt=True, jwt_issuer="https://userpool-x.example.com",
                                jwt_audiences=["aud-1"], jwt_jwks_upstream_id="jwks-up")
    actions = [a for a, _ in api.calls]
    binds = _binds(api)
    assert "wasm-jwt-auth" in binds and "wasm-upstream-identity" in binds
    assert "wasm-key-auth" not in binds
    assert "CreateConsumer" not in actions and "CreateConsumerCredential" not in actions
    assert out["ticket"] is None
    cfg = binds["wasm-jwt-auth"]
    assert cfg["Issuer"] == "https://userpool-x.example.com"
    assert cfg["RemoteJwks"]["UpstreamId"] == "jwks-up"
    assert cfg["RemoteJwks"]["Url"].endswith("/keys")
    assert cfg["AllowedAudiences"] == ["aud-1"]
    assert cfg["FailureModeAllow"] is False


def test_build_gateway_line_both_mode():
    from agentkit.auth import credential_hosting as ch
    api = _FakeApigApi()
    out = ch.build_gateway_line(api, "gw-1", provider_name="p", relay_function_id="fn", service_name="p",
                                auth_mode="both", allow_unenforced_jwt=True, jwt_issuer="https://userpool-x.example.com",
                                jwt_jwks_upstream_id="jwks-up")
    binds = _binds(api)
    assert {"wasm-key-auth", "wasm-jwt-auth", "wasm-upstream-identity"} <= set(binds)
    assert out["ticket"].startswith("ck-")


def test_build_gateway_line_jwt_requires_issuer():
    import pytest
    from agentkit.auth import credential_hosting as ch
    from agentkit.auth.errors import AuthError
    with pytest.raises(AuthError):
        ch.build_gateway_line(_FakeApigApi(), "gw-1", provider_name="p", relay_function_id="fn",
                              service_name="p", auth_mode="jwt")


def test_build_gateway_line_jwt_localjwks_inline():
    from agentkit.auth import credential_hosting as ch
    api = _FakeApigApi()
    out = ch.build_gateway_line(api, "gw-1", provider_name="p", relay_function_id="fn", service_name="p",
                                auth_mode="jwt", allow_unenforced_jwt=True, jwt_issuer="https://userpool-x.example.com",
                                jwt_audiences=["aud-1"], jwt_jwks='{"keys":[{"kid":"k1"}]}')
    cfg = _binds(api)["wasm-jwt-auth"]
    assert cfg["LocalJwks"] == '{"keys":[{"kid":"k1"}]}'   # inline, no JWKS upstream / whitelist
    assert "RemoteJwks" not in cfg
    assert cfg["Issuer"] == "https://userpool-x.example.com"
    assert cfg["FailureModeAllow"] is False
    assert out["ticket"] is None


def test_build_gateway_line_jwt_disabled_by_default():
    # jwt/both is experimental and must refuse without allow_unenforced_jwt so an admin
    # can't accidentally enable an unvalidated inbound-auth path in production.
    import pytest
    from agentkit.auth import credential_hosting as ch
    from agentkit.auth.errors import AuthError
    with pytest.raises(AuthError):
        ch.build_gateway_line(_FakeApigApi(), "gw-1", provider_name="p", relay_function_id="fn",
                              service_name="p", auth_mode="jwt",
                              jwt_issuer="https://userpool-x.example.com", jwt_jwks='{"keys":[]}')


def test_apierror_carries_remediation_hint():
    from agentkit.auth._openapi import ApiError, remediation_for
    assert "whitelist" in (remediation_for("OperationDenied.AccountNotInWhitelist") or "")
    assert remediation_for("InvalidParameter") is None
    e = ApiError("CreateUpstream", "OperationDenied.AccountNotInWhitelist", "nope")
    assert e.hint and "whitelist" in e.hint
