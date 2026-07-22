# Claude Code Skills

A collection of Claude Code skills. These extend Claude Code with
reusable workflows, automations, and capabilities.

## Install

In Claude Code, run:

```
/plugin marketplace add EvanOman/claude-skills
/plugin install evan-skills@evan-skills
```

Skills are then available as `/evan-skills:skill-name` (e.g., `/evan-skills:slides`).

## Available Skills

### Research & Content

| Skill | Command | Description |
|-------|---------|-------------|
| Deep Research | `/evan-skills:deep-research` | Parallelized multi-agent research with synthesis. Partitions a question across 6-15 sub-agents, each writing a report, then synthesizes into a single document. |
| Slides | `/evan-skills:slides` | Generate Markdown slide decks optimized for Gamma. Designs a narrative arc, writes assertion-titled slides, optionally submits to Gamma's API. |
| Bulletize | `/evan-skills:bulletize` | Restructure rambling prose into bullet points. Preserves original wording -- reformats, doesn't rewrite. |

### Development Workflow

| Skill | Command | Description |
|-------|---------|-------------|
| Pystd | `/evan-skills:pystd` | Apply Python project standards: uv, ruff, ty, just, GitHub Actions CI. Includes pyproject.toml, Justfile, and CI templates. |
| Tsstd | `/evan-skills:tsstd` | Apply TypeScript project standards: Biome, strict tsc, Vitest, just, GitHub Actions CI. Includes tsconfig.json, biome.json, Justfile, and CI templates. |
| Bootstrap App | `/evan-skills:bootstrap-app` | Scaffold a FastAPI + Tailwind mobile-first web app with dark mode, async SQLite, and the full Astral toolchain. |
| Explorable | `/evan-skills:explorable` | Build an interactive pedagogical web app ("explorable") to develop intuition for how a system or algorithm works step-by-step. Intuition-first design workflow, traced-execution animations, cycling defaults, no build step. |
| Review | `/evan-skills:review` | Code review using a sub-agent with a structured rubric (correctness, security, error handling, quality, testing, docs). |
| RVM | `/evan-skills:rvm` | Review-Merge workflow: review changes, fix issues, push, and merge in one pass. |
| Handoff | `/evan-skills:handoff` | Generate a structured handoff note capturing session state, completed work, in-flight tasks, and next steps for a future agent. |
| Session History Search | `/evan-skills:session-history-search` | Search, list, and review past Claude Code and Codex CLI sessions. Includes twin CLI tool families (`cc-*` for Claude Code, `cx-*` for Codex) for BM25-ranked full-prompt search, project filtering, and full transcript reading. |
| Prep Public | `/evan-skills:prep-public` | Prepare a project for public GitHub release. Scans for secrets, cleans paths, sets up .gitignore/.env.example/LICENSE/README. |
| Overmind | `/evan-skills:overmind` | Enter persistent orchestration mode with structured briefs, isolated workers, and verified handoffs. Uses native registries in-harness and a shared durable MCP lifecycle for Claude↔Codex dispatch, follow-up, waiting, results, interruption, and cleanup. |
| Overmind v2 | `/evan-skills:overmind-v2` | Orchestrate persistent Claude↔Codex worker groups through a shared SQLite-backed broker with event-driven waits, bounded collection, recovery, and visible billing provenance. |

### Meta

| Skill | Command | Description |
|-------|---------|-------------|
| Add Skill | `/evan-skills:add-skill` | Meta-skill for creating new Claude Code skills. Guides you through structure, frontmatter, and design principles. |
| Graduate Skill | `/evan-skills:graduate-skill` | Graduate a private local skill into this repo: scrub for secrets/personal paths, move, symlink back so the repo is the source of truth, update README, commit and push. |
| Design Polish | `/evan-skills:design-polish` | Polish frontend design with distinctive aesthetics. Asks for a style vibe (Refined, Bold, Warm, Technical) then applies a complete design system. |

## Not Included: Deployment Pipeline Skills

These skills are part of my personal deployment setup and are too machine-specific to
share directly, but the pattern is worth describing -- you could adapt it to your own
infrastructure.

### The Three-Skill Deployment Pipeline

I run a home server (Ubuntu, systemd, Tailscale) that hosts ~15 personal web apps and
services. Three skills work together to go from "code on disk" to "app accessible from
any device on my Tailnet":

**`/install-service`** -- Takes the current project directory and generates a systemd
service file. It detects the project type (Docker, Python/uv, Node.js, static site) and
produces the right unit file with restart rate limiting, proper environment setup, and
no dev-mode reload watchers. Installs as either a user or system service, enables
lingering so it survives logout, and verifies the service starts.

**`/add-to-homepage`** -- Exposes the running service via Tailscale Serve at a subpath
(e.g., `https://mymachine.tailnet.ts.net/myapp`). Handles the common gotcha of subpath
routing -- most frameworks assume they're served from `/`, so the skill knows how to
configure `root_path` (FastAPI), `base-path` (Jaeger), `serve_from_sub_path` (Grafana),
relative HTMX paths, etc. Then adds a service card to a central HTML homepage so all
apps are discoverable from one page.

**`/deploy`** -- Orchestrates the full lifecycle. On first run: install service + configure
Tailscale + add to homepage. After that: update (rebuild/restart), status check, or
uninstall. Encourages adding a `just redeploy` command to each project so any agent can
deploy changes without understanding the project's internals.

### Adapting This Pattern

The core idea: **three composable skills that separate concerns** (process management,
network exposure, lifecycle orchestration). If you use a different stack:

- Replace systemd with Docker Compose, PM2, or launchd
- Replace Tailscale Serve with Caddy, nginx, or Cloudflare Tunnel
- Replace the HTML homepage with Dashy, Homer, or a bookmark file
- Keep the `/deploy` orchestrator pattern -- it's the glue

If you want the actual skill files to study, just ask -- happy to share them separately.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed
- For `/evan-skills:pystd` and `/evan-skills:bootstrap-app`: [uv](https://docs.astral.sh/uv/), [just](https://github.com/casey/just)
- For `/evan-skills:tsstd`: [Node.js](https://nodejs.org/), [just](https://github.com/casey/just)
- For `/evan-skills:slides` Gamma integration: `GAMMA_API_KEY` environment variable
