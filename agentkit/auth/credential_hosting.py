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

"""Credential hosting (data plane) — backs ``agentkit credential-hosting``.

Stores one or more API keys with the credential-provider API and fronts each upstream
behind the API gateway, so the sandbox image carries a revocable ticket instead of the
real key. ``host_credentials`` performs, from AK/SK alone, for each credential:

1. store the key via the credential-provider API;
2. deploy a small relay function (adds the ``Bearer`` prefix and streams responses)
   as the gateway upstream;
3. create the gateway service/route and bind the credential-injection + key-auth
   plugins;
4. return the gateway URL + the ticket the sandbox uses.

``set_tool_env`` then writes those values into a sandbox tool's environment so that
sessions created from it use the hosted credential with no end-user action.
"""

from __future__ import annotations

import base64
import io
import json
import secrets
import time
import zipfile
from dataclasses import dataclass

from agentkit.auth._openapi import ApiError, OpenApiClient
from agentkit.auth.errors import AuthError

# A stateless relay: inject Bearer if missing, stream the response, trust the OS CA.
_BEARER_RELAY = r'''
import http.server, os, ssl, urllib.request, urllib.error
UP = os.environ.get("RELAY_UPSTREAM", "https://ark.cn-beijing.volces.com")
CA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cacert.pem")
CTX = ssl.create_default_context(cafile=CA) if os.path.exists(CA) else ssl.create_default_context()
HOP = {"connection","keep-alive","transfer-encoding","te","trailer","upgrade",
       "proxy-authenticate","proxy-authorization","host","content-length","accept-encoding"}
class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    def log_message(self, *a): pass
    def _proxy(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        hd = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        a = hd.pop("Authorization", None) or hd.pop("authorization", None)
        if a:
            hd["Authorization"] = a if a.startswith("Bearer ") else "Bearer " + a
        try:
            r = urllib.request.urlopen(urllib.request.Request(UP + self.path, data=body, headers=hd, method=self.command), timeout=110, context=CTX)
        except urllib.error.HTTPError as e:
            r = e
        except Exception as e:
            self.send_response(502); self.send_header("Content-Type","application/json"); self.end_headers()
            self.wfile.write(('{"error":"relay_upstream_unreachable","detail":"%s"}' % str(e)[:120]).encode()); return
        self.send_response(getattr(r, "status", None) or r.code)
        for k, v in r.headers.items():
            if k.lower() not in HOP: self.send_header(k, v)
        self.end_headers()
        while True:
            c = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
            if not c: break
            self.wfile.write(c); self.wfile.flush()
    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _proxy
if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("0.0.0.0", 8000), H).serve_forever()
'''


@dataclass
class HostedCredential:
    """Result of hosting a model key — what the sandbox needs."""

    provider_name: str
    gateway_url: str
    ticket: str | None   # the key-auth ticket; None in pure-JWT mode (sandbox carries a JWT)
    model_base_url: str   # what the sandbox points the model client at
    region: str
    service_id: str
    relay_function_id: str

    def sandbox_env(self) -> dict[str, str]:
        env = {"MODEL_API_BASE": self.model_base_url}
        if self.ticket:
            env["MODEL_API_KEY"] = self.ticket
        return env


def split_base_url(url: str) -> tuple[str, str]:
    """Split a full API base URL into ``(scheme://host, path)``.

    Tolerates a schemeless host (``ark.example.com/v3`` -> ``https://ark.example.com``,
    ``/v3``). The host becomes the relay upstream; the path is appended to the gateway
    URL the sandbox uses.
    """
    import urllib.parse

    s = (url or "").strip()
    if s and "://" not in s:
        s = "https://" + s
    u = urllib.parse.urlsplit(s)
    if u.scheme and u.netloc:
        return f"{u.scheme}://{u.netloc}", (u.path or "/")
    return s.rstrip("/"), "/"


