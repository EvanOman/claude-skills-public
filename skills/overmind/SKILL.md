---
name: overmind
description: Enter a persistent orchestration mode that decomposes the current mission, delegates bounded work to isolated workers, and verifies their results. Use when the user explicitly asks for overmind mode, orchestration, fan-out, delegation, workers or minions, tech-lead mode, or coordinated parallel work; re-invoke when orchestration discipline has drifted.
---

# Overmind

Enter a mode, not a one-shot command. Infer the mission from the conversation, state the objective
and decomposition briefly, then direct workers until the user ends the mode or the mission is done.
New implementation work that arrives during the mode is also orchestrated.

Keep judgment in the main loop: decomposition, architecture, brief-writing, integration, and review.
Delegate bounded execution that a worker can complete and verify independently. Work inline when the
task depends heavily on conversational context, requires cross-cutting judgment, or is cheaper than
writing a precise brief.

## Select a backend

Read only the selected backend reference before first dispatch:

- `backends/codex.md` — Codex workers: native Codex delegation inside Codex, the official Codex MCP
  bridge inside Claude, then a Codex CLI fallback.
- `backends/claude.md` — Claude workers: native Claude subagents inside Claude and Claude's
  daemon-backed background-agent CLI from Codex.
- `backends/opencode.md` — metered external-model workers when the user requests them or native
  capacity is unavailable.

An explicit user choice wins. Otherwise prefer the active harness's native registry: Codex workers
in Codex, Claude workers in Claude. Crossing harnesses does not make the worker a native registry
entry in the parent harness. When the bundled Overmind lifecycle MCP server is configured, prefer
its common `spawn`, `list`, `status`, `wait`, `result`, `followup`, `interrupt`, and `cleanup`
operations for cross-harness work; otherwise use the selected backend's provider-specific bridge.
Read `mcp/README.md` for installation and capability details. Preserve provider and billing facts
inside backend references rather than assuming a fixed model version here.

When useful, run `bin/usage-check.sh` from this skill directory before a large fan-out. Treat its
output as advisory because quota snapshots can be stale. If the selected backend lacks headroom,
explain that and propose an alternative instead of switching silently.

## Write the brief as the interface

Workers receive the brief and their work directory, not the orchestrator's unstated reasoning. Make
every brief self-contained:

```text
GOAL:        The outcome in one sentence.
CONTEXT:     Relevant files, current behavior, and facts not obvious from the repository.
CONSTRAINTS: Boundaries, invariants, patterns, and forbidden changes.
DONE WHEN:   Observable acceptance criteria.
VERIFY:      Exact commands or checks that demonstrate completion.
```

If `DONE WHEN` and `VERIFY` cannot be concrete, decompose or investigate further before dispatch.
Batch related mechanical work when it shares context; do not create workers merely to avoid a small
amount of direct work.

## Partition safely

Delegate independent tasks in parallel and dependent tasks in sequence. Assign one clear owner per
output. When multiple workers may modify the same repository, give each an isolated worktree or an
equivalent isolated checkout. Never let workers race in a shared checkout.

Before dispatch into an existing checkout, record its status so pre-existing changes remain
distinguishable from worker output. Give workers the narrowest permissions and directory scope that
can complete the brief.

## Use exactly one background owner

For every dispatch, exactly one layer owns background execution. A native registry owns its agent;
the Overmind lifecycle bridge owns the provider job behind its durable Overmind ID; a synchronous
MCP bridge stays a harness-tracked tool call; `claude --bg` owns its daemon session; and a CLI wrapper
stays in the foreground of one harness-managed background command. Never add shell `&`, `nohup`,
`disown`, or a nested fan-out beneath one of those layers. Save the lifecycle identifier returned by
the owning layer and use that layer to inspect, continue, stop, and collect the result.

## Manage worker state

- Start fresh for independent work, parallel branches, or a confused/long-running context.
- Continue an existing worker when a follow-up depends on state it just created or on a specific
  debugging history.
- Prefer fresh, ephemeral workers by default. Persistence is for genuinely stateful chains.
- If the same task misses twice, stop retrying the worker. Improve the brief, change approach, or
  take the judgment-heavy portion inline.

Use backend-specific continuation, waiting, messaging, and interruption mechanics only as described
in the selected backend reference.

## Verify every result

After every handoff:

1. Inspect the actual diff or produced artifacts, not only the worker summary.
2. Run the verification yourself in the correct work directory.
3. Compare the result with every acceptance criterion and repository constraint.
4. Integrate only the parts that pass; preserve unrelated user changes.

Treat worker claims as evidence to check. If no diff is available, enumerate outputs and inspect the
underlying execution record supported by that backend.

## Maintain the mode

Context growth can weaken orchestration discipline. Re-invoke this skill when the main loop starts
doing delegable implementation or when the user asks to restore overmind behavior.
