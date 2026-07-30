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

`run`, `run_many`, and `reply` accept the Claude-specific, per-job options below. All are ignored by
non-Claude providers. Set them on an individual job, or at the request's top level as a default for
jobs that omit them; `reply` inherits the parent job's values unless the continuation overrides them.

- `permission_mode` (default `bypassPermissions`): the Claude CLI permission mode for the worker
  session. The broker previously defaulted background workers to `dontAsk`, which auto-denies tool
  calls with no TTY to answer a prompt; a denied worker parks itself in the CLI's `blocked` state and
  never progresses on its own, which every recovery required an explicit `stop`. `bypassPermissions`
  lets a worker act on its brief and reach a terminal state unattended. Pass `acceptEdits`, `auto`,
  `dontAsk`, `manual`, or `plan` to opt back into a narrower mode for a specific job. When the mode
  is `bypassPermissions` the launch also passes `--allow-dangerously-skip-permissions`, because
  selecting the mode is not by itself enough for the CLI to allow it; without that flag the request
  can be refused and the worker silently falls back to prompting.

- `strict_mcp_config` (default `true`) and `mcp_config` (default none): a worker is launched with
  `--strict-mcp-config`, so it sees only the MCP servers named in its own `mcp_config` and none of
  the operator's user- or project-scope configuration. A background worker cannot answer a server
  approval prompt: three measured jobs reached a terminal state in under five seconds with a 64-byte
  artifact reading "approve 1 new project MCP server (grafana) — attach to respond", because the
  assigned worktree happened to contain a `.mcp.json`. Pass `mcp_config` (a path, inline JSON, or a
  list) to hand a worker exactly the servers its brief needs. Set `strict_mcp_config: false` only
  when a worker is meant to inherit everything the operator has configured, and accept that an
  unapproved server will stall it.

- `min_result_bytes` (default `300`): the smallest result artifact reported as `succeeded` rather
  than `unknown`. Measured over 188 broker-launched Claude jobs, the median `succeeded` artifact was
  138 bytes of CLI progress note and only 4.8% carried a real report; Codex's median over the same
  window was 1,929 bytes. A `succeeded` job is never reconciled again and the orchestrator is told to
  trust it, so a success with no readable work product is the most expensive misreport available: the
  brief looks done, nothing verifies it, and the same work is dispatched again later. Reporting it
  `unknown` says what is actually true. Set `0` for a job whose deliverable really is a one-word
  verdict.
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

  Telling the worker is not enough on its own, because the CLI enforces the opposite. A background
  session's first `Write` is refused by a workspace guard — "This background session hasn't isolated
  its changes yet. Call EnterWorktree first" — and `bypassPermissions` does not cover it, because it
  is workspace policy rather than a permission prompt. The measured cost: a worker finished an
  85,000-token audit, was refused the write, and sat blocked for 39 hours on "Approve Write for
  experiments/… , or use EnterWorktree, or take summary as-is". The launch therefore also passes
  `--settings '{"worktree": {"bgIsolation": "none"}}'`. `workspace_note: false` leaves the guard on,
  since that is how a caller says the worker is responsible for isolating itself.

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

- `idle_hard_timeout_seconds` (default `3600`): ends a worker whose state file has stopped changing
  for this long *even while the CLI reports a task in flight*. A worker can sit at
  `inFlight: {tasks: 1}` permanently, because the counter is not always cleared when the underlying
  process exits, so a finished or wedged worker looks busy forever and the quiescence reaper above
  never touches it. This ceiling was originally opt-in, on the argument that `updatedAt` does not
  advance during a tool call and a thirty-minute test suite is indistinguishable from a hang. The
  measured consequence of that argument was that not one of 188 jobs ever set it, and a wedged worker
  held a non-terminal record for 39 hours. An hour is a bound on *silence*, not on runtime: the CLI
  rewrites the state file on every message and tool result, so this cannot fire while a worker is
  producing anything at all. Raise it for a job with a single legitimately silent step longer than an
  hour; `0` disables it. `OVERMIND_V2_CLAUDE_HARD_TIMEOUT_SECONDS` sets it globally.

Do not be tempted to reap on `tempo: "idle"` plus staleness without the in-flight guard. Measured
against a real parent worker waiting on a subagent: the parent reported `tempo: "idle"` from the
moment the subagent started, with `updatedAt` frozen for over three minutes, while `inFlight` held
`local_agent` and `local_bash`. Without the in-flight check that parent would have been killed
mid-flight, taking its subagent's work with it.

## Session ownership and orphaned workers

Each job records the orchestrator session that launched it, in `owner_session`. Nothing
needs to pass it: the MCP server is a child of the session process, so it walks its own
process ancestry and matches a live entry in the session registry under
`<state-dir>/sessions/`. Set `owner_session` on a job to attribute it deliberately, or
`OVERMIND_V2_OWNER_SESSION` to force one.

A session registers itself while it is alive. The Claude CLI hands the session id to the
status line and nowhere else -- it is absent from the MCP server's environment and from a
worker's own state file -- so the status line is what binds session id to a live process.
Liveness is pid *plus* process start identity, never pid alone, because pids are recycled
and claiming a stranger's process is the one mistake this must not make.

