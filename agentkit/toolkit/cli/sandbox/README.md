# AgentKit Sandbox Commands

The AgentKit CLI provides top-level helper commands for creating and reusing
AgentKit tool sandbox sessions.

## Install From Current Branch

Use editable mode when testing sandbox CLI changes from the current checkout:

```bash
cd /path/to/agentkit-sdk-python
python3 -m pip install -e .
```

If `uv` is used for the active environment:

```bash
cd /path/to/agentkit-sdk-python
uv pip install -e .
```

If an older PyPI installation shadows the current checkout, uninstall it first
and then reinstall this branch:

```bash
python3 -m pip uninstall agentkit-sdk-python
python3 -m pip install -e .
```

Verify that the CLI is available from the current branch:

```bash
agentkit --help
python3 -m pip show agentkit-sdk-python
```

## Commands

### Create

Create an AgentKit Tool for sandbox sessions. This command prepares the backing
TOS bucket/path, builds a `CreateTool` request, waits until the tool reaches
`Ready`, and prints the created tool ID.

```bash
agentkit create \
  --tool-type CodeEnv \
  --tool-name demo-sandbox-tool \
  --tos-bucket agentkit-platform-example
```

Options:

- `--tool-type`: optional. Tool type to create; defaults to `CodeEnv`.
- `--tool-name`: optional. Tool name. If omitted, the CLI generates a name like
  `agentkit-codeenv-<random>`.
- `--tos-bucket`: optional. TOS bucket mounted at `/home/gem`. If omitted, the
  CLI uses `TOSService.generate_bucket_name()`, which resolves the configured
  default bucket template.
- `--model-name`: optional. Injected into the tool as `OPENCODE_MODEL`,
  `CODEX_MODEL`, and `ANTHROPIC_MODEL`.
- `--model-api-key`: optional. Injected into the tool as `OPENCODE_API_KEY`,
  `CODEX_API_KEY`, and `ANTHROPIC_AUTH_TOKEN`.
- `--model-base-url`: optional. Injected into the tool as `OPENCODE_BASE_URL`,
  `CODEX_BASE_URL`, `MODEL_BASE_URL`, and `ANTHROPIC_BASE_URL`. If omitted,
  Volcengine Ark compatible endpoints are used.

Region configuration is read from environment variables. `AGENTKIT_SANDBOX_REGION`
configures AgentKit Tool operations, and `AGENTKIT_SANDBOX_TOS_REGION` configures
TOS operations. Each defaults to `cn-beijing` when unset or empty.

Credential resolution is delegated to the underlying SDK/service clients:
`AgentkitToolsClient` handles `CreateTool` credentials, and `TOSService` handles
TOS credentials. The command supports the same credential sources as the shared
Volcengine configuration, including environment variables and global
`agentkit config --global` settings.

The generated tool TOS mount uses:

```text
BucketPath: /sandbox-session/default/default
LocalMountPath: /home/gem
Endpoint: http://tos-<region>.ivolces.com
```

When `exec` or `shell` later creates a session from this tool, the session flow
calls `GetTool`, reads this mount configuration, and mounts a per-session path:

```text
/sandbox-session/tool-<tool-id>/session-<session-id>/
```

`agentkit create` itself does not write `.agentkit/tool.json`; `exec` and
`shell` cache resolved or auto-created tools there when they need a tool ID.

### Get

Sync sessions for the current tool, then read a sandbox session from the local
session store.

```bash
agentkit get --session-id 123456789
```

Options:

- `--session-id`: required. Sandbox session ID to look up.
- `--tool-id`: optional. Defaults to `AGENTKIT_SANDBOX_TOOL_ID`. If neither is
  set, the CLI resolves an existing tool by `--tool-type`.
- `--tool-type`: optional. `CodeEnv` or `SkillEnv`; defaults to `CodeEnv`.
  Used when resolving the current tool after `--tool-id` and
  `AGENTKIT_SANDBOX_TOOL_ID` are both absent.

Before returning, `get` calls `ListSessions` for the resolved tool and follows
`NextToken` until all pages are loaded. The returned remote sessions replace
the same tool's records in `.agentkit/sandbox/sessions.json`; records for other
tools are preserved. Sessions whose `UserSessionId` is empty are ignored because
they were not created through this CLI's session flow.

### Shell

Execute a command in a sandbox shell.

```bash
agentkit shell \
  --session-id 123456789 \
  --command 'echo $TEST_VAR' \
  --shell-id shell-example
```

Options:

- `--session-id`: optional. Sandbox session ID used as the local session key.
  If omitted, a UUID is generated and the command creates a sandbox session
  through the same idempotent session ensure flow as `exec`.
- `--tool-id`: optional. Defaults to `AGENTKIT_SANDBOX_TOOL_ID`. If neither is
  set, the CLI resolves a tool by `--tool-type`.
