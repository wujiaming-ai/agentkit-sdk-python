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

"""CLI auth commands — SSO login / logout / whoami + profile management.

End users can authenticate two ways and both feed the same SDK credential chain:

* **AK/SK** — set ``VOLCENGINE_ACCESS_KEY`` / ``_SECRET_KEY`` (or ``agentkit
  config``); nothing to do here.
* **SSO** — ``agentkit login`` opens a browser, authenticates against the UserPool's
  UserPool (Feishu / ByteDance-SSO / any OIDC IdP) and stores short-lived STS
  credentials. Every later command then works with no AK/SK on disk.
"""

from __future__ import annotations

import json
from typing import Optional

import typer

auth_app = typer.Typer(help="Authentication: SSO login and credential profiles.")


def _echo(obj: object) -> None:
    typer.echo(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _fail(message: str, hint: str | None = None):
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    if hint:
        typer.secho(f"  hint: {hint}", fg=typer.colors.YELLOW, err=True)
    raise typer.Exit(1)


def login_command(
    address: Optional[str] = typer.Argument(
        None,
        help="Login address, e.g. `agentkit login my-pool.example.com` (the UserPool URL) — "
        "the CLI auto-discovers issuer / client id / STS role from the address. "
        "Or a local discovery file from the admin: `agentkit login ./agentkit-cli.json`. "
        "Omit to re-login to the last UserPool.",
    ),
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="Use a named, pre-seeded profile instead of an address."),
    duration: int = typer.Option(3600, "--duration", help="Requested STS credential lifetime, seconds."),
) -> None:
    """Authenticate via browser SSO and store short-lived STS credentials.

    Just type the login address — the UserPool publishes its (non-secret) login
    coordinates at /.well-known/agentkit-cli, the browser logs you in against its
    UserPool, and AssumeRoleWithOIDC confirms you belong to an allowed pool and
    hands back the sandbox permissions. No issuer/client/role flags, no AK/SK.
    """
    from agentkit.auth import login
    from agentkit.auth.errors import AuthError

    try:
        if address:
            typer.secho(f"→ resolving {address} and opening browser for SSO login…", fg=typer.colors.CYAN, err=True)
        elif profile:
            typer.secho(f"→ logging in with profile '{profile}'…", fg=typer.colors.CYAN, err=True)
        else:
            typer.secho("→ re-logging in to the last UserPool…", fg=typer.colors.CYAN, err=True)
        session = login(
            profile,
            address=address,
            duration_seconds=duration,
            on_url=lambda u: typer.secho(f"  if it didn't open, visit:\n  {u}", err=True),
        )
        creds = session.credentials()
    except AuthError as exc:
        _fail(str(exc).split("\n")[0], getattr(exc, "hint", None))

    typer.secho(
        f"✓ logged in ('{session.profile.address or session.profile.name}', "
        f"account {creds.account_id or '?'}, "
        f"expires {creds.expires_at.isoformat() if creds.expires_at else '~1h'})",
        fg=typer.colors.GREEN, err=True,
    )
    typer.secho(
        "  every `agentkit` command now uses these STS credentials — no AK/SK, no env vars.",
        err=True,
    )


def logout_command(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="SSO profile name."),
) -> None:
    """Clear the stored SSO session (refresh token + cached STS credentials)."""
    from agentkit.auth import logout

    removed = logout(profile)
    if removed:
        typer.secho("✓ logged out (session cleared)", fg=typer.colors.GREEN, err=True)
    else:
        typer.secho("nothing to clear (not logged in)", fg=typer.colors.YELLOW, err=True)


def whoami_command(
    profile: Optional[str] = typer.Option(None, "--profile", "-p", help="SSO profile name."),
) -> None:
    """Show the identity behind the current credentials."""
    from agentkit.auth import whoami
    from agentkit.auth.errors import AuthError

    try:
        ident = whoami(profile)
    except AuthError as exc:
        _fail(str(exc).split("\n")[0], getattr(exc, "hint", None))
    _echo(ident)