def set_tool_env(api: OpenApiClient, tool_id: str, updates: dict) -> None:
    """Merge env vars into a sandbox tool so every session created from it inherits them."""
    r = api.call("agentkit", "GetTool", "2025-10-30", {"ToolId": tool_id})
    t = r.get("Tool") if isinstance(r.get("Tool"), dict) else r
    envs = {e.get("Key"): e.get("Value") for e in (t.get("Envs") or []) if e.get("Key")}
    envs.update(updates)
    api.call("agentkit", "UpdateTool", "2025-10-30",
             {"ToolId": tool_id, "Envs": [{"Key": k, "Value": v} for k, v in envs.items()]})


def list_gateways(api: OpenApiClient) -> list[dict]:
    """List running API gateways in the caller's account (id + name).

    Lets an ApiError propagate (e.g. APIG not enabled on the account) so the operator
    sees an actionable failure rather than a misleading empty list.
    """
    res = api.call("apig", "ListGateways", "2021-03-03", {"PageSize": 100, "PageNumber": 1})
    out = []
    for g in (res.get("Items") or res.get("Gateways") or []):
        if str(g.get("Status")) in ("", "Running"):
            out.append({"id": str(g.get("Id") or ""), "name": str(g.get("Name") or "")})
    return out


def ensure_account_ready(api: OpenApiClient, *, pool: str = "default") -> None:
    """Ensure the Agent Identity workload pool exists (created only if missing)."""
    try:
        api.call("id", "GetWorkloadPool", "2025-10-30", {"WorkloadPoolName": pool})
        return  # pool already exists
    except ApiError as exc:
        if "NotFound" not in exc.code and "not found" not in exc.message.lower():
            raise  # surface real failures (Identity not enabled / access denied) with their hint
    # pool not found -> create it
    api.call_ok("id", "CreateWorkloadPool", "2025-10-30",
                {"WorkloadPoolName": pool, "Description": "default credential pool"})


def ensure_gateway(api: OpenApiClient, *, name: str, region: str = "cn-beijing") -> str:
    """Find-or-create a running API gateway; return its id (reuses the default VPC)."""
    for g in list_gateways(api):
        if g["name"] == name:
            return g["id"]
    vpcs = api.call_get("vpc", "DescribeVpcs", "2020-04-01", {"PageNumber": 1, "PageSize": 50})
    vlist = vpcs.get("Vpcs") or []
    vpc = next((v for v in vlist if v.get("IsDefault")), None) or (vlist[0] if vlist else None)
    if not vpc:
        raise AuthError("no VPC in this account to host a gateway; create a VPC first")
    vpc_id = vpc.get("VpcId")
    subs = api.call_get("vpc", "DescribeSubnets", "2020-04-01", {"VpcId": vpc_id, "PageNumber": 1, "PageSize": 50})
    slist = [s for s in (subs.get("Subnets") or []) if s.get("Status") in ("Available", "")]
    if not slist:
        raise AuthError(f"no available subnet in VPC {vpc_id}")
    res = api.call("apig", "CreateGateway", "2021-03-03", {
        "Name": name, "Region": region, "Type": "standard",
        "NetworkSpec": {"VpcId": vpc_id, "SubnetIds": [s["SubnetId"] for s in slist[:2]]},
    })
    gid = str(res.get("Id") or "")
    for _ in range(45):  # ~6 min ceiling; usually Running in ~60-70s
        g = api.call("apig", "GetGateway", "2021-03-03", {"Id": gid}).get("Gateway", {})
        st = g.get("Status")
        if st == "Running":
            return gid
        if st in ("Failed", "Error"):
            raise AuthError(f"gateway {gid} provisioning failed: {st}")
        time.sleep(8)
    raise AuthError(f"gateway {gid} not Running after timeout")


def vault_api_key(api: OpenApiClient, name: str, key: str, *, pool: str = "default") -> str:
    """Vault a key into a KMS API-key credential provider (write-only). Idempotent."""
    api.call_ok("id", "CreateApiKeyCredentialProvider", "2025-10-30",
                {"Name": name, "ApiKey": key, "PoolName": pool})
    return name


