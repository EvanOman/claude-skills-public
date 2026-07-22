---
name: overmind-v2
description: Orchestrate persistent, cross-harness subagents through a shared local task broker with grouped fan-out, event-driven waiting, bounded result collection, continuation, interruption, recovery, and usage evidence. Use when the user explicitly asks for Overmind v2, coordinated parallel work, a Claude-to-Codex or Codex-to-Claude worker, background agents that must survive the parent session, a subagent bake-off, or reliable status tracking without model-driven polling.
---

# Overmind v2

Operate as the mission controller. Infer the outcome from the conversation, keep synthesis and final
judgment in the parent, and delegate bounded work through the Overmind v2 broker. Prefer native
subscription-backed workers; never select a metered backend unless the user explicitly requests it.

## Start with capabilities

Resolve the directory containing this `SKILL.md` to an absolute `SKILL_ROOT`; commands below are
relative to that directory, not the user's current working directory. Run
`$SKILL_ROOT/scripts/om doctor --json` before the first cross-harness fan-out in a session. Use the
returned provider and billing facts instead of assuming that a harness, model, or live-steering
feature is available. Read [references/setup.md](references/setup.md) when installing the broker in
Claude and Codex. Read [references/protocol.md](references/protocol.md) when debugging lifecycle
behavior or using advanced filters. Read [references/testing.md](references/testing.md) for a
deterministic bake-off or broker regression work.

## Orchestrate a mission

1. Decompose the outcome into independently verifiable briefs with one owner per artifact.
2. Record the current checkout state before dispatching repository work. Isolate concurrent writers
   in separate worktrees.
3. Launch a group in one operation with `run-many`; use an idempotency key when retrying a request.
4. Continue useful control-plane work only while capacity remains. Never poll `jobs` in a reasoning
   loop.
5. Call `await` once with `all_terminal`, or `any_terminal` when later work depends on the first
   result. Resume an interrupted wait with its returned event cursor.
6. Call `collect` for bounded previews. Read full result artifacts only for workers whose details are
   needed.
7. Inspect produced artifacts and run the brief's named verification before synthesis.
8. Use `reply` for a stateful correction. Use `stop` for obsolete work and `forget` only when the
   lifecycle record is no longer useful.

Write each brief as:

```text
GOAL:        One observable outcome.
CONTEXT:     Relevant paths, facts, and dependencies.
CONSTRAINTS: Scope, invariants, billing class, and forbidden changes.
DONE WHEN:   Acceptance criteria visible outside the worker's narrative.
VERIFY:      Exact commands or checks.
```

## Keep context and spend bounded

- Prefer `run-many -> await -> collect` over one launch, wait, and result cycle per worker.
- Ask `jobs` for active work in the current group, not global history.
- Keep result previews small; use artifact paths for full output.
- Treat subscription quota and token counters as usage evidence, not dollar invoices.
- Reject silent provider fallback across billing classes.
- Treat broker jobs as execution state, never as durable user to-dos.

## Handle completion and recovery

Trust normalized terminal states from the broker: `succeeded`, `failed`, `interrupted`, and
`unknown`. A worker summary alone is not proof. If the parent or broker restarts, query the existing
group and resume from its event cursor; do not relaunch without the same idempotency key. When a
provider cannot be observed, preserve the job as `unknown` rather than inventing failure.

## Use the command surface

Use `$SKILL_ROOT/scripts/om --help` for the human CLI and `$SKILL_ROOT/scripts/overmind-v2-mcp` for
the MCP stdio server. Canonical operations are `run`, `run-many`, `jobs`, `show`, `await`, `collect`,
`reply`, `stop`, `forget`, and `doctor`. Human aliases are accepted, but do not teach duplicate MCP
tool names.

Use v1 only as the control during migration or when v2's doctor reports an unavailable required
capability. Do not modify or migrate v1 state implicitly.
