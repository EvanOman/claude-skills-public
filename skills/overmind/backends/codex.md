# Backend: Codex

Prefer the active harness's native Codex delegation capability. It uses the current subscription and
native permission model without pinning a model in this skill. Use the CLI wrapper only when native
delegation is unavailable or when a scriptable standalone session is specifically useful.

## Native delegation

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
