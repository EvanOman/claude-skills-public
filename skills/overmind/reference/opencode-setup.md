# Setup: opencode + GLM-5.2 worker

One-time wiring so `worker.sh` can drive a GLM-5.2 worker. This documents the full
setup so it can be reproduced or swapped.

## 1. opencode

Installed via Homebrew (`brew install opencode`). Verify: `opencode --version`.
Upgrade: `opencode upgrade`.

## 2. Provider config — `~/.config/opencode/opencode.json`

A **custom OpenAI-compatible provider** named `glm` pointed at z.ai's Coding-Plan endpoint.
The key is read from the `ZAI_API_KEY` env var (which `worker.sh` populates from the key
file — see step 3). Deliberately **no** global `permission: allow` here, so interactive
opencode stays safe; the worker opts into auto-approve per-run via
`--dangerously-skip-permissions`.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "glm": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Z.ai GLM (Coding Plan)",
      "options": {
        "baseURL": "https://api.z.ai/api/coding/paas/v4",
        "apiKey": "{env:ZAI_API_KEY}"
      },
      "models": {
        "glm-5.2":     { "name": "GLM-5.2" },
        "glm-5-turbo": { "name": "GLM-5-Turbo" }
      }
    }
  }
}
```

Model reference used by `worker.sh` / `opencode run -m`: **`glm/glm-5.2`**.

Endpoints (for reference):
- OpenAI-compatible (what we use): `https://api.z.ai/api/coding/paas/v4`
- Anthropic-compatible (alt):      `https://api.z.ai/api/anthropic`

## 3. API key

Get one from the **GLM Coding Plan** dashboard: https://z.ai/manage-apikey/apikey-list
(requires an active Coding Plan subscription — Lite is the cheapest tier).

Store it so `worker.sh` finds it (chmod 600, not world-readable):

```bash
install -m 600 /dev/null ~/.config/opencode/zai.key
printf '%s' 'YOUR_KEY_HERE' > ~/.config/opencode/zai.key
```

Sanity check the whole chain:
```bash
ZAI_API_KEY="$(cat ~/.config/opencode/zai.key)" opencode models glm    # lists GLM models
ZAI_API_KEY="$(cat ~/.config/opencode/zai.key)" opencode run -m glm/glm-5.2 "reply with OK"
```

## 4. Swapping the worker model

`worker.sh` respects `WORKER_MODEL=provider/model`. To orchestrate a different worker,
add its provider block to `opencode.json` (any models.dev provider, or another
OpenAI/Anthropic-compatible endpoint) and set `WORKER_MODEL`. The orchestration discipline
in `SKILL.md` is unchanged.

## Gotchas learned in practice

- Headless `opencode run` **blocks on permission prompts** unless you pass
  `--dangerously-skip-permissions` (or set `permission: allow` in config). `worker.sh`
  passes the flag so workers don't hang. The web docs mention a `--auto` flag — that's
  wrong for this version; the real flag is `--dangerously-skip-permissions`.
- Session ids come from `opencode session list`. `worker.sh` resolves the freshest one
  after a run; if you launch workers in parallel, capture `SESSION=` from each run's output
  rather than trusting "newest".
- `--variant` sets provider reasoning effort (e.g. `high`); expose via `WORKER_EFFORT`.
