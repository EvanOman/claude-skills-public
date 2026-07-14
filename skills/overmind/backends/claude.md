# Backend: claude — in-harness subagents via the Agent tool

No wrapper script — the Agent tool is native. Workers draw the Max-plan included
budget. Unique strengths: workers get harness tools (MCP servers, skills, Artifact),
run inside this session's permission envelope, and the harness notifies you when
background agents finish (no polling).

## Model tiers — always pass `model` explicitly

**A subagent that omits `model` inherits the session model. From a Fable main loop
that silently bills premium rates — never allowed.** Pick per the delegation rules:

| `model` | Use for |
|---|---|
| `haiku` | Mechanical/boilerplate: renames, config changes, test runs, scripted transforms, repo sweeps |
| `sonnet` | Routine, well-specified implementation |
| `opus` | Hard reasoning inside the worker (rare — usually that work belongs in your loop) |

## Mechanics

- **Brief = the `prompt` param.** Same GOAL/CONTEXT/CONSTRAINTS/DONE WHEN/VERIFY
  format; the agent doesn't see this conversation.
- **Agent types**: `general-purpose` for implementation; `Explore` for read-only
  fan-out searches ("what's the conclusion", not file dumps); `Plan` for design
  studies; plus the specialized reviewer types in the registry.
- **Parallel fan-out**: multiple Agent calls in a single message run concurrently.
- **Continuation** (= session resume): `SendMessage` to a previously spawned agent's
  ID/name continues it with its context intact. A new Agent call starts fresh.
- **Isolation**: pass `isolation: "worktree"` to any file-modifying agent when other
  agents (or you) touch the same repo — auto-cleaned if unchanged.
- **Background**: agents run in the background by default; `run_in_background: false`
  only when you need the result before your next decision.

## Workflow (large deterministic fan-outs)

For N-items × same-transform pipelines, find→verify loops, or judge panels, the
Workflow tool scripts the fan-out deterministically (`pipeline()`, `parallel()`,
schema-validated agent outputs). **Requires explicit user opt-in** ("use a workflow",
"ultracode") — for everything else, stick to Agent calls. Inside a Workflow authored
from a Fable session, every `agent()` call must set an explicit non-Fable `model`.
