#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
WORKER="$SCRIPT_DIR/claude-worker.sh"
TEST_ROOT=$(mktemp -d -t claude-worker-test.XXXXXX)
FAKE_CLAUDE="$TEST_ROOT/fake-claude"
JOBS_DIR="$TEST_ROOT/jobs"
CALLS="$TEST_ROOT/calls.log"
COUNTER="$TEST_ROOT/counter"
mkdir -p "$JOBS_DIR" "$TEST_ROOT/workdir"
trap 'rm -rf -- "$TEST_ROOT"' EXIT

cat >"$FAKE_CLAUDE" <<'FAKE'
#!/usr/bin/env bash
set -euo pipefail

jobs=${CLAUDE_JOBS_DIR:?}
root=$(dirname -- "$jobs")
calls="$root/calls.log"
counter="$root/counter"
cmd=${1:-launch}

auth=clean
[[ -z "${ANTHROPIC_API_KEY:-}" && -z "${ANTHROPIC_AUTH_TOKEN:-}" && -z "${ANTHROPIC_BASE_URL:-}" ]] || auth=dirty
printf 'cwd=%s auth=%s command=' "$PWD" "$auth" >>"$calls"
printf '%q ' "$@" >>"$calls"
printf '\n' >>"$calls"

case "$cmd" in
  agents)
    shopt -s nullglob
    files=("$jobs"/*/state.json)
    if (( ${#files[@]} == 0 )); then
      printf '[]\n'
    else
      jq -s '[.[] | {id:.daemonShort, cwd, kind:"background", startedAt:.createdAt,
        sessionId, name, state}]' "${files[@]}"
    fi
    exit 0
    ;;
  logs)
    id=${2:?}
    state="$jobs/$id/state.json"
    if jq -e '(.output | type) == "object" and (.output | has("result"))' "$state" >/dev/null; then
      jq -r '.output.result' "$state"
    else
      printf 'LOG:%s\n' "$id"
    fi
    exit 0
    ;;
  stop)
    id=${2:?}
    state="$jobs/$id/state.json"
    tmp="$state.tmp"
    jq '.state="stopped" | .detail="stopped by test"' "$state" >"$tmp"
    mv "$tmp" "$state"
    printf 'Stopped %s\n' "$id"
    exit 0
    ;;
  rm)
    id=${2:?}
    rm -rf -- "$jobs/$id"
    printf 'Removed %s\n' "$id"
    exit 0
    ;;
esac

number=0
[[ ! -f "$counter" ]] || number=$(<"$counter")
number=$((number + 1))
printf '%s\n' "$number" >"$counter"
short=$(printf '%08x' "$number")
full=$(printf '%08x-0000-4000-8000-%012d' "$number" "$number")
resume=""
name=""
prompt=""
while (( $# > 0 )); do
  case "$1" in
    --resume) resume="$2"; shift 2 ;;
    --name) name="$2"; shift 2 ;;
    --model|--permission-mode) shift 2 ;;
    --bg) shift ;;
    --) shift; prompt=${1:-}; break ;;
    *) prompt="$1"; shift ;;
  esac
done

state=done
detail="fake completed"
result="FAKE_OK"
if [[ -n "$resume" ]]; then
  result="CONTINUED:$prompt"
elif [[ "$prompt" == *FAIL* ]]; then
  state=failed
  detail="fake failure"
  result=""
elif [[ "$prompt" == *HANG* ]]; then
  state=working
  detail="fake working"
  result=""
fi

mkdir -p "$jobs/$short"
if [[ -n "$result" ]]; then
  jq -n --arg state "$state" --arg detail "$detail" --arg result "$result" \
    --arg daemon "$short" --arg session "$full" --arg resume "$resume" \
    --arg cwd "$PWD" --arg name "$name" --arg created "$number" \
    '{state:$state, detail:$detail, output:{result:$result}, daemonShort:$daemon,
      sessionId:$session, resumeSessionId:($resume | if . == "" then $session else . end),
      cwd:$cwd, name:$name, createdAt:($created | tonumber)}' >"$jobs/$short/state.json"
else
  jq -n --arg state "$state" --arg detail "$detail" --arg daemon "$short" \
    --arg session "$full" --arg resume "$resume" --arg cwd "$PWD" \
    --arg name "$name" --arg created "$number" \
    '{state:$state, detail:$detail, output:null, daemonShort:$daemon,
      sessionId:$session, resumeSessionId:($resume | if . == "" then $session else . end),
      cwd:$cwd, name:$name, createdAt:($created | tonumber)}' >"$jobs/$short/state.json"
fi
printf '\033[32mBackground agent started: %s\033[0m\n' "$short"
FAKE
chmod +x "$FAKE_CLAUDE"

run_worker() {
  CLAUDE_BIN="$FAKE_CLAUDE" \
    CLAUDE_JOBS_DIR="$JOBS_DIR" \
    CLAUDE_WORKER_POLL_INTERVAL=0.01 \
    "$WORKER" "$@"
}

assert_contains() {
  local haystack="$1" needle="$2"
  [[ "$haystack" == *"$needle"* ]] || {
    printf 'expected output to contain %q, got:\n%s\n' "$needle" "$haystack" >&2
    exit 1
  }
}

set +e
missing_model=$(run_worker run 'brief' 2>&1)
missing_code=$?
set -e
[[ "$missing_code" == 2 ]]
assert_contains "$missing_model" 'model is required'

subscription_output=$(
  ANTHROPIC_API_KEY=never-print-api \
  ANTHROPIC_AUTH_TOKEN=never-print-token \
  ANTHROPIC_BASE_URL=https://never-print.invalid \
    run_worker run --wait -C "$TEST_ROOT/workdir" -m sonnet --name fake-one \
      --subscription 'DO WORK'
)
assert_contains "$subscription_output" 'JOB=00000001'
assert_contains "$subscription_output" 'FAKE_OK'
assert_contains "$(<"$CALLS")" 'auth=clean'
assert_contains "$(<"$CALLS")" "cwd=$TEST_ROOT/workdir"
if rg -q 'never-print' "$CALLS" <<<"$subscription_output"; then
  printf 'credential marker leaked into output or fake CLI log\n' >&2
  exit 1
fi

status_output=$(run_worker status 00000001)
assert_contains "$status_output" 'STATE=done'
assert_contains "$status_output" 'NAME=fake-one'
[[ "$(run_worker last 00000001)" == 'FAKE_OK' ]]

full_status_output=$(run_worker status 00000001-0000-4000-8000-000000000001)
assert_contains "$full_status_output" 'ID=00000001'

set +e
short_prefix_output=$(run_worker status 0000000 2>&1)
short_prefix_code=$?
uuid_prefix_output=$(run_worker status 00000001-0000 2>&1)
uuid_prefix_code=$?
set -e
[[ "$short_prefix_code" == 2 ]]
[[ "$uuid_prefix_code" == 2 ]]
assert_contains "$short_prefix_output" 'expected 8-character daemon ID or full session UUID'
assert_contains "$uuid_prefix_output" 'expected 8-character daemon ID or full session UUID'

set +e
failure_output=$(run_worker run --wait -m haiku 'FAIL NOW' 2>&1)
failure_code=$?
set -e
[[ "$failure_code" == 3 ]]
assert_contains "$failure_output" 'JOB=00000002'
assert_contains "$failure_output" 'fake failure'

continuation_output=$(
  ANTHROPIC_API_KEY=never-print-cont-api \
  ANTHROPIC_AUTH_TOKEN=never-print-cont-token \
  ANTHROPIC_BASE_URL=https://never-print-cont.invalid \
    run_worker cont --subscription --wait 00000001 'FOLLOW UP'
)
assert_contains "$continuation_output" 'JOB=00000003'
assert_contains "$continuation_output" 'CONTINUED:FOLLOW UP'
assert_contains "$(<"$CALLS")" '--resume 00000001-0000-4000-8000-000000000001'
assert_contains "$(tail -n 2 "$CALLS")" 'auth=clean'

hang_output=$(run_worker run -m sonnet 'HANG HERE')
assert_contains "$hang_output" 'JOB=00000004'
stop_output=$(run_worker stop 00000004)
assert_contains "$stop_output" 'Stopped 00000004'
set +e
wait_output=$(run_worker wait 00000004 2>&1)
wait_code=$?
set -e
[[ "$wait_code" == 4 ]]
assert_contains "$wait_output" 'stopped by test'

list_output=$(run_worker list)
assert_contains "$list_output" $'ID\tSTATE\tNAME\tCWD\tSESSION'
assert_contains "$list_output" $'00000003\tdone'

# Resolution must reject duplicate exact identifiers before invoking Claude,
# especially for destructive operations.
mkdir -p "$JOBS_DIR/duplicate-short"
jq '.sessionId="aaaaaaaa-0000-4000-8000-000000000001" |
  .resumeSessionId=.sessionId' \
  "$JOBS_DIR/00000001/state.json" >"$JOBS_DIR/duplicate-short/state.json"
calls_before=$(wc -l <"$CALLS")
set +e
ambiguous_stop_output=$(run_worker stop 00000001 2>&1)
ambiguous_stop_code=$?
set -e
[[ "$ambiguous_stop_code" == 1 ]]
assert_contains "$ambiguous_stop_output" 'ambiguous job ID: 00000001 matched 2 jobs'
[[ "$(wc -l <"$CALLS")" == "$calls_before" ]]

mkdir -p "$JOBS_DIR/duplicate-session"
jq '.daemonShort="000000aa"' \
  "$JOBS_DIR/00000001/state.json" >"$JOBS_DIR/duplicate-session/state.json"
calls_before=$(wc -l <"$CALLS")
set +e
ambiguous_rm_output=$(run_worker rm 00000001-0000-4000-8000-000000000001 2>&1)
ambiguous_rm_code=$?
set -e
[[ "$ambiguous_rm_code" == 1 ]]
assert_contains "$ambiguous_rm_output" \
  'ambiguous job ID: 00000001-0000-4000-8000-000000000001 matched 2 jobs'
[[ "$(wc -l <"$CALLS")" == "$calls_before" ]]

printf 'claude-worker fake tests: PASS\n'
