---
name: graduate-skill
description: Graduate a private local skill into Evan's public GitHub skills repo, making the repo the single source of truth via symlinks. Use when the user wants to "graduate this skill", "publish this skill to my public skills", "move a skill to the public repo", or promote a personal ~/.claude/skills skill to the shared claude-skills-public repo.
disable-model-invocation: true
allowed-tools: Bash(ls *), Bash(git *), Bash(mv *), Bash(ln *), Bash(chmod *), Bash(mkdir *), Bash(readlink *), Bash(diff *), Bash(realpath *), Read, Write, Edit, Glob, Grep
---

# Graduate a Skill to the Public Repo

Promote a private skill from `~/.claude/skills/<name>/` into `~/dev/claude-skills-public/skills/<name>/`, then symlink it back so the repo is the single source of truth. `<name>` is the skill to graduate (ask if not given).

Repo path: `~/dev/claude-skills-public` (branch `main`).

**HARD RULE — never `git add -A` / `git add .` in that repo.** Multiple Claude sessions may share the checkout. Stage only the exact paths this graduation touches. Do not `checkout`/`switch`/`reset`/`rebase` there.

Run the phases in order. Stop and ask the user at the two gates (scrub findings, final ship).

## Phase 1: Preflight

- `~/.claude/skills/<name>/` exists, is a real directory, and is **not already a symlink** (`readlink` returns nothing). If it's a symlink, it's already graduated — stop.
- `~/dev/claude-skills-public` exists and `git -C ~/dev/claude-skills-public branch --show-current` is `main`.
- `git -C ~/dev/claude-skills-public status --short` — note any unrelated uncommitted changes (concurrent session). Proceed, but stage only your own paths later.
- If `~/dev/claude-skills-public/skills/<name>/` already exists: **drift reconciliation** — `diff -ru` the two dirs, show the user, ask which side wins before continuing. Don't overwrite silently.

## Phase 2: Scrub (CRITICAL)

Scan **every file in the skill dir** (SKILL.md, bin/, references/, scripts/, assets/) for leaks. This is per-skill-directory, not per-project.

Grep for:
- **Secrets/keys:** `sk-ant-`, `sk-`, `AKIA`, `ghp_`, `gho_`, `Bearer`, `token=`, `password`, `secret`, `credential`, `-----BEGIN`, high-entropy base64/hex >32 chars, `.pem`/`.key` files.
- **Local/personal:** `/home/<user>/`, `/Users/<user>/`, `C:\Users\`; personal email addresses (gmail/personal domains, `@` greps); real names in comments; internal hostnames, IPs, Tailscale/ngrok hosts.
- **Employer/internal:** employer names, codenames, internal project references, internal URLs.

**Genericize, don't just delete.** Preferred fixes:
- Hardcoded home paths in Python → module constant `HOME_PREFIX = str(Path.home()) + "/"`, then `path.replace(HOME_PREFIX, "~/")` for display (this exact pattern is used in the repo's `session-history-search` bins). In shell, use `$HOME`/`~`. Keep the tool functional, just not machine-specific.
- Personal email/name only needed as an example → replace with a placeholder.

Present all findings (severity + file:line + proposed fix) to the user. **Some personal references are intentional** (a skill may legitimately reference Evan's vault or workflow) — that's a human call. **GATE: get the user's OK on the scrub before moving.** If a real live secret is found, tell the user to revoke it first.

Apply approved fixes in place (still under `~/.claude/skills/<name>/`).

## Phase 3: Move

Source isn't a git repo, so plain move + add is fine:

```
mv ~/.claude/skills/<name> ~/dev/claude-skills-public/skills/<name>
chmod +x ~/dev/claude-skills-public/skills/<name>/bin/*   # only if bin/ exists
```

## Phase 4: Symlink Back

```
ln -s ~/dev/claude-skills-public/skills/<name> ~/.claude/skills/<name>
```

If the skill has a `bin/` of CLI tools, symlink each tool into `~/.claude/bin/` too:
```
ln -s ~/dev/claude-skills-public/skills/<name>/bin/<tool> ~/.claude/bin/<tool>
```
If a `~/.claude/bin/<tool>` already exists: `diff` it against the repo copy first. If identical, replace with the symlink. If it differs, show the user and ask before replacing.

## Phase 5: README

Add one row to the skills table in `~/dev/claude-skills-public/README.md`, matching the existing format:
`| <Title Case Name> | \`/evan-skills:<name>\` | <one-line description> |`
Pick the right section (Research & Content, Development Workflow, or Meta).

## Phase 6: Verify

- `readlink ~/.claude/skills/<name>` resolves to the repo dir.
- `Read` SKILL.md through the symlink path — readable.
- For each bin tool: run `~/.claude/bin/<tool> --help` (or equivalent) and confirm exit 0.

## Phase 7: Ship

- Show the user the full diff: `git -C ~/dev/claude-skills-public status --short` and `git -C ~/dev/claude-skills-public diff` plus the new untracked skill dir.
- **GATE: explicit user confirmation to commit + push.**
- Stage only this graduation's paths (never `-A`):
  ```
  git -C ~/dev/claude-skills-public add skills/<name> README.md
  ```
- Commit with a concise message, e.g. `Graduate <name> skill to public repo`.
- `git -C ~/dev/claude-skills-public push`.

## Reversal (un-graduate)

1. `rm ~/.claude/skills/<name>` (removes the symlink only) and any `~/.claude/bin/<tool>` symlinks.
2. Copy the dir back: `cp -r ~/dev/claude-skills-public/skills/<name> ~/.claude/skills/<name>`.
3. `git -C ~/dev/claude-skills-public rm -r skills/<name>`, revert the README row, commit, push.