def _profile_app() -> typer.Typer:
    app = typer.Typer(help="Manage SSO login profiles (non-secret coordinates).")

    @app.command("set")
    def set_profile(
        name: str = typer.Argument(..., help="Profile name."),
        issuer: str = typer.Option(..., "--issuer", help="OIDC issuer URL."),
        client_id: str = typer.Option(..., "--client-id", help="Public OAuth client id."),
        role_trn: str = typer.Option(..., "--role-trn", help="STS role TRN."),
        provider_trn: Optional[str] = typer.Option(None, "--provider-trn", help="IAM OIDC provider TRN."),
        region: str = typer.Option("cn-beijing", "--region"),
    ) -> None:
        from agentkit.auth import AuthProfile, save_profile

        path = save_profile(AuthProfile(
            name=name, issuer=issuer, client_id=client_id, role_trn=role_trn,
            provider_trn=provider_trn, region=region,
        ))
        typer.secho(f"✓ saved profile '{name}' → {path}", fg=typer.colors.GREEN, err=True)

    @app.command("list")
    def list_profiles_cmd() -> None:
        from agentkit.auth import list_profiles

        names = list_profiles()
        _echo(names or [])

    @app.command("show")
    def show_profile(name: Optional[str] = typer.Argument(None)) -> None:
        from agentkit.auth import load_profile
        from agentkit.auth.errors import AuthError

        try:
            _echo(load_profile(name).to_dict())
        except AuthError as exc:
            _fail(str(exc).split("\n")[0], getattr(exc, "hint", None))

    return app


