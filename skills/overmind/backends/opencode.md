# Backend: opencode — GLM-5.2 via opencode CLI

Wrapper: `~/.claude/skills/overmind/bin/opencode-worker.sh`. Metered API
(~$1.4/$4.4 per Mtok on GLM-5.2) — cheap but not free; check `stats`. One-time setup
(z.ai key, provider config) is documented in `../reference/opencode-setup.md` — read it
if `run` errors about a missing key.

## Verbs

```bash
W=~/.claude/skills/overmind/bin/opencode-worker.sh
export WORKER_DIR=/path/to/project      # the project the worker edits — always set

"$W" run  "<brief>"                # fresh worker, clean context → prints SESSION=<id>
"$W" cont <SESSION> "<brief>"      # continue with accumulated context
"$W" fork <SESSION> "<brief>"      # branch a known-good session to explore a risky change
"$W" last "<brief>"                # continue the most recent session
"$W" list                          # live sessions (id + title)
"$W" stats                         # token usage + cost so far
"$W" kill <SESSION>                # delete a session
```

## Env knobs

| Var | Default | Meaning |
|---|---|---|
| `WORKER_MODEL` | `glm/glm-5.2` | `glm/glm-5-turbo` for trivial mechanical tasks |
| `WORKER_DIR` | `$PWD` | Directory the worker is confined to |
| `WORKER_EFFORT` | unset | Reasoning effort: minimal…max |
| `WORKER_ATTACH` | unset | `http://localhost:PORT` of a `serve` backend (warm MCP reuse) |

## Notes

- The worker runs with `--dangerously-skip-permissions` scoped to that invocation only
  (headless opencode otherwise blocks on approval prompts); interactive opencode config
  is untouched. It is confined to `WORKER_DIR` — always point it at a project dir.
- `fork` is unique to this backend — use it to try approach B without polluting the
  session that did approach A.
- When launching parallel workers, capture `SESSION=` from each run's own output; the
  "newest session" heuristic is unreliable under concurrency.
- The pattern is model-agnostic: add any provider to `~/.config/opencode/opencode.json`
  and set `WORKER_MODEL=provider/model` to swap the worker's identity.

## Parallel dispatch (learned the hard way)

Dispatch N parallel workers as **N separate harness-tracked background Bash calls**
(one `run` each), never as one background call that forks N `"$W" run ... &` children
with `wait`. The nested form's children **hang silently instead of running** — 0-byte
`/tmp/worker.*.log` files, no sessions registered, target repos untouched — and the
processes stay alive for hours (observed: 24h) quietly holding opencode state that can
block later workers. Before re-dispatching after any suspected dispatch failure,
**hunt and kill the orphans**:

```bash
ps -eo pid,etime,args | grep 'opencode run' | grep -v grep   # old etime = zombie
kill <pids>    # your own hung workers — safe to kill by pid
```

Then re-dispatch; idempotent briefs make that safe. Three or so concurrent workers
(separately dispatched) are proven fine.