def _zip_relay() -> str:
    try:
        import certifi
        from pathlib import Path

        cacert = Path(certifi.where()).read_bytes()
    except Exception:
        cacert = b""
    files = [("relay.py", _BEARER_RELAY.encode())]
    if cacert:  # only bundle a CA file when non-empty — the relay falls back to system CAs otherwise
        files.append(("cacert.pem", cacert))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return base64.b64encode(buf.getvalue()).decode()


def _find_function(api: OpenApiClient, name: str) -> str | None:
    try:
        res = api.call("vefaas", "ListFunctions", "2021-03-03", {"PageSize": 100, "PageNumber": 1})
    except ApiError:
        return None
    for fn in (res.get("Items") or res.get("Functions") or []):
        if fn.get("Name") == name:
            return str(fn.get("Id") or "")
    return None


def deploy_relay(api: OpenApiClient, name: str, upstream_url: str) -> str:
    """Deploy (idempotently) the stateless Bearer relay; return the function id."""
    existing = _find_function(api, name)
    if existing:
        return existing
    fn = api.call("vefaas", "CreateFunction", "2021-03-03", {
        "Name": name, "Runtime": "native-python3.12/v1", "Command": "python3 relay.py",
        "Port": 8000, "MemoryMB": 1024, "RequestTimeout": 120,
        "SourceType": "zip", "Source": _zip_relay(),
        "Envs": [{"Key": "RELAY_UPSTREAM", "Value": upstream_url}],
        "Description": "AgentKit credential-hosting model relay",
    })
    fid = str(fn.get("Id") or "")
    api.call("vefaas", "Release", "2021-03-03", {"FunctionId": fid, "RevisionNumber": 0})
    for _ in range(30):
        st = api.call("vefaas", "GetReleaseStatus", "2021-03-03", {"FunctionId": fid})
        if str(st.get("Status")).lower() in {"done", "succeeded", "success"}:
            break
        if "fail" in str(st.get("Status")).lower():
            raise AuthError(f"relay release failed: {st}")
        time.sleep(5)
    return fid


def _ensure_plugin(api: OpenApiClient, gateway_id: str, plugin_name: str) -> None:
    """Install and enable a gateway plugin (idempotent); set Enable=True explicitly."""
    try:
        api.call("apig", "CreatePlugin", "2022-11-12",
                 {"GatewayId": gateway_id, "PluginName": plugin_name, "PluginConfig": "", "Enable": True})
        return
    except ApiError as exc:
        if "Duplicat" not in exc.code and "Exist" not in exc.code:
            raise
    api.call("apig", "UpdatePlugin", "2022-11-12",
             {"GatewayId": gateway_id, "PluginName": plugin_name, "Enable": True, "PluginConfig": ""})


def _already_exists(exc: ApiError) -> bool:
    c = exc.code.lower()
    return any(tok in c for tok in ("duplicat", "exist", "conflict", "repeat"))


def _bind_plugin(api: OpenApiClient, route_id: str, plugin_name: str, config: dict) -> None:
    """Bind a plugin to a route, tolerating an existing identical binding (idempotent)."""
    try:
        api.call("apig", "CreatePluginBinding", "2021-03-03", {
            "PluginName": plugin_name, "Scope": "ROUTE", "Target": route_id, "Enable": True,
            "PluginConfig": json.dumps(config),
        })
    except ApiError as exc:
        if not _already_exists(exc):
            raise


