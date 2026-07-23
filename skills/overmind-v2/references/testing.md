# Test Overmind v2 deterministically

Resolve the skill directory to an absolute `SKILL_ROOT`. The self-contained superiority gate uses
an isolated state directory and the same broker/provider adapter boundary as production:

```bash
python3 "$SKILL_ROOT/scripts/bakeoff.py" --json
```

It compares equivalent four-worker missions, asserts v2 uses no more than three parent lifecycle
calls, verifies restart-safe idempotency, and gates persistent-MCP status latency below 50 ms. Cold
CLI startup latency is reported separately and is not the agent-facing MCP gate.

Run all broker regressions from the owning repository root:

```bash
python3 -m unittest discover -s skills/overmind-v2/tests -v
```

The suite uses `unittest discover` with a `support` module imported from the tests directory, so
running plain `pytest` from elsewhere fails collection — use the documented command above.

## Activate the fake provider manually

The fake provider is test-only and is never discovered unless explicitly injected. From the owning
repository root, create an isolated directory and export:

```bash
export OVERMIND_V2_STATE_DIR=/tmp/overmind-v2-manual/state
export OVERMIND_V2_FAKE_PROVIDER="$SKILL_ROOT/tests/fake_provider.py"
export OVERMIND_V2_FAKE_STATE_DIR=/tmp/overmind-v2-manual/provider
export OVERMIND_V2_FAKE_CALL_LOG=/tmp/overmind-v2-manual/provider-calls.jsonl
```

A deterministic job uses the normal fields plus a test-only `fake` object:

```json
{
  "provider": "fake",
  "label": "worker-1",
  "cwd": "/tmp",
  "brief": "deterministic worker 1",
  "billing_class": "subscription-native",
  "fake": {"mode": "success", "delay": 0.25, "result": "RESULT-1"}
}
```

Supported test modes are `success`, `failure`, `unknown`, and `hold`. Keep this adapter out of live
state and production MCP registrations. Prefer the bake-off script over manually reproducing its
restart and performance assertions.
