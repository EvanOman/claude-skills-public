# Configure Overmind v2

Resolve this skill directory to an absolute, stable path. Register the same stdio launcher in both
harnesses:

```bash
claude mcp add --scope user overmind-v2 -- /absolute/path/to/skills/overmind-v2/scripts/overmind-v2-mcp
codex mcp add overmind-v2 -- /absolute/path/to/skills/overmind-v2/scripts/overmind-v2-mcp
```

The launcher starts or connects to the per-user broker automatically. Confirm the shared view from
both harnesses with the `doctor` tool or:

```bash
/absolute/path/to/skills/overmind-v2/scripts/om doctor --json
```

Use `OVERMIND_V2_STATE_DIR` only for tests, isolated development, or an intentional second broker.
Do not point v2 at v1's cache directory. V2 does not import, alter, or delete v1 lifecycle records.

To remove the integration, remove the `overmind-v2` MCP registration from each harness. Do not
delete the state directory while jobs are active. Lifecycle records and result artifacts remain
under `~/.local/state/overmind-v2/` until explicitly forgotten or archived.

## Claude worker launch options

`run`, `run_many`, and `reply` accept four Claude-specific, per-job options. All are ignored by
non-Claude providers. Set them on an individual job, or at the request's top level as a default for
jobs that omit them; `reply` inherits the parent job's values unless the continuation overrides them.

- `permission_mode` (default `bypassPermissions`): the Claude CLI permission mode for the worker
  session. The broker previously defaulted background workers to `dontAsk`, which auto-denies tool
  calls with no TTY to answer a prompt; a denied worker parks itself in the CLI's `blocked` state and
  never progresses on its own, which every recovery required an explicit `stop`. `bypassPermissions`
  lets a worker act on its brief and reach a terminal state unattended. Pass `acceptEdits`, `auto`,
  `dontAsk`, `manual`, or `plan` to opt back into a narrower mode for a specific job.
- `isolate_worker_config` (default `true`): launches the worker without the operator's user-level
  Claude settings, hooks, and plugins, so a SessionStart hook or a standing workflow skill (TDD
  ritual, worktree setup, brainstorming prompt, etc.) doesn't consume the worker's turn before it
  touches the brief. Implemented as `--setting-sources project,local` when the installed `claude` CLI
  supports that flag (checked once per broker process via `claude --help`); on an older CLI without
  it, the broker instead prepends a short standard preamble to the brief telling the worker to skip
  onboarding ceremony and execute the brief directly (see `CEREMONY_PREAMBLE` in `providers.py`). Set
  `isolate_worker_config: false` to let a job inherit the operator's full config instead.
- `workspace_note` (default `true`): when the worker's `cwd` is a git checkout, appends a short note
  telling it that it is already in the dedicated directory the orchestrator assigned and must not
  create another worktree. Without it, a write-capable worker tends to call `EnterWorktree`, which
  creates a nested `.claude/worktrees/<name>` checkout on its own branch; the work succeeds but is
  stranded where the orchestrator is not looking, and the assigned branch appears untouched. Set
  `workspace_note: false` when a worker is supposed to manage its own worktree.

- `idle_grace_seconds` (default `300`): how long a worker may sit with no turn in progress before the
  broker ends its session and reports it terminal. The Claude CLI parks a worker that completed its
  work without emitting a final message at `state: "working"` with `tempo: "idle"`, an empty
  `inFlight`, and a frozen `updatedAt`. That is not the `blocked` case above -- nothing is waiting on
  the operator, the turn simply ended silently -- and the broker would otherwise poll it forever, so
  `await` never satisfies and `reply` refuses to continue it. Observed on roughly a third of workers
  in a seven-worker run whose git state proved the work had finished. The reaped job is reported
  `unknown`, not `succeeded`: the broker has no report to judge, so it declines to claim success and
  the orchestrator should verify the brief's artifacts. The worker's last progress note is kept as
  the result artifact. Set a larger value for a job that legitimately idles, or `0` to disable.

A related launch detail: `--model` is passed only when a job specifies one, so workers inherit the
operator's configured default instead of a tier hardcoded in the adapter.

CLI equivalents: `om run --permission-mode <mode>`, `om run --no-isolate-worker-config`, and
`om run --no-workspace-note` (the first two are also available on `om reply`). `run-many` and MCP callers set the same field names directly in the job
object or request.

## Claude stall/blocked-turn reconciliation

The Claude CLI's background-job state (`~/.claude/jobs/<id>/state.json`) can report `state: "blocked"`
when a turn has genuinely ended and the CLI is waiting synchronously for operator input (a permission
denial, or a real clarifying question). `blocked` never self-transitions to `done`; treating it as
non-terminal is what previously left jobs showing `running` forever with a frozen `updated_at`, and
made `reply` fail with a "still running" conflict since the provider has no live-steering support.
`ClaudeProvider.reconcile` now maps `blocked` to `succeeded` and captures the CLI's own `needs` (or
`detail`) text as the result artifact when there is no structured `output.result`, so the parent can
judge the content and `reply` can create a continuation immediately.