def ensure_jwks_upstream(api: OpenApiClient, gateway_id: str, issuer: str, *, name: str | None = None) -> str:
    """Find-or-create an HTTPS Domain upstream the gateway uses to fetch the UserPool
    JWKS for ``wasm-jwt-auth``; return its id.

    NOTE: creating a Domain-type upstream may require the account to be enabled for it
    (``CreateUpstream`` can return ``OperationDenied.AccountNotInWhitelist``). If so,
    pre-create the upstream in the console and pass its id as ``jwt_jwks_upstream_id``
    to :func:`build_gateway_line` to skip this, or use the inline-JWKS path instead.
    """
    import urllib.parse

    host = urllib.parse.urlsplit(issuer if "://" in issuer else "https://" + issuer).netloc
    name = name or f"jwks-{(host.split('.')[0] or 'pool')}-up"
    try:
        up = api.call("apig", "CreateUpstream", "2021-03-03", {
            "GatewayId": gateway_id, "Name": name, "SourceType": "Domain",
            "UpstreamSpec": {"Domain": {"DomainList": [{"Domain": host, "Port": 443, "Protocol": "HTTPS"}]}},
        })
    except ApiError as exc:
        if not _already_exists(exc):
            raise
        res = api.call("apig", "ListUpstreams", "2021-03-03",
                       {"GatewayId": gateway_id, "PageNumber": 1, "PageSize": 100})
        up = next((u for u in (res.get("Items") or []) if u.get("Name") == name), {})
    return str(up.get("Id") or "")


def _fetch_jwks(issuer: str) -> str:
    """Fetch the UserPool JWKS document (raw JSON) from ``{issuer}/keys``."""
    import urllib.request

    from agentkit.auth.ssl_trust import harden_default_ssl_context

    harden_default_ssl_context()
    return urllib.request.urlopen(issuer.rstrip("/") + "/keys", timeout=15).read().decode()


def _bind_jwt_auth(api: OpenApiClient, gateway_id: str, route_id: str, *,
                   issuer: str, audiences: list[str] | None,
                   jwks: str | None = None, jwks_upstream_id: str | None = None) -> None:
    """Bind ``wasm-jwt-auth`` to a route to validate the inbound UserPool JWT (RS256) by
    issuer + audience.

    Default path is **inline LocalJwks** — the JWKS document is embedded in the plugin
    config, so it needs no JWKS upstream. Pass ``jwks`` to supply the document, otherwise
    it is fetched from ``{issuer}/keys``. Supply ``jwks_upstream_id`` only to use the
    RemoteJwks path instead (which needs a Domain upstream).
    """
    _ensure_plugin(api, gateway_id, "wasm-jwt-auth")
    cfg: dict = {"Issuer": issuer, "AllowedAudiences": list(audiences or []), "FailureModeAllow": False}
    if jwks_upstream_id and not jwks:
        cfg["RemoteJwks"] = {"UpstreamId": jwks_upstream_id, "Url": issuer.rstrip("/") + "/keys"}
    else:
        cfg["LocalJwks"] = jwks if jwks is not None else _fetch_jwks(issuer)
    _bind_plugin(api, route_id, "wasm-jwt-auth", cfg)


