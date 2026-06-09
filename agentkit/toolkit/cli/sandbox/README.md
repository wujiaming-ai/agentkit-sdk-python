# AgentKit Sandbox CLI

The sandbox CLI provides helper commands for creating and reusing AgentKit tool
sandbox sessions.

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
agentkit sandbox --help
python3 -m pip show agentkit-sdk-python
```

## Commands

### Create

Create a sandbox session through `CreateSession` and persist the result locally.

```bash
agentkit sandbox create \
  --user-session-id 123456789 \
  --ttl 28800 \
  --tool-id t-example
```

Options:

- `--user-session-id`: optional. Defaults to a generated UUID.
- `--ttl`: optional. Defaults to `AGENTKIT_SANDBOX_TTL`, then `28800`.
- `--tool-id`: optional. Defaults to `AGENTKIT_SANDBOX_TOOL_ID`. If neither is
  set, the command fails.

Output:

```json
{
  "user_session_id": "123456789",
  "tool_id": "t-example",
  "session_id": "s-example",
  "endpoint": "https://example.com/?Authorization=..."
}
```

### Get

Read a created sandbox session from the local session store.

```bash
agentkit sandbox get --user-session-id 123456789
```

Options:

- `--user-session-id`: required. User session ID to look up.

### Exec

Execute a command in a sandbox shell.

```bash
agentkit sandbox exec \
  --user-session-id 123456789 \
  --command 'echo $TEST_VAR' \
  --shell-id shell-example
```

Options:

- `--user-session-id`: required. Used to look up the stored endpoint.
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

### Terminal

Open a streaming WebSocket terminal to the sandbox. By default, this connects
without running an initial command.

```bash
agentkit sandbox terminal --user-session-id 123456789
```

Options:

- `--user-session-id`: required. Used to look up the stored endpoint.
- `--command`: optional. Initial command to run after the terminal is ready.
  Omit this option to connect without running an initial command. Use
  `--command codex` to start the remote Codex TUI.
- `--shell-id`: optional. Existing shell terminal ID to connect to. When this is
  set and `--command` is omitted, no initial command is sent.

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

`agentkit sandbox create` writes session results to:

```text
.agentkit/sandbox/sessions.json
```

The file is a JSON object keyed by `user_session_id`:

```json
{
  "123456789": {
    "user_session_id": "123456789",
    "tool_id": "t-example",
    "session_id": "s-example",
    "endpoint": "https://example.com/?Authorization=..."
  }
}
```

Repeated creates with the same `user_session_id` overwrite the previous entry.

## Module Layout

- `cli.py`: registers the sandbox Typer app and subcommands.
- `sandbox_create.py`: create command implementation.
- `sandbox_get.py`: get command implementation.
- `sandbox_exec.py`: exec command implementation.
- `sandbox_terminal.py`: streaming terminal command implementation.
- `utils.py`: shared store, URL, JSON, and error helpers.