- `--tool-type`: optional. `CodeEnv` or `SkillEnv`; defaults to `CodeEnv`.
  Used when resolving or creating a tool after `--tool-id` and
  `AGENTKIT_SANDBOX_TOOL_ID` are both absent.
- `--command`: required. Command to execute in the sandbox.
- `--exec-dir`: optional execution directory.
- `--shell-id`: optional shell terminal ID for re-entering an existing shell.

The command posts to `<endpoint>/v1/shell/exec` with:

```json
{
  "id": "shell-example",
  "exec_dir": "",
  "command": "echo $TEST_VAR"
}
```

The response is returned as JSON. If the service returns `data.session_id`, the
CLI renames it to `data.shell_id`.

### Exec

Open a streaming WebSocket exec session to the sandbox. By default, this connects
without running an initial command.

```bash
agentkit exec --session-id 123456789
```

Options:

- `--session-id`: optional. Sandbox session ID used as the local
  session key. If omitted, a UUID is generated and the command creates a
  sandbox session through the same idempotent session ensure flow.
- `--tool-id`: optional. Defaults to `AGENTKIT_SANDBOX_TOOL_ID`. If neither is
  set, the CLI resolves a tool by `--tool-type`.
- `--tool-type`: optional. `CodeEnv` or `SkillEnv`; defaults to `CodeEnv`.
  Used when resolving or creating a tool after `--tool-id` and
  `AGENTKIT_SANDBOX_TOOL_ID` are both absent.
- `--command`: optional. Initial command to run after the exec session is ready.
  Omit this option to connect without running an initial command. Use
  `--command codex` to start the remote Codex TUI.
- `--shell-id`: optional. Existing shell terminal ID to connect to. When this is
  set and `--command` is omitted, no initial command is sent.
- `--model-name`: optional. When creating a sandbox session, injects the value
  as `OPENCODE_MODEL`, `CODEX_MODEL`, and `ANTHROPIC_MODEL`.
- `--model-api-key`: optional. When creating a sandbox session, injects the
  value as `OPENCODE_API_KEY`, `CODEX_API_KEY`, and `ANTHROPIC_AUTH_TOKEN`.

The command connects to `<endpoint>/v1/shell/ws`, streams remote output to local
stdout, forwards local stdin as terminal input, sends terminal resize events, and
responds to WebSocket `ping` messages with `pong`.

When the remote terminal returns a shell session ID, the CLI prints it and
stores it as `terminal_shell_id` in `.agentkit/sandbox/sessions.json` while the
connection is active. The CLI removes the current `terminal_shell_id` from the
store when the connection is detached or closed.

Press `Ctrl-]`, or type `exit` / `exit()`, to detach from the local terminal.
`Ctrl-C` is forwarded to the remote process, which is useful for interrupting
Codex or shell commands without closing the local WebSocket client.

## Local Store

`agentkit exec` writes session results to:

```text
.agentkit/sandbox/sessions.json
```

The file is a JSON object keyed by `session_id`:

```json
{
  "123456789": {
    "session_id": "123456789",
    "tool_id": "t-example",
    "instance_id": "s-example",
    "endpoint": "https://example.com/?Authorization=..."
  }
}
```

Repeated exec opens with the same `session_id` refresh the previous
entry when the remote session is reachable, or overwrite it after recreating the
remote session.

When `--tool-id` and `AGENTKIT_SANDBOX_TOOL_ID` are both omitted, `exec` and
`shell` resolve one tool per type through:

1. `.agentkit/tool.json`
2. `ListTools` filtered by `ToolType`
3. automatic `agentkit create --tool-type <type>`-equivalent creation

Resolved tool records are stored in:

```text
.agentkit/tool.json
```

Example:

```json
{
  "CodeEnv": {
    "tool_id": "t-code-example",
    "tool_type": "CodeEnv",
    "name": "agentkit-codeenv-example",
    "status": "Ready"
  },
  "SkillEnv": {
    "tool_id": "t-skill-example",
    "tool_type": "SkillEnv",
    "name": "agentkit-skillenv-example",
    "status": "Ready"
  }
}
```

## Module Layout

- `../cli.py`: registers `create`, `get`, `exec`, and `shell` as top-level commands.
- `session_create.py`: shared session creation and idempotent ensure helpers.
- `session_sync.py`: shared remote session list/sync helpers.
- `tool_resolve.py`: shared sandbox tool resolution and local tool cache helpers.
- `cli_create.py`: create command implementation.
- `cli_get.py`: get command implementation.
- `cli_shell.py`: shell command implementation.
- `cli_exec.py`: streaming exec command implementation.
- `utils.py`: shared store, URL, JSON, and error helpers.