def build_gateway_line(
    api: OpenApiClient, gateway_id: str, *, provider_name: str, relay_function_id: str,
    service_name: str, region: str = "cn-beijing",
    auth_mode: str = "ticket",
    jwt_issuer: str | None = None,
    jwt_audiences: list[str] | None = None,
    jwt_jwks: str | None = None,
    jwt_jwks_upstream_id: str | None = None,
    allow_unenforced_jwt: bool = False,
) -> dict:
    """Create the upstream/service/route and bind the gateway plugins; return a ticket.

    Idempotent for the infrastructure: every resource uses a deterministic name and
    falls back to a list-and-match on an 'already exists' error, so a retry after a
    partial failure converges instead of erroring.

    ``auth_mode`` controls inbound authorization (the outbound ``wasm-upstream-identity``
    key injection is unchanged in every mode):

    - ``"ticket"`` (default): ``wasm-key-auth`` + a revocable ``ck-`` ticket. The ticket
      is a write-only key-auth secret, issued only for a newly created consumer
      credential; if the line already carries one, this raises so the caller can revoke
      and re-issue rather than return a ticket that was never registered.
    - ``"jwt"``: ``wasm-jwt-auth`` validates an inbound UserPool JWT (issuer/audience);
      no ticket is issued (``ticket`` is ``None``). Requires ``jwt_issuer``.
    - ``"both"``: bind both for transition. The combined-enforcement semantics of two
      inbound auth plugins on one route are not yet validated, so this stays experimental.
    """
    if auth_mode not in ("ticket", "jwt", "both"):
        raise AuthError(f"unknown auth_mode {auth_mode!r} (expected ticket|jwt|both)")
    if auth_mode in ("jwt", "both") and not jwt_issuer:
        raise AuthError("auth_mode jwt/both requires jwt_issuer")
    if auth_mode in ("jwt", "both") and not allow_unenforced_jwt:
        raise AuthError(
            "auth_mode 'jwt'/'both' is experimental and disabled by default: inbound JWT "
            "identity binding is not yet validated end-to-end for this gateway line, so the "
            "supported path is the default ticket mode.",
            hint="pass allow_unenforced_jwt=True only in a controlled test, never in production.",
        )

    def _find(action: str, version: str, params: dict, name_field: str, want: str) -> dict:
        res = api.call("apig", action, version, {**params, "PageNumber": 1, "PageSize": 100})
        for it in (res.get("Items") or []):
            if str(it.get(name_field) or "") == want:
                return it
        return {}

    _ensure_plugin(api, gateway_id, "wasm-upstream-identity")
    _ensure_plugin(api, gateway_id, "wasm-key-auth")

    up_name = f"{service_name}-up"
    try:
        up = api.call("apig", "CreateUpstream", "2021-03-03", {
            "GatewayId": gateway_id, "Name": up_name, "SourceType": "VeFaas",
            "Protocol": "HTTP", "UpstreamSpec": {"VeFaas": {"FunctionId": relay_function_id}},
        })
    except ApiError as exc:
        if not _already_exists(exc):
            raise
        up = _find("ListUpstreams", "2021-03-03", {"GatewayId": gateway_id}, "Name", up_name)
    uid = str(up.get("Id") or "")

    try:
        svc = api.call("apig", "CreateGatewayService", "2021-03-03", {
            "GatewayId": gateway_id, "ServiceName": service_name, "Protocol": ["HTTP", "HTTPS"],
            "DomainType": "DefaultDomain", "AuthSpec": {"Enable": False},
            "ServiceNetworkSpec": {"EnablePublicNetwork": True, "EnablePrivateNetwork": False},
        })
    except ApiError as exc:
        if not _already_exists(exc):
            raise
        svc = _find("ListGatewayServices", "2021-03-03", {"GatewayId": gateway_id}, "ServiceName", service_name)
    sid = str(svc.get("Id") or "")

    route_name = f"{service_name}-route"
    try:
        route = api.call("apig", "CreateRoute", "2022-11-12", {
            "Name": route_name, "ServiceId": sid,
            "MatchRule": {"Path": {"MatchType": "Prefix", "MatchContent": "/"}, "Method": ["POST", "GET"]},
            "UpstreamList": [{"UpstreamId": uid, "Weight": 100}], "Enable": True, "Priority": 0,
        })
    except ApiError as exc:
        if not _already_exists(exc):
            raise
        route = _find("ListRoutes", "2022-11-12", {"ServiceId": sid}, "Name", route_name)
    rid = str(route.get("Id") or "")

    _bind_plugin(api, rid, "wasm-upstream-identity",
                 {"ProviderType": "ApiKey", "ProviderName": provider_name, "FailureModeAllow": False})

    ticket: str | None = None
    if auth_mode in ("ticket", "both"):
        try:
            consumer = api.call("apig", "CreateConsumer", "2021-03-03", {"Name": service_name, "GatewayId": gateway_id})
        except ApiError as exc:
            if not _already_exists(exc):
                raise
            consumer = _find("ListConsumers", "2021-03-03", {"GatewayId": gateway_id}, "Name", service_name)
        cid = str(consumer.get("Id") or consumer.get("ConsumerId") or "")

        ticket = "ck-" + secrets.token_hex(20)
        try:
            api.call("apig", "CreateConsumerCredential", "2021-03-03", {
                "ConsumerId": cid, "CredentialType": "key-auth", "KeyAuthCredential": {"APIKey": ticket},
            })
        except ApiError as exc:
            if not _already_exists(exc):
                raise
            raise AuthError(
                f"credential line for '{service_name}' already carries a ticket; revoke it "
                "(or use a different provider name) to issue a new one."
            )

        _bind_plugin(api, rid, "wasm-key-auth",
                     {"KeySources": [{"Header": "authorization"}], "AllowedConsumers": [service_name]})

    if auth_mode in ("jwt", "both"):
        _bind_jwt_auth(api, gateway_id, rid, issuer=jwt_issuer, audiences=jwt_audiences,
                       jwks=jwt_jwks, jwks_upstream_id=jwt_jwks_upstream_id)

    return {"service_id": sid, "route_id": rid, "ticket": ticket,
            # https so the ticket travels over TLS — the default gateway domain serves HTTPS
            # because the service is created with HTTPS in its Protocol list (see above).
            "gateway_url": f"https://{sid}.apigateway-{region}.volceapi.com"}


