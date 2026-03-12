---
name: prep-public
description: Prepare a project for public release on GitHub. Scans for secrets, API keys, local file paths, and embarrassing content. Sets up .gitignore, .env.example, LICENSE, README. Applies language-specific standards (pystd for Python). Use when the user wants to open-source, publish, or share a project publicly.
disable-model-invocation: true
allowed-tools: Bash(ls *), Bash(git *), Bash(rm *), Bash(mkdir *), Bash(just *), Read, Write, Edit, Glob, Grep
---

# Prepare Project for Public Release

Audit and clean a project so it's ready to share publicly on GitHub.

## Process

Run these phases in order. Use sub-agents for independent work (security scan, README drafting, screenshots) to parallelize.

### Phase 1: Security Scan (CRITICAL)

This is the most important phase. Scan every file in the project (excluding `.venv/`, `node_modules/`, etc.) for:

**Secrets and credentials:**
- API keys: `sk-ant-`, `sk-`, `AKIA`, `ghp_`, `gho_`, `Bearer`, `token=`
- Passwords: `password`, `passwd`, `secret`, `credential`
- Connection strings: `postgres://`, `mysql://`, `redis://`, `mongodb://`
- Private keys: `-----BEGIN`, `.pem`, `.key` files
- Any high-entropy strings that look like keys (base64, hex >32 chars)

**Local/personal references:**
- Home directory paths: `/home/<user>/`, `/Users/<user>/`, `C:\Users\`
- Hardcoded hostnames, IP addresses, internal URLs
- Personal email addresses, names in code comments
- Tailscale, ngrok, or tunnel-specific configuration

**Embarrassing content:**
- Debug `print()` statements in production code
- `console.log` debug lines in frontend
- TODO/FIXME/HACK comments that reveal unfinished work
- Commented-out code blocks
- Test credentials or dummy data that looks real
- Internal project names, codenames, or references to employers

**Service/deployment files to scrutinize:**
- systemd `.service` files (often contain plaintext keys and local paths)
- Docker compose files, Dockerfiles
- CI/CD configs with hardcoded values
- Terraform/infrastructure files

Report all findings with severity (CRITICAL/HIGH/MEDIUM/LOW) and file:line references. If a real secret is found, **immediately tell the user to revoke it** before proceeding.

### Phase 2: Fix Issues

For each finding:

- **Secrets**: Remove from file. If the file is a deployment artifact (service file, docker-compose with secrets), either remove it from the repo or templatize it.
- **Local paths**: Replace with configurable values (env vars, relative paths, CLI args).
- **Debug output**: Remove or comment out.
- **Embarrassing content**: Clean up or remove.

Create or update:

- **`.gitignore`** — must cover at minimum:
  ```
  .venv/
  node_modules/
  __pycache__/
  *.pyc
  .env
  *.db
  *.sqlite
  dist/
  build/
  .DS_Store
  ```
  Add project-specific entries (data directories, local config, etc.)

- **`.env.example`** — document every required environment variable with placeholder values. Use the pattern `VARIABLE_NAME=<description or example>`.

- **`LICENSE`** — ask the user which license they want if none exists. Default suggestion: MIT.

### Phase 3: Apply Standards

**Python projects** (if `pyproject.toml` exists):
- Run `/pystd` skill to apply Astral stack (ruff, ty, pytest-cov, Justfile, CI)
- Run `just fc` to verify everything passes

**Node/JS projects** (if `package.json` exists):
- Verify `.eslintrc` or equivalent exists
- Check for `prettier` config

**Any project:**
- Verify tests exist and pass
- Check that the dev server binds to `127.0.0.1`, not `0.0.0.0`

### Phase 4: README

Write a `README.md` that is:

- **Matter-of-fact** — describe what it does, not why it's amazing
- **Honest about scope** — if it's a demo, say it's a demo
- **Structured** for quick scanning:
  1. One-line description
  2. Screenshot (if frontend exists — take one with agent-browser)
  3. What it does (2-3 paragraphs max)
  4. How it works (architecture, key design decisions)
  5. Setup instructions (clone, install, configure, run)
  6. Development commands
  7. Architecture overview (file tree with descriptions)
  8. Stack/dependencies list
  9. License
- **Highlights the interesting parts** — what makes this project worth looking at? The tech combo, the architecture pattern, the sandbox approach, etc. Don't hype it, but do point out what's distinctive.

### Phase 5: Final Verification

1. Initialize git if not already: `git init`
2. Run `git status` to verify `.gitignore` is catching everything sensitive
3. Grep the tracked files one more time for secrets and local paths
4. Run the test suite one final time
5. Report the complete status to the user

## Checklist Output

After completion, present a checklist:

```
[x] Security scan — no secrets found
[x] .gitignore — covers .env, .venv, data files
[x] .env.example — documents required env vars
[x] LICENSE — MIT
[x] README.md — with screenshots
[x] Standards applied — linting, formatting, type-checking, CI
[x] Tests passing — N/N
[x] Git clean — no sensitive files tracked
[ ] ACTION REQUIRED: Revoke key XYZ (if applicable)
```
