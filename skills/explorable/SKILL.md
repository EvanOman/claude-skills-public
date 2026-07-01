---
name: explorable
description: Build an interactive pedagogical web app ("explorable") to develop intuition for how a system, algorithm, or process works step-by-step. Use when the user wants to visualize how something works, build a learning app, or watch a system evolve interactively. Best for systems and computer programs (databases, protocols, data structures, algorithms).
argument-hint: <topic, e.g. "LSM trees" or "Raft leader election">
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion
---

# Explorable: pedagogical interactive apps

Build a single-page interactive web app whose job is to land specific intuitions — not to demo. Design principles and failure modes: `principles.md`. Proven code patterns to port: `reference-patterns.md` (both in this skill directory).

## Iron rule

**Name the target intuitions before designing any UI.** Ask the user: "Which 1–3 intuitions should this land?" and "What naive approach does this design beat?" Every screen element must serve an intuition; the naive-comparison metric must be visible in the UI (e.g. "consistent hashing moved 3/20 keys; mod-N would have moved 17/20").

## Workflow

Gates require the user's input. If the user has pre-delegated ("use your judgment") or the work runs inside a subagent that cannot ask, don't stall: make the call, label it as an assumption, and record it in the project README so it can be revisited.

1. **Intuition targets** — get the 1–3 intuitions + the contrast baseline from the user. Gate here.
2. **Classify the subject** → layout pattern (table in `principles.md`): algorithm / data structure / distributed system / math-ML / state machine.
3. **Inventory** — draft three lists for the user to edit: persistent state to show (always visible, position = meaning), operations that change it, and the dynamic property worth animating per operation type (read path and write path may each get one — e.g. a storage engine's read-trace, a B-tree's split propagation). Document the final lists in the project README. Gate here.
4. **Interaction surface** — pick from: cycling defaults (NEVER blank-canvas inputs — see rule below), a **seed button** when the structure needs critical mass before it's interesting (one click bulk-loads a curated dataset; required for trees/graphs), preset scenarios, parameter knobs, time controls (required whenever an animation carries meaning — a speed slider alone is not time control; pause + step + skip satisfies it for irreversible structural animations, backward scrub only when steps are cheaply replayable), failure-injection buttons (distributed systems).

   **Cycling-defaults rule: every input is pre-filled at every moment.** Write inputs advance through a deterministic sequence (strings: an alphabetical word list; numeric keys: a hand-shuffled spread like `[42, 17, 88, 5, …]` so structure-changing events arrive at a good rhythm). Read/lookup inputs pre-fill too — after any insert with the just-written key, after any seed with a mid-sequence key that demonstrates an interesting path. The seed→lookup path is the first thing a new user tries; it must work with zero typing.
5. **Scaffold** — backend wrapping a standalone, readable, fully-typed, tested module that owns ALL the logic (the module is itself a study artifact; UI must stay thin). Plain HTML/CSS/JS frontend, no build step. Triptych = the *conceptual* separation (state visualization + controls + annotated event log), not literally three equal panels — use named CSS grid areas and let the subject dictate proportions. Pick an uncommon high port and verify it's free first. Use `APP_PORT`/`APP_ROOT_PATH` env vars with root_path consumed by the ASGI server, not the framework router (see `reference-patterns.md` — the framework-level setting breaks route matching behind path-stripping reverse proxies), so the app is deployable behind a proxy subpath unchanged.
6. **Validate** — drive the golden path in a real browser: every operation, the animated path, defaults cycling, custom input, reset. Screenshot evidence. Don't claim success from "the server started" — exercise the UI. Use whatever browser automation is available (a browser MCP server/skill if installed; otherwise add Playwright to the project's dev dependencies — `uv add --dev playwright && uv run playwright install chromium` — and drive it from a short script). If no browser automation is possible in the environment, validate every API endpoint with curl, state that the visual layer is unverified, and ask the user to confirm in their browser. Reset state when done.

## Red flags — stop and fix

- UI sketched before intuitions named
- Input field that opens empty or "prompts for a name"
- ANY input left blank after ANY action — seed/insert/reset must all leave every field pre-filled (the seed→lookup path is where this slips)
- Speed slider standing in for pause/step
- Algorithm logic living in frontend state
- Naive-approach comparison missing from the screen
- A frontend build step (React/Vite/npm) appearing in the plan
- Animation invented by the frontend instead of replaying a real execution trace from the backend
