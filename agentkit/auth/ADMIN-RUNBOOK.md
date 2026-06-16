# AgentKit 沙箱凭据托管 · Admin Runbook

> 目标:让终端用户用 SSO 登录、进沙箱、用托管的模型 key —— **真 key 永不进沙箱,终端用户不接触任何凭据**。
> Admin 只跑两条命令 + 两个一次性的控制台动作。下文所有尖括号都是占位符,替换成你自己的值。

---

## 前提

- Admin 持有该账号的 Volcengine **AK/SK**(账号内有 IAM / AgentKit 管理权限)。
- 已有一个沙箱工具(自定义镜像,如 codex)。没有的话先建一个:`agentkit tools create`。
- 已装好带本套能力的 `agentkit` CLI。

```sh
export VOLCENGINE_ACCESS_KEY=<AK>
export VOLCENGINE_SECRET_KEY=<SK>
```

---

## ① 配「谁能登录」 —— 每个 UserPool 一次

```sh
agentkit auth admin sso-setup
```

交互(每项有默认,回车即可):

| 提示 | 说明 |
| --- | --- |
| 是否复用已有 UserPool | 输入 UserPool uid 复用;回车则新建 |
| 是否与上游 IdP 做联邦登录 | 接现有 SSO(飞书 / 字节 SSO)时选;否则回车 |
| 是否定制登录地址 | 想用自有 https 域名就填;否则回车用默认 |
| 确认执行 | 回车 |

**产出**:一个**登录地址**(发给终端用户)。命令会自动建公共 PKCE 客户端 + STS 角色(挂会话所需的受限权限)+ 发布登录地址。

配套两个**控制台动作**(命令会把要做的直接打印出来):

1. **仅新建联邦时** —— 在上游 IdP 应用的「允许回调地址」里加入命令打印的回调地址。
2. 在 Identity 控制台给该 UserPool **添加可登录用户**(或确认联邦覆盖范围)—— 命令打印控制台直链。

---

## ② 托管模型 key + 绑到沙箱工具 —— 每把 key 一次

```sh
agentkit credential-hosting
```

交互:

| 步骤 | 说明 |
| --- | --- |
| 凭据 #N | 名称(KMS provider)、**地址**(完整 URL,如 `https://<model-host>/<api-path>`)、**key**(隐藏输入) |
| 继续添加? | 一次可托管多个凭据(模型 key、伙伴 API key 等) |
| 选 API 网关 | `0` 自动新建,或选已有 / 粘贴网关 id |
| 确认执行 | 托管:key 存入 KMS + 部署中继 + 挂网关注入线 + 签发门票 |
| 写进沙箱工具? | 输入 `<TOOL_ID>`,再指定**地址**和**门票**各写进哪个环境变量 |

**产出**:每个凭据一份网关 `API_BASE` + 一张**门票**(可撤销,非真 key)。绑定到工具后,工具重新部署约 1–2 分钟。

> **环境变量名取决于镜像怎么读模型配置**(镜像启动时从 env 渲染配置)。
> codex 镜像的约定:地址 → `CODEX_BASE_URL`、门票 → `ARK_API_KEY`(即命令里的默认值)。
> 换别的镜像时,填该镜像实际读取的 env 名即可。

---

## ③ 终端用户 —— 自助,Admin 不用管

```sh
agentkit login <登录地址>
agentkit sandbox create   --tool-id <TOOL_ID> --user-session-id <会话名> --ttl 3600
agentkit sandbox terminal --user-session-id <会话名>
```

沙箱已配好:模型请求自动经网关、用门票;**沙箱内没有真 key**,终端用户全程不接触凭据。

---

## 验证(可选)

注入矩阵 —— 用 ② 产出的 `API_BASE` + 门票:

```sh
BASE=<API_BASE>; TICKET=<门票>
BODY='{"model":"<model>","input":"hi","max_output_tokens":16}'
curl -s -o /dev/null -w '无票: %{http_code}\n'        -X POST "$BASE/<endpoint>" -H 'Content-Type: application/json' -d "$BODY"
curl -s -o /dev/null -w '有票: %{http_code}\n'        -X POST "$BASE/<endpoint>" -H 'Content-Type: application/json' -H "Authorization: Bearer $TICKET" -d "$BODY"
curl -s -o /dev/null -w '门票直连上游: %{http_code}\n' -X POST "https://<model-host>/<api-path>/<endpoint>" -H 'Content-Type: application/json' -H "Authorization: Bearer $TICKET" -d "$BODY"
```

预期 **401 / 200 / 401**:有票经网关可调,无票拒,门票直连上游无效(门票 ≠ 真 key)。

进一个新建的沙箱 session,确认其中**搜不到真 key**。

---

## 维护

- **换 key**:重跑 ②(新门票即时生效;旧门票可单独撤销)。
- **撤销访问**:撤掉网关门票,或轮转 / 删除 KMS 里的 key。
- **轮换角色权限**:角色默认挂会话所需的系统策略 + 自定义策略;按需收紧或放宽。

---

## 安全须知

- **任何真值不入库**:真 key、真账号 id、真资源 id 一律不写进提交的文件——本文档全用占位符。
- 真 key 只存在于 KMS;沙箱镜像里只烧门票。
- 测试用过的真 key 用完即轮转。
