# Backend: Codex

Use this backend when the requested worker is Codex. Pick the surface from the orchestrator:

1. **From Codex:** use native delegation.
2. **From Claude Code:** prefer the configured Overmind lifecycle MCP bridge.
3. **Single synchronous turn:** use the official Codex MCP bridge when it is configured.
4. **Fallback:** use `bin/codex-worker.sh` as one harness-tracked command per worker.

The MCP bridge and CLI wrapper create Codex threads, but they do not register those threads as
provider-native Claude Agent entries. Keep their lifecycle identifiers separately.

For the common lifecycle bridge, call `overmind_spawn` with `provider: "codex"`, the complete brief,
absolute working directory, and label. Keep the returned Overmind job ID and use `overmind_status`,
`overmind_wait`, `overmind_result`, `overmind_followup`, `overmind_interrupt`, and
`overmind_cleanup`. The provider's Codex thread ID remains visible in the job record. Continuation
starts only after the current turn is terminal; live steering and thread fork remain native-Codex
capabilities.

## Codex to Codex: native delegation

Use the collaboration operations exposed by the harness:

- spawn a worker for one bounded, independently useful brief;
- list workers to inspect concurrency and current state;
- send information to a running worker without restarting its task;
- trigger a follow-up turn when the same worker should retain context;
- wait for completion without polling aggressively;
- interrupt work that is obsolete, unsafe, or outside its brief.

Current Codex surfaces may expose these as `spawn_agent`, `list_agents`, `send_message`,
`followup_task`, `wait_agent`, and `interrupt_agent`. Follow the active surface's schemas and
concurrency limit rather than assuming a fixed worker count.

Give each spawned worker a concrete task name, the smallest sufficient conversation fork, an
explicit work directory, and the full brief. Native workers share the filesystem, so use separate
worktrees for concurrent edits and remember that a worker's changes become visible immediately.

## Claude Code to Codex: official MCP bridge

Configure the installed Codex CLI once at user scope, then verify the connection:

```bash
claude mcp add --scope user codex -- codex mcp-server
claude mcp get codex
```

Claude Code exposes the server's deferred tools as `mcp__codex__codex` and
`mcp__codex__codex-reply` (the displayed prefix can vary if the server is renamed).

- Start with `mcp__codex__codex`: pass the complete brief as `prompt`, an absolute `cwd`, and the
  narrowest useful `sandbox`. Set `approval-policy` deliberately for unattended work; an action
  needing an unavailable approval fails rather than becoming silently approved.
- Save `structuredContent.threadId` and inspect the returned `content` as the turn result.
- Continue with `mcp__codex__codex-reply`, passing that `threadId` and the follow-up `prompt`.
- Verify produced files in the orchestrator after each call.

At the Codex server boundary this is a synchronous tool call, not an MCP Task. Current Claude Code
can move a long-running MCP call into its background-task UI and notify on completion, but the Codex
`threadId` is still unavailable until the call returns and the bridge has no independent list, wait,
or cancel API for that turn. Parallelism, if the active Claude surface permits parallel tool calls,
is owned by Claude's tool scheduler—not by shell backgrounding.

## Codex CLI fallback

The bundled wrapper creates standalone `codex exec` sessions and records their final messages and
event logs. Resolve it relative to this skill's directory:

```bash
W="<skill-root>/bin/codex-worker.sh"

printf '%s\n' "GOAL: ..." "CONTEXT: ..." "CONSTRAINTS: ..." "DONE WHEN: ..." "VERIFY: ..." \
  | "$W" run -C /path/to/project --label add-json-flag -

"$W" cont <SESSION> "follow-up prompt"
"$W" last <SESSION>
"$W" log <SESSION>
"$W" list
```

Run independent CLI workers concurrently through the active harness's background-process support.
Do not build an untracked shell fan-out that hides worker state.

### Safe background recipe from Claude Code

Make **one Bash tool call per worker**. The wrapper command must remain foreground inside that call;
set `run_in_background: true` on the Bash tool itself:

```text
Bash(
  command: "printf '%s\\n' 'GOAL: ...' 'CONTEXT: ...' 'CONSTRAINTS: ...' \\
            'DONE WHEN: ...' 'VERIFY: ...' | \\
            '<skill-root>/bin/codex-worker.sh' run -C /absolute/project \\
            --label add-json-flag -",
  run_in_background: true
)
```

Do not append `&`, `nohup`, or `disown`, and do not put several wrapper calls under one Bash task
with shell `wait`. That creates a second, detached background layer and prevents reliable completion
notification—the failure mode that produced untracked workers.

Save the Bash task ID. Claude Code automatically notifies on completion; use its background-output
control (`BashOutput` or `TaskOutput`, depending on the active surface) to inspect output and
`KillShell` or the displayed stop control to cancel. A successful wrapper result prints
`SESSION=<id>`; save that separately for Codex continuation and logs.

Run each `cont` as another foreground wrapper command, or as its own harness-managed background Bash
call if it is long-running. Never add a shell background operator.

### `run` options

| Flag | Meaning |
|---|---|
| `-C dir` | Worker directory. Set it explicitly. |
| `-p profile` | Optional locally configured Codex profile. Omit to inherit the current CLI default. |
| `-m model` | Optional explicit model override requested for this run. Do not hard-code a version in the skill. |
| `-s sandbox` | Optional sandbox override. Prefer the default permission boundary. |
| `--full-access` | Disable the sandbox only when the user has authorized that broader access. |
| `--schema file.json` | Require a schema-shaped final response. |
| `--label name` | Human-readable registry label. |
| `-- <args>` | Additional arguments passed to `codex exec`. |

The CLI fallback uses the active Codex login; verify locally whether that login is subscription- or
API-backed before making billing claims. Inspect the JSONL log when the summary is insufficient.
Run final verification outside the worker as required by the common workflow.