def _admin_app() -> typer.Typer:
    app = typer.Typer(
        help="Admin: provision UserPool CLI login and publish its discovery doc. "
        "Needs Volcengine AK/SK for the account that owns the UserPool.",
    )

    @app.command("doctor")
    def doctor(
        account: Optional[str] = typer.Option(None, "--account", help="Expected account id (guard)."),
        region: str = typer.Option("cn-beijing", "--region"),
        data_plane: bool = typer.Option(
            True, "--data-plane/--no-data-plane",
            help="Also check credential-hosting prerequisites (APIG / VPC / VeFaaS / KMS)."),
    ) -> None:
        """Read-only preflight: is this account ready for sso-setup + credential-hosting?

        Mutates nothing. Run it FIRST on a fresh account to see what must be enabled,
        instead of failing mid-provision.
        """
        from agentkit.auth._openapi import OpenApiClient
        from agentkit.auth.admin import preflight
        from agentkit.auth.errors import AuthError

        try:
            api = OpenApiClient(region=region, expect_account=account)
        except AuthError as exc:
            _fail(str(exc).split("\n")[0], getattr(exc, "hint", None))

        typer.secho(f"\nAgentKit 接入体检 · 账号 {api.account_id} · {region}\n", fg=typer.colors.CYAN, bold=True, err=True)
        checks = preflight(api, credential_hosting=data_plane)
        icon = {"ok": "✓", "fail": "✗", "warn": "!"}
        color = {"ok": typer.colors.GREEN, "fail": typer.colors.RED, "warn": typer.colors.YELLOW}
        failed = 0
        for c in checks:
            st = c["status"]
            failed += st != "ok"
            typer.secho(f"  {icon[st]} {c['name']}", fg=color[st], bold=(st != "ok"), err=True)
            typer.secho(f"      {c['detail']}", err=True)
            if c.get("fix"):
                typer.secho(f"      → {c['fix']}", fg=typer.colors.YELLOW, err=True)
        if failed:
            typer.secho(f"\n{failed} 项未通过 —— 先修好标 → 的项,再跑 sso-setup / credential-hosting。",
                        fg=typer.colors.RED, bold=True, err=True)
            try:
                from agentkit.platform import agentkit_enable_services_url
                typer.secho(f"开通服务: {agentkit_enable_services_url(region=region)}", err=True)
            except Exception:
                pass
            _echo({"checks": checks, "passed": False})
            raise typer.Exit(1)
        typer.secho("\n✓ 全部通过 —— 这账号可以跑 sso-setup + credential-hosting。", fg=typer.colors.GREEN, bold=True, err=True)
        _echo({"checks": checks, "passed": True})

    @app.command("create-userpool")
    def create_userpool(
        name: str = typer.Option(..., "--name", help="UserPool name."),
        region: str = typer.Option("cn-beijing", "--region"),
    ) -> None:
        """Create a UserPool (the IdP). Federate it to Feishu/ByteDance-SSO in the console."""
        from agentkit.auth.admin import create_user_pool
        from agentkit.auth.errors import AuthError

        try:
            uid, issuer = create_user_pool(name, region=region)
        except AuthError as exc:
            _fail(str(exc).split("\n")[0], getattr(exc, "hint", None))
        typer.secho(f"✓ UserPool created: {uid}", fg=typer.colors.GREEN, err=True)
        _echo({"user_pool_uid": uid, "issuer": issuer})

    @app.command("provision")
    def provision(
        user_pool: str = typer.Option(..., "--user-pool", help="UserPool uid."),
        account: Optional[str] = typer.Option(None, "--account", help="Account id (default: caller)."),
        region: str = typer.Option("cn-beijing", "--region"),
    ) -> None:
        """Ensure the public CLI client + IAM OIDC provider + STS role (idempotent)."""
        from agentkit.auth.admin import provision_cli_access
        from agentkit.auth.errors import AuthError

        try:
            coords = provision_cli_access(user_pool, region=region, account_id=account)
        except AuthError as exc:
            _fail(str(exc).split("\n")[0], getattr(exc, "hint", None))
        typer.secho("✓ provisioned CLI login (client + OIDC provider + STS role)", fg=typer.colors.GREEN, err=True)
        _echo(coords.discovery_doc())

    @app.command("sso-setup")
    def sso_setup_cmd(
        yes: bool = typer.Option(False, "--yes", "-y", help="Non-interactive: accept all defaults / provided flags."),
        user_pool: Optional[str] = typer.Option(None, "--user-pool", help="Reuse an existing UserPool uid."),
        create_pool: Optional[str] = typer.Option(None, "--create-pool", help="Create a new UserPool with this name."),
        account: Optional[str] = typer.Option(None, "--account", help="Account id (default: caller)."),
        region: str = typer.Option("cn-beijing", "--region"),
        idp: Optional[str] = typer.Option(None, "--idp", help="Federate an upstream IdP: bytedance | feishu."),
        idp_client_id: Optional[str] = typer.Option(None, "--idp-client-id"),
        idp_secret: Optional[str] = typer.Option(None, "--idp-secret"),
        bucket: Optional[str] = typer.Option(None, "--bucket", help="TOS bucket (default: agentkit-cli-<account>)."),
        domain: Optional[str] = typer.Option(None, "--domain", help="Custom https domain for the login URL."),
        client_name: Optional[str] = typer.Option(None, "--client-name"),
        provider_name: Optional[str] = typer.Option(None, "--provider-name"),
        role_name: Optional[str] = typer.Option(None, "--role-name"),
    ) -> None:
        """One command, interactive: provision EVERYTHING and publish the login address.

        Export AK/SK and run. Automates what it can and lets you confirm/customize
        each value with a pre-filled default — press Enter to accept, or type to
        override. ``--yes`` (or passing flags) runs it non-interactively.
        """
        from agentkit.auth._openapi import OpenApiClient
        from agentkit.auth.admin import CLI_CLIENT_NAME, OIDC_PROVIDER_NAME, ROLE_NAME, sso_setup
        from agentkit.auth.errors import AuthError

        try:
            api = OpenApiClient(region=region, expect_account=account)
        except AuthError as exc:
            _fail(str(exc).split("\n")[0], getattr(exc, "hint", None))
        acct = account or api.account_id
        interactive = not yes

        if interactive:
            typer.secho(f"\nAgentKit SSO 接入配置 — 账号 {acct},地域 {region}\n", fg=typer.colors.CYAN, bold=True, err=True)

        # 1. UserPool:复用或新建
        if not (user_pool or create_pool):
            if interactive:
                user_pool = typer.prompt(
                    "是否复用已有 UserPool?(是:输入 UserPool Uid;否:直接回车新建)",
                    default="", show_default=False,
                ).strip() or None
            if not user_pool:
                create_pool = "agentkit-cli-pool"

        # 2. 上游 IdP 联邦登录
        if interactive and not idp:
            if typer.confirm("是否需要与上游 IdP(如现有 SSO 登录系统)做联邦登录?", default=False):
                idp = typer.prompt("  上游 IdP 类型 (bytedance/feishu)", default="bytedance").strip().lower()
        if idp and not (idp_client_id and idp_secret):
            if not interactive:
                _fail("非交互模式下用 --idp 需同时提供 --idp-client-id 与 --idp-secret。")
            idp_client_id = idp_client_id or typer.prompt(f"  {idp} 应用 client-id")
            idp_secret = idp_secret or typer.prompt(f"  {idp} 应用 secret", hide_input=True)

        # 3. 资源名:统一用默认约定名(不再询问)
        cn, pn, rn = client_name or CLI_CLIENT_NAME, provider_name or OIDC_PROVIDER_NAME, role_name or ROLE_NAME

        # 4. 登录地址:默认 TOS,可定制为自有域名
        bk = bucket or f"agentkit-cli-{acct}"
        dom = domain
        if interactive and not dom:
            dom = typer.prompt(
                "是否定制 SSO 登录地址?(是:输入自定义 https 域名;否:回车用默认)",
                default="", show_default=False,
            ).strip() or None

        # 5. 列出将要做的改动,确认后执行
        if interactive:
            host = dom or f"{bk}.tos-{region}.volces.com"
            typer.secho("  将要执行以下配置:", fg=typer.colors.CYAN, bold=True, err=True)
            typer.secho(f"    • UserPool      {'复用 ' + user_pool if user_pool else '新建 (agentkit-cli-pool)'}", err=True)
            typer.secho(f"    • 上游联邦      {idp + ' 联邦登录' if idp else '不配置(用 UserPool 本地/已有登录)'}", err=True)
            typer.secho(f"    • CLI 客户端    {cn}", err=True)
            typer.secho(f"    • OIDC provider {pn}（若 issuer 已有 provider 则自动复用）", err=True)
            typer.secho(f"    • STS 角色      {rn}", err=True)
            typer.secho(f"    • 登录地址      https://{host}", err=True)
            if not typer.confirm("\n  确认执行?", default=True):
                raise typer.Exit(0)

        try:
            result = sso_setup(
                user_pool_uid=user_pool, create_pool_name=create_pool, region=region, account_id=acct,
                idp=idp, idp_client_id=idp_client_id, idp_secret=idp_secret,
                bucket=bk, custom_domain=dom, client_name=cn, provider_name=pn, role_name=rn, api=api,
            )
        except AuthError as exc:
            _fail(str(exc).split("\n")[0], getattr(exc, "hint", None))

        from agentkit.auth.admin import identity_console_url

        c = result.coords
        console = identity_console_url(c.region, user_pool_uid=c.user_pool_uid)

        def _hdr(t):
            typer.secho(t, fg=typer.colors.CYAN, bold=True, err=True)

        typer.secho(
            f"\n✓ SSO 接入已就绪  ·  账号 {c.account_id}  ·  {c.region}\n",
            fg=typer.colors.GREEN, bold=True, err=True,
        )
        _hdr("  登录地址")
        typer.secho("  把这一个地址发给终端用户即可:", err=True)
        typer.secho(f"      agentkit login {result.login_address}\n", fg=typer.colors.GREEN, err=True)

        _hdr("  下一步")
        typer.secho(
            "  1. 在 Identity 控制台为该 UserPool 添加可登录用户\n"
            "     (或配置飞书 / 字节 SSO 等上游联邦登录):",
            err=True,
        )
        typer.secho(f"       {console}", fg=typer.colors.BLUE, underline=True, err=True)
        typer.secho(
            "  2. 终端用户只需用浏览器访问上面的登录地址 —— 无需 AK/SK、无需任何额外配置。\n",
            err=True,
        )

        for step in result.manual_steps:
            typer.secho(f"  ⚠ 必做:{step}\n", fg=typer.colors.YELLOW, err=True)

        _hdr("  已创建 / 复用的资源")
        for label, value in (
            ("UserPool", c.user_pool_uid),
            ("CLI 客户端", f"{cn}  ·  {c.client_id}"),
            ("OIDC provider", c.provider_trn.rsplit('/', 1)[-1]),
            ("STS 角色", c.role_trn.rsplit('/', 1)[-1]),
        ):
            typer.secho(f"      {label:<16}{value}", err=True)
        typer.secho("", err=True)

        _echo({"login_address": result.login_address, "identity_console": console})

    @app.command("publish")
    def publish(
        user_pool: str = typer.Option(..., "--user-pool", help="UserPool uid."),
        bucket: str = typer.Option(..., "--bucket", help="TOS bucket to host the discovery doc."),
        account: Optional[str] = typer.Option(None, "--account"),
        region: str = typer.Option("cn-beijing", "--region"),
    ) -> None:
        """Provision (if needed) and publish /.well-known/agentkit-cli; print the login address."""
        from agentkit.auth.admin import provision_cli_access, publish_discovery
        from agentkit.auth.errors import AuthError

        try:
            coords = provision_cli_access(user_pool, region=region, account_id=account)
            url = publish_discovery(coords, bucket=bucket)
        except AuthError as exc:
            _fail(str(exc).split("\n")[0], getattr(exc, "hint", None))
        typer.secho(f"✓ published. End users now run:\n    agentkit login {url}", fg=typer.colors.GREEN, err=True)
        _echo({"login_address": url, "discovery": f"{url}/.well-known/agentkit-cli"})

    return app


