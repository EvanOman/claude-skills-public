# Backend: Claude

Use this backend when the requested worker is Claude. Pick the surface from the orchestrator:

1. **From Claude Code:** use its native Agent registry.
2. **From Codex:** prefer the configured Overmind lifecycle MCP bridge; otherwise launch Claude
   Code's daemon-backed background-agent CLI with `claude --bg`.

A daemon-backed CLI session is a first-class entry in `claude agents`, but it does not become a
native Codex agent-registry entry. Codex must keep the returned Claude ID and manage that lifecycle.

## Claude Code to Claude: native Agent registry

No wrapper script is needed. Workers draw the configured Claude subscription budget. They receive
Claude harness tools (including configured MCP servers and skills), run inside the session's
permission envelope, and notify the parent when background work finishes.

### Model tiers — always pass `model` explicitly

**A subagent that omits `model` inherits the session model. From a Fable main loop that silently
bills premium rates—never allowed.** Pick per the delegation rules:

| `model` | Use for |
|---|---|
| `haiku` | Mechanical/boilerplate: renames, config changes, test runs, scripted transforms, repo sweeps |
| `sonnet` | Routine, well-specified implementation |
| `opus` | Hard reasoning inside the worker (rare—usually that work belongs in your loop) |

### Mechanics

- **Brief = the `prompt` param.** Use the same GOAL/CONTEXT/CONSTRAINTS/DONE WHEN/VERIFY format; the
  agent does not see this conversation.
- **Agent types:** `general-purpose` for implementation; `Explore` for read-only fan-out searches
  ("what is the conclusion", not file dumps); `Plan` for design studies; plus specialized reviewer
  types in the registry.
- **Parallel fan-out:** multiple Agent calls in a single message run concurrently.
- **Continuation:** `SendMessage` to a previously spawned agent's ID/name continues it with context
  intact. A new Agent call starts fresh.
- **Isolation:** pass `isolation: "worktree"` to any file-modifying agent when other agents (or you)
  touch the same repository; it is auto-cleaned if unchanged.
- **Background:** agents run in the background by default; use `run_in_background: false` only when
  the result is required before the next decision.

### Workflow (large deterministic fan-outs)

For N-items × same-transform pipelines, find→verify loops, or judge panels, the Workflow tool
scripts the fan-out deterministically (`pipeline()`, `parallel()`, schema-validated agent outputs).
**This requires explicit user opt-in** ("use a workflow", "ultracode"); otherwise use Agent calls.
Inside a Workflow authored from a Fable session, every `agent()` call must set an explicit non-Fable
`model`.

## Codex to Claude: daemon-backed background agents

Use this path for an actual Claude worker. Do not substitute a native Codex subagent merely because
Codex has a better-integrated registry; that changes the requested provider.

When the Overmind MCP server is available, call `overmind_spawn` with `provider: "claude"`, an
explicit model, the complete brief, absolute working directory, and label. It refuses dispatch
unless the sanitized Claude CLI reports a logged-in `claude.ai` subscription. Keep the returned
Overmind job ID and use the common lifecycle tools for status, waiting, results, follow-up,
interruption, and cleanup. The provider's Claude daemon ID remains visible in the job record.

Use the bundled wrapper so one foreground Codex exec call owns dispatch, waiting, result delivery,
and signal-safe cancellation when the common MCP bridge is unavailable. Resolve it relative to this
skill's directory:

```bash
W="<skill-root>/bin/claude-worker.sh"

printf '%s\n' 'GOAL: ...' 'CONTEXT: ...' 'CONSTRAINTS: ...' \
  'DONE WHEN: ...' 'VERIFY: ...' \
  | "$W" run --wait --subscription -C /absolute/project \
      -m sonnet --name add-json-flag -
```

Keep the wrapper in the foreground of the Codex exec call. `run --wait` starts `claude --bg`, prints
`JOB=<ID>` immediately, waits on Claude's structured job state, prints the result, and returns a
distinct exit code for success, failure, or cancellation. If the exec call yields a harness session
ID while it waits, continue waiting through that same exec session. Do not append `&`, use
`nohup`/`disown`, or add another background layer.

The wrapper exposes Claude's registry as a scriptable lifecycle:

```bash
"$W" list
"$W" status <ID>
"$W" last <ID>
"$W" logs <ID>
"$W" wait <ID>
"$W" stop <ID>
"$W" rm <ID>

printf '%s\n' 'Follow-up instructions' \
  | "$W" cont --wait --subscription <ID> -
```

Continuation preserves the conversation but Claude assigns the new turn a new short job ID; save the
new `JOB=<ID>` returned by `cont`. Always inspect the actual diff plus the wrapper result after state
becomes `done`. `rm` is cleanup, not cancellation, and is destructive.

The wrapper requires an explicit model and defaults the permission mode to `dontAsk`. Its
`--subscription` option removes `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, and
`ANTHROPIC_BASE_URL` only from the spawned Claude launch, allowing the local subscription login to
win without printing secrets. Omit that option only when the user explicitly wants the configured
API/provider path. Underneath, `claude --bg` and `--print` conflict, and Claude must be launched from
the target checkout because it has no `-C` flag; the wrapper handles both details.
