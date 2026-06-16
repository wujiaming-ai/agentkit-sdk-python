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

"""Resolve a login address into a full :class:`AuthProfile`.

The end-user types only an address; everything else (issuer, public client id,
STS role/provider, region) is discovered from the UserPool's published
``/.well-known/agentkit-cli`` document (all **non-secret** identifiers).

Resolution (precedence: environment override > discovery document):

1. normalize the address (add ``https://``; only loopback may use ``http``);
2. if the host is itself a UserPool address, the issuer *is* the address;
3. fetch ``{base}/.well-known/agentkit-cli`` (JSON); on miss, fall back to
   ``{base}/api/auth/config`` and map its camelCase ``Result`` fields;
4. build a validated profile — secrets in the document are dropped on parse.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from agentkit.auth.errors import AuthError, NetworkError
from agentkit.auth.profile import AuthProfile, address_to_profile_name
from agentkit.auth.ssl_trust import harden_default_ssl_context

_WELL_KNOWN = "/.well-known/agentkit-cli"
_AUTH_CONFIG = "/api/auth/config"
_MAX_BODY = 64 * 1024
_TIMEOUT = 15.0

# camelCase aliases accepted from a GetAuthConfig-style Result.
_ALIASES = {
    "issuer": ("issuer", "userPoolIssuer"),
    "client_id": ("client_id", "cliClientId", "publicClientId", "nativeClientId"),
    "role_trn": ("role_trn", "roleTrn"),
    "provider_trn": ("provider_trn", "providerTrn"),
    "region": ("region",),
    "transport": ("transport",),
    "scope": ("scope",),
}


def _normalize(address: str) -> tuple[str, str]:
    """Return ``(base_url, host)``. Only loopback may use plain http."""
    a = (address or "").strip()
    if not a:
        raise AuthError("a login address is required, e.g. `agentkit login my-pool.example.com`")
    if "://" not in a:
        a = "https://" + a
    parts = urllib.parse.urlsplit(a)
    host = parts.netloc
    is_loopback = host.split(":")[0] in {"127.0.0.1", "localhost", "::1"}
    if parts.scheme != "https" and not is_loopback:
        raise AuthError(f"address must be https:// (got {parts.scheme}://{host})")
    return f"{parts.scheme}://{host}", host


def _is_userpool_host(host: str) -> bool:
    h = host.lower()
    return ".userpool.auth.id." in h or h.startswith("userpool-")


def _fetch_json(url: str, timeout: float) -> dict | None:
    """GET a JSON object, tolerant of content-type; None on miss/non-object."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read(_MAX_BODY + 1)
    except urllib.error.HTTPError:
        return None
    except urllib.error.URLError as exc:
        raise NetworkError(f"cannot reach {url}: {exc.reason}") from exc
    if len(raw) > _MAX_BODY:
        raise AuthError(f"discovery document at {url} is too large")
    try:
        doc = json.loads(raw)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def _pick(doc: dict, field: str) -> str | None:
    for alias in _ALIASES.get(field, (field,)):
        v = doc.get(alias)
        if v:
            return str(v)
    return None


def discover_cli_config(base: str, *, timeout: float = _TIMEOUT) -> dict:
    """Return the merged discovery dict from well-known (preferred) or GetAuthConfig."""
    doc = _fetch_json(base + _WELL_KNOWN, timeout)
    if doc is None:
        cfg = _fetch_json(base + _AUTH_CONFIG, timeout)
        doc = (cfg.get("Result") if isinstance(cfg, dict) else None) or cfg or {}
    return {field: _pick(doc, field) for field in _ALIASES if _pick(doc, field)}


def _load_local_doc(address: str) -> dict | None:
    """If the address points at a local discovery JSON file, read it directly.

    Lets an end user log in fully offline from a discovery doc the admin produced
    (see ``agentkit auth admin publish``) — no hosted URL, no localhost server::

        agentkit login ./agentkit-cli.json

    Returns the parsed dict, or None if the address is not a local file.
    """
    a = (address or "").strip()
    if a.startswith("file://"):
        a = urllib.parse.urlsplit(a).path
    elif "://" in a:
        return None  # an http(s) address — fetch over the network instead
    path = Path(os.path.expanduser(a))
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise AuthError(f"could not read discovery file {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise AuthError(f"discovery file {path} is not a JSON object")
    return doc


def resolve_profile(address: str, *, timeout: float = _TIMEOUT, harden_ssl: bool = True) -> AuthProfile:
    """Resolve a login address (URL) — or a local discovery file — into a profile."""
    if harden_ssl:
        harden_default_ssl_context()

    local = _load_local_doc(address)
    if local is not None:
        disc = {field: _pick(local, field) for field in _ALIASES if _pick(local, field)}
        base = str(disc.get("issuer") or "")
        host = urllib.parse.urlsplit(base).netloc  # name keyed to the UserPool host
    else:
        base, host = _normalize(address)
        disc = discover_cli_config(base, timeout=timeout)

    name = address_to_profile_name(host or address)
    issuer = (f"{base}" if host and _is_userpool_host(host) else None) or disc.get("issuer")
    client_id = disc.get("client_id")
    role_trn = disc.get("role_trn")
    provider_trn = disc.get("provider_trn")
    region = disc.get("region") or _derive_region(host) or "cn-beijing"
    transport = (disc.get("transport") or "sts").lower()
    scope = disc.get("scope") or "openid profile email offline_access"

    missing = [k for k, v in (("issuer", issuer), ("client_id", client_id), ("role_trn", role_trn)) if not v]
    if missing:
        raise AuthError(
            f"{address} did not provide a complete CLI login config (missing: {', '.join(missing)}).",
            hint="the admin must publish GET "
            f"{base}{_WELL_KNOWN} with issuer / client_id / role_trn / provider_trn "
            "(all non-secret). See `agentkit auth admin publish`.",
        )

    return AuthProfile(
        name=name, issuer=str(issuer), client_id=str(client_id), role_trn=str(role_trn),
        provider_trn=provider_trn, region=region, scope=scope, transport=transport,
        address=base,
    ).validate()


def _derive_region(host: str) -> str | None:
    m = re.search(r"\b(?:cn-[a-z]+|(?:us|ap|eu|sa|af|me|ca)-[a-z]+-\d)\b", host)
    return m.group(0) if m else None