def credential_hosting_command(
    yes: bool = typer.Option(False, "--yes", "-y", help="Non-interactive: accept defaults / provided flags."),
    gateway: Optional[str] = typer.Option(None, "--gateway", help="API gateway id that fronts the model."),
    key: Optional[str] = typer.Option(None, "--key", help="The model API key to vault (prefer the interactive prompt)."),
    provider: Optional[str] = typer.Option(None, "--provider", help="KMS provider name (default: agentkit-model-key)."),
    upstream: Optional[str] = typer.Option(None, "--upstream", help="Full API base URL, e.g. https://ark.cn-beijing.volces.com/api/plan/v3"),
    model_path: Optional[str] = typer.Option(None, "--model-path", help="Override the API path (normally taken from --upstream)."),
    account: Optional[str] = typer.Option(None, "--account"),
    region: str = typer.Option("cn-beijing", "--region"),
    auth_mode: str = typer.Option("ticket", "--auth-mode", help="Inbound auth: ticket (default) | jwt | both (jwt = UserPool JWT identity binding)."),
    jwt_issuer: Optional[str] = typer.Option(None, "--jwt-issuer", help="UserPool issuer URL the gateway validates inbound JWTs against (jwt/both)."),
    jwt_audience: Optional[list[str]] = typer.Option(None, "--jwt-audience", help="Allowed JWT audience (repeatable)."),
    jwks_upstream_id: Optional[str] = typer.Option(None, "--jwks-upstream-id", help="Pre-created Domain upstream id the gateway fetches the JWKS from (jwt/both)."),
) -> None:
    """Host one or more API-key credentials so the real key never enters the sandbox.

    Interactive: collect a credential (upstream + path + key), ask whether to add
    another, repeat; then provision all of them on a shared gateway and print, per
    credential, the base URL + the revocable ticket the sandbox carries.
    """
    from agentkit.auth._openapi import OpenApiClient
    from agentkit.auth.credential_hosting import host_credentials, list_gateways
    from agentkit.auth.errors import AuthError

    try:
        api = OpenApiClient(region=region, expect_account=account)
    except AuthError as exc:
        _fail(str(exc).split("\n")[0], getattr(exc, "hint", None))
    acct = account or api.account_id
    interactive = not yes

    from agentkit.auth.credential_hosting import split_base_url as _split

    if interactive:
        typer.secho(f"\nAgentKit 凭据托管 · 账号 {acct}\n", fg=typer.colors.CYAN, bold=True, err=True)

    # 1) 循环收集凭据(一个地址 + 一把 key),直到 admin 说收完
    creds: list[dict] = []
    if interactive:
        while True:
            n = len(creds) + 1
            typer.secho(f"  凭据 #{n}", fg=typer.colors.CYAN, bold=True, err=True)
            prov = typer.prompt("    名称", default=f"cred-{n}").strip()
            url = typer.prompt("    地址").strip()
            k = typer.prompt("    key(隐藏)", hide_input=True)
            if not k:
                _fail("需要提供 key。")
            up, mp = _split(url)
            creds.append({"provider_name": prov, "upstream_url": up, "api_path": mp, "key": k})
            if not typer.confirm("  继续添加?", default=False):
                break
    else:
        if not key:
            _fail("非交互模式需要 --key。")
        up, mp = _split(upstream or "https://ark.cn-beijing.volces.com/api/plan/v3")
        creds.append({"provider_name": provider or "agentkit-model-key",
                      "upstream_url": up, "api_path": model_path or mp, "key": key})

    # 2) 网关:0=自动新建 / 选已有 / --gateway 指定
    gw = gateway
    if not gw and interactive:
        gws = list_gateways(api)
        typer.secho("\n  选择 API 网关:", fg=typer.colors.CYAN, bold=True, err=True)
        typer.secho("    0. 新建网关(自动创建,约 1-2 分钟)", err=True)
        for i, g in enumerate(gws, 1):
            typer.secho(f"    {i}. {g['name']}  ({g['id']})", err=True)
        sel = typer.prompt("  序号(0=新建,或粘贴网关 id)", default="0").strip()
        if sel == "0":
            gw = None
        elif sel.isdigit() and 1 <= int(sel) <= len(gws):
            gw = gws[int(sel) - 1]["id"]
        else:
            gw = sel

    # 3) 汇总,确认后统一执行
    if interactive:
        typer.secho(f"\n  共 {len(creds)} 个凭据 · 网关 {gw or '自动新建'}", fg=typer.colors.CYAN, bold=True, err=True)
        for c in creds:
            typer.secho(f"    {c['provider_name']}  →  {c['upstream_url']}{c['api_path']}", err=True)
        if not typer.confirm("  确认执行?", default=True):
            raise typer.Exit(0)
        typer.secho("  创建中…", err=True)

    try:
        hosted = host_credentials(credentials=creds, gateway_id=gw, region=region, account_id=acct, api=api,
                                  auth_mode=auth_mode, jwt_issuer=jwt_issuer,
                                  jwt_audiences=jwt_audience, jwt_jwks_upstream_id=jwks_upstream_id)
    except AuthError as exc:
        _fail(str(exc).split("\n")[0], getattr(exc, "hint", None))

    typer.secho(f"\n✓ 已托管 {len(hosted)} 个凭据:\n", fg=typer.colors.GREEN, bold=True, err=True)
    for h in hosted:
        typer.secho(f"  {h.provider_name}", fg=typer.colors.CYAN, bold=True, err=True)
        typer.secho(f"    API_BASE = {h.model_base_url}", err=True)
        if h.ticket:
            typer.secho(f"    API_KEY  = {h.ticket}   （门票,非真 key）", err=True)
        else:
            typer.secho("    （JWT 模式:沙箱携带 UserPool JWT,无门票)", err=True)
    typer.secho("", err=True)

    # 绑定:把"地址+门票"写进沙箱工具的环境变量,之后从它建的沙箱自动带托管凭据
    if interactive and hosted and typer.confirm("  写进某个沙箱工具(之后建的沙箱自动用,end-user 不碰凭据)?", default=False):
        from agentkit.auth.credential_hosting import set_tool_env

        h = hosted[0]
        if len(hosted) > 1:
            for i, x in enumerate(hosted, 1):
                typer.secho(f"    {i}. {x.provider_name}", err=True)
            sel = typer.prompt("  绑定第几个凭据", default="1").strip()
            if sel.isdigit() and 1 <= int(sel) <= len(hosted):
                h = hosted[int(sel) - 1]
        tid = typer.prompt("  沙箱工具 tool-id").strip()
        base_env = typer.prompt("    地址写进哪个环境变量", default="CODEX_BASE_URL").strip()
        env = {base_env: h.model_base_url}
        if h.ticket:
            key_env = typer.prompt("    门票写进哪个环境变量", default="ARK_API_KEY").strip()
            env[key_env] = h.ticket
        else:
            typer.secho("    JWT 模式(实验特性):不写门票;沙箱需经 shim 携带 UserPool JWT。", err=True)
        try:
            set_tool_env(api, tid, env)
            typer.secho(f"  ✓ 已写入工具 {tid}(工具重新部署约 1-2 分钟)。", fg=typer.colors.GREEN, err=True)
            typer.secho("    部署完成后,从该工具建的沙箱自动用托管凭据;end-user 零操作。", err=True)
        except AuthError as exc:
            typer.secho(f"  · 写入失败:{str(exc).split(chr(10))[0]}", fg=typer.colors.YELLOW, err=True)

    _echo({"hosted": [{"provider": h.provider_name, "api_base": h.model_base_url, "ticket": h.ticket} for h in hosted]})


auth_app.command(name="login")(login_command)
auth_app.command(name="logout")(logout_command)
auth_app.command(name="whoami")(whoami_command)
auth_app.add_typer(_profile_app(), name="profile")
auth_app.add_typer(_admin_app(), name="admin")
