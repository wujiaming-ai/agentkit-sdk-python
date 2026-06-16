# AgentKit Sandbox Commands

The AgentKit CLI provides `agentkit sandbox` helper commands for creating and
reusing AgentKit tool sandbox sessions.

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
agentkit sandbox create \
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
  `CODEX_API_KEY`, and `ANTHROPIC_AUTH_TOKEN`. If omitted, the CLI uses
  `MODEL_API_KEY` when that environment variable is set.

The tool always injects Volcengine Ark compatible endpoints into
`OPENCODE_BASE_URL`, `CODEX_BASE_URL`, `MODEL_BASE_URL`, and
`ANTHROPIC_BASE_URL`. Custom `--model-base-url` is intentionally not exposed.

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

When `sandbox exec` or `sandbox shell` later creates a session from this tool,
the session flow calls `GetTool`, reads this mount configuration, and mounts a
per-session path:

```text
/sandbox-session/tool-<tool-id>/session-<session-id>/
```

After the tool reaches `Ready`, `agentkit sandbox create` writes the tool
information to `.agentkit/sandbox/tools.json`. Only one tool record is stored
per `ToolType`; creating or resolving another tool of the same type replaces
that type's record.

### Get

Sync sessions for the current tool, then read sandbox sessions from the local
session store.

```bash
agentkit sandbox get --session-id 123456789
agentkit sandbox get
```

Options:

- `--session-id`: optional. Sandbox session ID to look up. If omitted, the CLI
  returns all records from `.agentkit/sandbox/sessions.json` after syncing the
  current tool.
- `--tool-id`: optional. Defaults to `AGENTKIT_SANDBOX_TOOL_ID`. If neither is
  set, the CLI resolves an existing tool by `--tool-type`.
- `--tool-type`: optional. `CodeEnv` or `SkillEnv`; defaults to `CodeEnv`.
  Used when resolving the current tool after `--tool-id` and
  `AGENTKIT_SANDBOX_TOOL_ID` are both absent.

Before returning, `get` calls `ListSessions` for the resolved tool and follows
`NextToken` until all pages are loaded. The returned remote sessions replace
the same tool's records in `.agentkit/sandbox/sessions.json`; records for other
tools are preserved. Sessions whose `UserSessionId` is empty are ignored because
they were not created through this CLI's session flow. When `--session-id` is
omitted and no existing tool can be resolved, the command skips remote sync and
returns the current local store, or `{}` if the store does not exist.

When `--session-id` is provided but the session is not found after sync, the
command exits with status `1` and returns structured JSON:

```json
{
  "tool_id": "t-example",
  "session_id": "123456789",
  "error_msg": "Sandbox session not found: 123456789"
}
```

### Shell

Execute a command in a sandbox shell.

```bash
agentkit sandbox shell \
  --session-id 123456789 \
  --command 'echo $TEST_VAR' \
  --shell-id shell-example
```

Options:

- `--session-id`: optional. Sandbox session ID used as the local session key.
  If omitted, a UUID is generated and the command creates a sandbox session
  through the same idempotent session ensure flow as `sandbox exec`.
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
agentkit sandbox exec --session-id 123456789
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
  value as `OPENCODE_API_KEY`, `CODEX_API_KEY`, and `ANTHROPIC_AUTH_TOKEN`. If
  omitted, the CLI uses `MODEL_API_KEY` when that environment variable is set.

The command connects to `<endpoint>/v1/shell/ws`, streams remote output to local
stdout, forwards local stdin as terminal input, sends terminal resize events, and
responds to WebSocket `ping` messages with `pong`.

When the remote terminal returns a shell session ID, the CLI prints it and
stores it in the `terminal_shell_id` list in `.agentkit/sandbox/sessions.json`
while the connection is active. Multiple live WebSocket connections under the
same sandbox `session_id` are tracked in the same list. The CLI removes only the
current shell ID from the list when that connection is detached or closed.

Press `Ctrl-]`, or type `exit` / `exit()`, to detach from the local terminal.
`Ctrl-C` is forwarded to the remote process, which is useful for interrupting
Codex or shell commands without closing the local WebSocket client.

## Local Store

`agentkit sandbox exec` writes session results to:

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
    "endpoint": "https://example.com/?Authorization=...",
    "terminal_shell_id": ["shell-example"]
  }
}
```

Repeated exec opens with the same `session_id` refresh the previous
entry when the remote session is reachable, or overwrite it after recreating the
remote session.

When `--tool-id` and `AGENTKIT_SANDBOX_TOOL_ID` are both omitted,
`sandbox exec` and `sandbox shell` resolve one tool per type through:

1. `.agentkit/sandbox/tools.json`
2. `ListTools` filtered by `ToolType`
3. automatic `agentkit sandbox create --tool-type <type>`-equivalent creation

Cached and listed tools are reused only when their status is `Ready`; tools in
states such as `Creating`, `Error`, `Deleting`, or `Deleted` are ignored.

Resolved tool records are stored in:

```text
.agentkit/sandbox/tools.json
```

Example:

```json
{
  "CodeEnv": {
    "ToolId": "t-code-example",
    "Name": "agentkit-codeenv-example",
    "Status": "Ready",
    "ToolType": "CodeEnv"
  },
  "SkillEnv": {
    "ToolId": "t-skill-example",
    "Name": "agentkit-skillenv-example",
    "Status": "Ready",
    "ToolType": "SkillEnv"
  }
}
```

## Module Layout

- `cli.py`: registers the `create`, `get`, `exec`, and `shell` sandbox subcommands.
- `../cli.py`: registers the `sandbox` command group.
- `session_create.py`: shared session creation and idempotent ensure helpers.
- `session_sync.py`: shared remote session list/sync helpers.
- `tool_resolve.py`: shared sandbox tool resolution and local tool cache helpers.
- `cli_create.py`: create command implementation.
- `cli_get.py`: get command implementation.
- `cli_shell.py`: shell command implementation.
- `cli_exec.py`: streaming exec command implementation.
- `utils.py`: shared store, URL, JSON, and error helpers.