def host_credentials(
    *,
    credentials: list[dict],
    gateway_id: str | None = None,
    gateway_name: str | None = None,
    region: str = "cn-beijing",
    account_id: str | None = None,
    api: OpenApiClient | None = None,
    auth_mode: str = "ticket",
    jwt_issuer: str | None = None,
    jwt_audiences: list[str] | None = None,
    jwt_jwks: str | None = None,
    jwt_jwks_upstream_id: str | None = None,
    allow_unenforced_jwt: bool = False,
) -> list[HostedCredential]:
    """Host one or more credentials behind a single shared gateway.

    Each item in ``credentials`` is a dict ``{provider_name, key, upstream_url,
    api_path}`` (a credential = an upstream + its key). The gateway and the account
    setup are done once; each credential gets its own provider, relay, and route.
    """
    api = api or OpenApiClient(region=region, expect_account=account_id)
    ensure_account_ready(api)
    if not gateway_id:
        gateway_id = ensure_gateway(api, name=gateway_name or "agentkit-credhost-gw", region=region)
    out: list[HostedCredential] = []
    for c in credentials:
        provider = c["provider_name"]
        upstream = c.get("upstream_url") or "https://ark.cn-beijing.volces.com"
        path = c.get("api_path") or "/api/v3"
        vault_api_key(api, provider, c["key"])
        relay = deploy_relay(api, f"{provider}-relay", upstream)
        line = build_gateway_line(api, gateway_id, provider_name=provider,
                                  relay_function_id=relay, service_name=provider, region=region,
                                  auth_mode=auth_mode, jwt_issuer=jwt_issuer,
                                  jwt_audiences=jwt_audiences, jwt_jwks=jwt_jwks,
                                  jwt_jwks_upstream_id=jwt_jwks_upstream_id,
                                  allow_unenforced_jwt=allow_unenforced_jwt)
        out.append(HostedCredential(
            provider_name=provider, gateway_url=line["gateway_url"], ticket=line["ticket"],
            model_base_url=line["gateway_url"] + path, region=region,
            service_id=line["service_id"], relay_function_id=relay,
        ))
    return out


def host_model_key(
    *,
    key: str,
    gateway_id: str | None = None,
    gateway_name: str | None = None,
    provider_name: str = "agentkit-model-key",
    upstream_url: str = "https://ark.cn-beijing.volces.com",
    model_path: str = "/api/v3",
    region: str = "cn-beijing",
    account_id: str | None = None,
    api: OpenApiClient | None = None,
) -> HostedCredential:
    """Host a single credential (thin wrapper over :func:`host_credentials`)."""
    return host_credentials(
        credentials=[{"provider_name": provider_name, "key": key,
                      "upstream_url": upstream_url, "api_path": model_path}],
        gateway_id=gateway_id, gateway_name=gateway_name, region=region,
        account_id=account_id, api=api,
    )[0]
