# Backend: codex — GPT-5.5 via Codex CLI

Wrapper: `~/.claude/skills/overmind/bin/codex-worker.sh`. Session logs and registry
in `~/.cache/codex-worker/`. Auth is ChatGPT OAuth — runs draw subscription quota, not
metered dollars.

## Verbs

```bash
W=~/.claude/skills/overmind/bin/codex-worker.sh

# Fresh worker. Brief via stdin ('-') is preferred for multi-line briefs.
printf '%s\n' "GOAL: ..." "CONTEXT: ..." "CONSTRAINTS: ..." "DONE WHEN: ..." "VERIFY: ..." \
  | "$W" run -C /path/to/project --label add-json-flag -
#  → prints SESSION=<uuid>, exit code, token usage, final message

"$W" cont <SESSION> "follow-up prompt"   # continue with the worker's accumulated context
"$W" last <SESSION>                      # reprint final message
"$W" log  <SESSION>                      # path to raw JSONL event log (every command it ran)
"$W" list                                # all tracked sessions: time, label, workdir, tokens
```

For anything non-trivial, launch via Bash `run_in_background: true` and read the output
when notified — don't foreground-block.

## Options on `run`

| Flag | Meaning |
|---|---|
| `-C dir` | Workdir the worker edits (default `$PWD`). Always set this explicitly. |
| `-p profile` | `worker` (default: gpt-5.5, high reasoning, workspace-write) or `worker-lite` (gpt-5.4-mini, medium — renames, config edits, scripted transforms) |
| `-m model` | Override model directly (gpt-5.5, gpt-5.4, gpt-5.4-mini) |
| `--full-access` | Escalate sandbox to danger-full-access — only when the brief needs network (package installs) or writes outside the workdir |
| `--schema file.json` | Force the final message to match a JSON Schema — use when parsing the result programmatically |
| `--label name` | Registry label so `list` stays readable |
| `-- <args>` | Passthrough to `codex exec` (e.g. `-- --add-dir /other/dir`, `-- -i shot.png`) |

## Notes

- Profiles are files at `~/.codex/<name>.config.toml` (Codex ≥0.141; legacy
  `[profiles.*]` tables in config.toml are a hard error).
- Worker runs pass `-c skills.enabled=false`: Codex mirrors all Claude skills in
  `~/.codex/skills/` and burns ~20k tokens/run following them if left on. Re-enable per
  run with `-- -c skills.enabled=true` only if the brief genuinely needs a skill.
- The JSONL event log is the ground truth for what the worker actually did — check it
  when a result smells off.
- Workspace-write sandbox has no network; test commands needing the net will fail
  inside the worker. Either run VERIFY yourself afterward (you were doing that anyway)
  or grant `--full-access` deliberately.