`om orphans` lists running workers whose owning session is gone; `om orphans --stop` ends
them. This is deliberately a command rather than an automatic sweep. Surviving the parent
session is a documented capability of this broker, so a worker outliving its launcher is a
legitimate state and not by itself a leak; a sweep that killed those would quietly destroy
work someone meant to keep. Run it when you want the cleanup, or wire it to a session-end
hook if you want it every time.

### Unrecognized states are transitions, not outcomes

A CLI state the adapter does not map is treated as still-running for
`UNRECOGNIZED_STATE_GRACE_SECONDS` (60) and only settles to `unknown` once the state file
stops changing **and** the job itself is older than the grace. This exists because of an
observed misreport: a worker took a SIGTERM, reported `SIGTERM (143); respawning`, was
recorded terminal as `unknown` -- and then respawned and committed its work correctly. A
terminal job is never reconciled again, so the broker permanently reported a successful
worker as failed and discarded its result. `respawning` and `restarting` are now mapped to
running outright; the grace covers whatever else the CLI may emit mid-transition. Genuine
`failed`/`stopped` states are still reported immediately, since they are mapped explicitly.

Staleness alone was not enough. Eleven measured launches were declared terminal `unknown`
with that same `SIGTERM (143); respawning` detail between five and twelve seconds after
launch, on the *first* reconcile — the state file already carried an `updatedAt` older than
the grace, so the staleness test passed instantly. The CLI hands a background session a
pre-spawned host process (`claude bg-spare`, visible in `ps`), and the timestamp the adapter
reads at that moment predates the job. One of those eleven workers was verified still
running, correctly, an hour after the broker had written it off. A job younger than the
grace therefore cannot settle to `unknown`, whatever its state file claims.

The provider's unnormalized state string is now recorded on every state-change event as
`provider_raw_state`, because that is the one fact needed to diagnose a bad `unknown` and it
was not recoverable from the surrounding detail text.

### `database is locked` is contention, not a dead worker

Six measured jobs were reported permanently unobservable with
`provider observation failed: database is locked`. Each per-job watcher writes on every
observation, so a fan-out means many short write transactions competing for SQLite's single
writer lock; `busy_timeout` covers most of that, but SQLite returns `SQLITE_BUSY` without
consulting the busy handler when a transaction cannot start against a snapshot that has
moved. Two changes: the store raises `busy_timeout` to 30s and retries a busy
`BEGIN`/`COMMIT` for up to 30s, and a watcher no longer declares a job unobservable on the
first failure — it backs off and retries, and gives up only after
`OBSERVATION_FAILURE_LIMIT` (5) consecutive failures. Declaring a job terminal is
irreversible, so it should never be the response to one transient error.

A related launch detail: `--model` is passed only when a job specifies one, so workers inherit the
operator's configured default instead of a tier hardcoded in the adapter.

CLI equivalents: `om run --permission-mode <mode>`, `om run --no-isolate-worker-config`,
`om run --no-workspace-note`, `om run --mcp-config <path>`, `om run --no-strict-mcp-config`, and
`om run --min-result-bytes <n>` (the permission and isolation flags are also available on
`om reply`). `run-many` and MCP callers set the same field names directly in the job object or
request.

## What a Claude result artifact contains

The artifact is the worker's **final assistant message**, read from its own session transcript
(`linkScanPath` in the CLI's job state, or `~/.claude/projects/*/<sessionId>.jsonl`). Sidechain
records are subagent turns, not the worker's conclusion, and are skipped.

This is not the same thing as `output.result` in the CLI's job state, which is a one-line headline
the CLI keeps for its job list — "hello.txt contains OK" for a job whose real report ran to several
paragraphs. Recording that headline as the result is what produced a 138-byte median result for
Claude workers against 1,929 for Codex, which has always captured its last agent message. The
headline is still the fallback when no transcript is readable, followed by the CLI's `needs`/`detail`
note for a blocked or reaped worker; both fallbacks are usually short enough to trip
`min_result_bytes` and be reported `unknown`, which is the honest answer when the worker itself never
said anything.

Finalizing waits for the transcript to go quiet, because the CLI flushes it *after* marking its
state file terminal. Measured: a report recorded at :41.5 with a terminal marker at :43.4 was still
absent from the file when the broker read at :44, so the artifact captured was the worker's opening
line. A terminal job is never reconciled again, so that loss is permanent — hence
`CLAUDE_TRANSCRIPT_QUIET_SECONDS` (2) of no writes before finalizing, and
`CLAUDE_FINALIZE_CEILING_SECONDS` (30) after which the job settles regardless, so a transcript that
never quiets cannot hold a finished job open.

## Claude stall/blocked-turn reconciliation

The Claude CLI's background-job state (`~/.claude/jobs/<id>/state.json`) can report `state: "blocked"`
when a turn has genuinely ended and the CLI is waiting synchronously for operator input (a permission
denial, or a real clarifying question). `blocked` never self-transitions to `done`; treating it as
non-terminal is what previously left jobs showing `running` forever with a frozen `updated_at`, and
made `reply` fail with a "still running" conflict since the provider has no live-steering support.
`ClaudeProvider.reconcile` now maps `blocked` to `succeeded` and captures the CLI's own `needs` (or
`detail`) text as the result artifact when there is no structured `output.result`, so the parent can
judge the content and `reply` can create a continuation immediately.
