---
name: graduate-skill
description: Promote a privately owned skill into Evan's public skills repository, update the canonical agent-config registry, scrub and validate the public payload, and publish managed links to both Claude and Codex. Use when the user asks to graduate, publish, open-source, or move a personal skill into the public skills repo.
---

# Graduate a Skill

Move a skill from its canonical private repository owner into
`/home/evan/dev/claude-skills-public/skills/<name>`, then update agent-config so both harnesses
discover that public owner.

Use these fixed locations:

- public skills repository: `/home/evan/dev/claude-skills-public`
- ownership registry: `/home/evan/dev/agent-config/registry/skills.toml`
- publisher: `/home/evan/dev/agent-config/scripts/sync-skills`

## Hard rules

- Treat an ordinary owning repository as the source of truth. Harness install directories, plugin
  caches, marketplace checkouts, dependency trees, and generated worktrees are never sources.
- Use installed links only as evidence for locating an owner; reconcile them against the registry.
- Preserve unrelated work. Never switch, reset, or rebase a shared checkout, and never stage all
  changes. Stage only paths changed by this graduation.
- Stop at both human gates: scrub approval and final ship approval.

## 1. Establish ownership

Read the registry entry for `<name>`.

- If registered, use its `source` as the candidate owner and confirm it resolves to an ordinary Git
  repository containing `SKILL.md`.
- If unregistered, ask the user to identify the private owning repository. Do not infer a harness
  install directory as the owner. Plan a new registry entry with the intended harnesses.
- If the registry, installed links, and filesystem disagree, show the mismatch and ask which ordinary
  repository is authoritative before changing anything.
- If the source already resolves to the public destination, report that it is already graduated and
  stop.

Record `git status --short`, current branch, and repository root for the source, public repository,
and agent-config. Note unrelated changes and work around them.

If the public destination already exists, compare it with the source and ask the user which content
wins. Never overwrite a divergent destination silently.

## 2. Scrub the complete payload

Scan every file in the canonical source, including scripts, references, assets, examples, and agent
metadata. Look for:

- credentials, tokens, private keys, authorization headers, and high-entropy secret-like values;
- personal paths, emails, names, private hosts, IPs, tailnet addresses, and internal URLs;
- employer names, codenames, private project references, and proprietary examples;
- harness-home paths or provider-specific mechanics presented as portable workflow.

Genericize machine-specific implementation without breaking it: derive paths from the environment,
use placeholders in examples, and move legitimate provider mechanics into provider-specific
metadata or references. Some personal facts may be intentional; do not decide that silently.

Present findings as severity, file and line, exposure risk, and proposed fix.

**SCRUB GATE:** obtain explicit approval before applying scrub changes or copying anything into the
public repository. If a live secret is found, require revocation or rotation before continuing.

Apply only approved fixes in the canonical source and run its relevant tests.

## 3. Prepare the ownership transfer

Copy the approved source into the public destination while preserving executable bits. Do not copy
Git metadata, caches, generated output, or unrelated repository files.

Then:

1. Validate the copied skill with the available skill validator.
2. Run its bundled scripts in a safe help, check, or fixture mode as appropriate.
3. Scan `SKILL.md` with agent-config's portability scanner.
4. Add or update `agents/openai.yaml` when Codex UI metadata is missing or stale.
5. Update the public repository's skill table using its existing format.
6. Update the registry entry so `source` is the public destination and its `harnesses` accurately
   names every supported harness.
7. Remove the old tracked source from its owning repository only after the public copy validates.

Do not hand-create links in either harness home. Preview publication through the registry:

```bash
/home/evan/dev/agent-config/scripts/sync-skills --dry-run
```

Resolve conflicts or unexpected removals before proceeding.

## 4. Review and ship

Show the user:

- status and full scoped diff for every affected repository;
- the complete new public skill payload, including untracked files;
- validator, portability, and script/test results;
- the skill-sync dry-run and the exact paths it will update;
- the proposed commits and push order.

**SHIP GATE:** obtain explicit approval before publishing managed links, committing, or pushing.
Clarify whether approval covers all affected repositories and both harness installations.

After approval:

1. Run `/home/evan/dev/agent-config/scripts/sync-skills` to publish the registry state.
2. Run it again with `--check` to verify both managed skill forests.
3. Stage only the public skill, its public index row, the old owner's removed paths, and the registry
   entry. Never use a repository-wide add.
4. Commit each owning repository with a concise message and push the public payload before the
   registry pointer that depends on it.
5. Report commit IDs, push results, and final sync verification.

If any step fails, stop and report the partial state. Do not improvise destructive rollback.

## Reversal

Reverse graduation through ownership, not harness homes: restore the skill to an ordinary private
owner, point the registry back to it, preview and run `sync-skills`, remove the public copy through
Git, and ship the scoped repository changes behind the same explicit gate.
