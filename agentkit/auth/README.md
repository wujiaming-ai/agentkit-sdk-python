# agentkit.auth — passwordless login + credential hosting

Two planes that let an AgentKit sandbox run with **no long-lived key on the client
and no model key in the sandbox**:

- **Control plane** — `agentkit login <address>`: browser SSO (OAuth 2.0 / OIDC)
  exchanged for short-lived STS credentials. The end user types only an address.
- **Data plane** — `agentkit credential-hosting`: the model key is vaulted and
  injected at the API gateway; the sandbox carries a revocable ticket, never the key.

The library is **self-contained and stdlib-only**, with no import dependency on
`agentkit.client` / `agentkit.platform` — it can be used standalone or vendored.

| Method | How | On disk |
| --- | --- | --- |
| **AK/SK** | `VOLCENGINE_ACCESS_KEY` / `_SECRET_KEY` (or `agentkit config`) | long-lived key |
| **SSO** | `agentkit login` → browser OAuth/OIDC → STS | only a refresh token + short-lived STS creds |

## Library use

```python
from agentkit.auth import AuthProfile, login, save_profile

save_profile(AuthProfile(
    name="my-pool",
    issuer="https://userpool-<uid>.userpool.auth.id.cn-beijing.volces.com",
    client_id="<PUBLIC_CLI_CLIENT_ID>",             # public PKCE/loopback client
    role_trn="trn:iam::<ACCOUNT_ID>:role/<ROLE_NAME>",
    provider_trn="trn:iam::<ACCOUNT_ID>:oidc-provider/<PROVIDER_NAME>",
))

session = login("my-pool")           # opens a browser; persists the session
creds = session.credentials()        # -> StsCredentials(ak, sk, token, expires_at)
# `credentials()` auto-refreshes from the cached refresh token when near expiry.
```

## CLI — the end user types only an address

```bash
agentkit login my-pool.example.com    # browser SSO -> STS; nothing else to type
agentkit whoami                       # show the verified identity
agentkit sandbox create ...           # transparently uses the STS session
agentkit logout
```

`agentkit login <address>` fetches the UserPool's published, **non-secret**
`/.well-known/agentkit-cli` (issuer / client_id / role_trn / provider_trn /
region), runs the browser PKCE login, then `AssumeRoleWithOIDC` — the OIDC
provider's trust confirms you belong to an allowed UserPool and hands back the
sandbox permissions. The resolved profile is marked **active** (a pointer file)
so the next command finds the session with no env var.

`agentkit login` (no address) re-logs into the last UserPool. A named, manually-seeded
profile is still possible via `agentkit auth profile set NAME --issuer ...` then
`agentkit login -p NAME` (escape hatch when no well-known is served).

## Admin — provision login (control plane)

`agentkit auth admin sso-setup` stands up, in one interactive command, everything
the end user needs and publishes the discovery doc:

- a **public PKCE CLI client** in the UserPool (reusing or creating the pool);
- an **IAM OIDC provider** trusting the pool issuer + that client;
- a **scoped STS role** trusted by the provider;
- the **`/.well-known/agentkit-cli`** document (non-secret) at a real https URL.

Sub-commands `create-userpool` / `provision` / `publish` expose the individual
steps. All provisioning credentials are read from the environment; no secret is
written to the discovery document or to disk, and every write first asserts
`GetCallerIdentity == the expected account`.

## Credential hosting (data plane)

`agentkit credential-hosting` vaults one or more API keys and fronts each upstream
behind the API gateway:

- the key is stored with the credential-provider API and injected at the gateway
  (fail-closed); the sandbox receives only a **revocable ticket**;
- optionally writes the gateway base URL + ticket into a sandbox tool's environment,
  so sessions created from that tool use the hosted credential with no end-user action.

See [`ADMIN-RUNBOOK.md`](ADMIN-RUNBOOK.md) for the end-to-end admin walkthrough.

## How it plugs into the SDK

`agentkit.platform.configuration.VolcConfiguration.get_service_credentials`
resolves credentials in priority order; the SSO session is one source:

```
1. explicit ak/sk      2. service env      3. global env
4. SSO session (this lib)      5. config file      6. VeFaaS IAM      7. .env
```

The SSO source is imported lazily, so the SDK keeps working if this library is
absent. STS credentials carry an `X-Security-Token`, which the SDK signer now
threads through (see `base_service_client.py`).

## Modules

| Module | Responsibility |
| --- | --- |
| `resolve.py` | resolve a login address into a profile (well-known discovery) |
| `oauth.py` | OAuth2 Authorization-Code + PKCE (RFC 8252 loopback) |
| `sts.py` | `AssumeRoleWithOIDC`, `GetCallerIdentity` |
| `session.py` | `AuthSession` — STS creds + auto-refresh |
| `store.py` | secure session store (0600 file / OS keyring) |
| `profile.py` | non-secret login coordinates |
| `providers.py` | `AkSkCredentialProvider`, `SsoStsCredentialProvider` |
| `_sigv4.py` | Volcengine SigV4 signer (session-token aware) |
| `_redact.py` | secret scrubbing for logs/errors |
| `ssl_trust.py` | corporate TLS-proxy CA hardening (truststore / keychain) |
| `admin.py` | admin provisioning for login (control plane) |
| `credential_hosting.py` | model-key vaulting + gateway injection (data plane) |
| `_openapi.py` | minimal signed OpenAPI client used by the admin path |
