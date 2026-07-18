#!/usr/bin/env bash
# claude-worker — foreground lifecycle wrapper for Claude Code background agents.
#
# Exit codes:
#   0  command succeeded, or a waited-for job finished successfully
#   1  CLI, lookup, or state error
#   2  invalid usage
#   3  waited-for job failed
#   4  waited-for job was stopped or cancelled
#   130/143  interrupted; the target job is stopped before the wrapper exits
set -uo pipefail

CLAUDE_BIN="${CLAUDE_BIN:-claude}"
CLAUDE_STATE_ROOT="${CLAUDE_CONFIG_DIR:-${HOME:?HOME is not set}/.claude}"
CLAUDE_JOBS_DIR="${CLAUDE_JOBS_DIR:-$CLAUDE_STATE_ROOT/jobs}"
POLL_INTERVAL="${CLAUDE_WORKER_POLL_INTERVAL:-1}"
WAIT_JOB_ID=""

usage() {
  cat <<'USAGE'
Usage: claude-worker.sh <command> [arguments]

Commands:
  run [--wait] [-C dir] -m model [--name label]
      [--permission-mode mode] [--subscription] <brief|->
      Start a new Claude background agent. A brief of '-' is read from stdin.
      The model is required; permission mode defaults to dontAsk.

  cont [--wait] [--subscription] <id> <prompt|->
                                 Continue a conversation as a new job.
  list                           List Claude background agents as TSV.
  status <id>                    Show a job's current state and metadata.
  last <id>                      Print output.result, falling back to detail/logs.
  logs <id>                      Print recent output through Claude Code.
  stop <id>                      Stop a job but preserve its conversation.
  rm <id>                        Delete a job through Claude Code.
  wait <id>                      Block until a job is done, failed, or stopped.

IDs may be the eight-character daemon ID or the full session UUID. run and cont
print JOB=<new-short-id>. cont always returns the newly created job ID.

--subscription removes ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, and
ANTHROPIC_BASE_URL only from the spawned Claude launch command so subscription
authentication can win. Credential values are never printed.
USAGE
}

usage_error() {
  printf 'claude-worker: %s\n' "$*" >&2
  printf 'Try: %s --help\n' "$0" >&2
  exit 2
}

fail() {
  printf 'claude-worker: %s\n' "$*" >&2
  exit 1
}

require_value() {
  local option="$1" value="${2:-}"
  [[ -n "$value" ]] || usage_error "$option requires a value"
}

validate_id() {
  local id="$1"
  [[ -n "$id" ]] || usage_error "missing job ID"
  if [[ ! "$id" =~ ^[[:xdigit:]]{8}$ && \
        ! "$id" =~ ^[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}$ ]]; then
    usage_error "invalid job ID (expected 8-character daemon ID or full session UUID): $id"
  fi
}

read_prompt() {
  local argument="$1" prompt
  if [[ "$argument" == "-" ]]; then
    prompt=$(</dev/stdin)
  else
    prompt="$argument"
  fi
  [[ -n "$prompt" ]] || usage_error "prompt must not be empty"
  printf '%s' "$prompt"
}

run_claude() {
  local auth_mode="$1"
  shift
  if [[ "$auth_mode" == "subscription" ]]; then
    env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL \
      "$CLAUDE_BIN" "$@"
  else
    "$CLAUDE_BIN" "$@"
  fi
}

agents_json() {
  local output
  if ! output=$(run_claude default agents --json --all 2>/dev/null); then
    return 1
  fi
  jq -e 'if type == "array" then . else error("not an array") end' <<<"$output" 2>/dev/null
}

state_json_for_id() {
  local id="$1" all matches count
  local -a state_files=()

  shopt -s nullglob
  state_files=("$CLAUDE_JOBS_DIR"/*/state.json)
  shopt -u nullglob
  if (( ${#state_files[@]} > 0 )); then
    matches=$(jq -cs --arg id "$id" '[.[] | select(
      (.daemonShort // "") == $id or
      (.id // "") == $id or
      (.sessionId // .resumeSessionId // "") == $id
    )]' "${state_files[@]}" 2>/dev/null) || return 1
    count=$(jq -r 'length' <<<"$matches")
    if (( count == 1 )); then
      jq -c '.[0]' <<<"$matches"
      return 0
    elif (( count > 1 )); then
      printf 'claude-worker: ambiguous job ID: %s matched %d jobs\n' "$id" "$count" >&2
      return 1
    fi
  fi

  all=$(agents_json) || return 1
  matches=$(jq -c --arg id "$id" '[.[] | select(
    (.daemonShort // "") == $id or
    (.id // "") == $id or
    (.sessionId // .resumeSessionId // "") == $id
  )]' <<<"$all" 2>/dev/null) || return 1
  count=$(jq -r 'length' <<<"$matches")
  if (( count == 1 )); then
    jq -c '.[0]' <<<"$matches"
    return 0
  elif (( count > 1 )); then
    printf 'claude-worker: ambiguous job ID: %s matched %d jobs\n' "$id" "$count" >&2
  fi
  return 1
}

short_id_for() {
  local id="$1" state_json short
  if state_json=$(state_json_for_id "$id"); then
    short=$(jq -r '.daemonShort // .id // ((.sessionId // "")[0:8]) // empty' <<<"$state_json")
    [[ "$short" =~ ^[[:xdigit:]]{8}$ ]] && printf '%s\n' "$short" && return 0
  fi
  return 1
}

full_session_for() {
  local id="$1" state_json session
  state_json=$(state_json_for_id "$id") || return 1
  session=$(jq -r '.sessionId // .resumeSessionId // empty' <<<"$state_json")
  [[ -n "$session" ]] || return 1
  printf '%s\n' "$session"
}

cwd_for() {
  local id="$1" state_json cwd
  state_json=$(state_json_for_id "$id") || return 1
  cwd=$(jq -r '.cwd // empty' <<<"$state_json")
  [[ -n "$cwd" ]] || return 1
  printf '%s\n' "$cwd"
}

parse_launch_id() {
  local output="$1" excluded="${2:-}" candidate short
  while IFS= read -r candidate; do
    short="${candidate:0:8}"
    [[ -n "$excluded" && ( "$candidate" == "$excluded" || "$short" == "${excluded:0:8}" ) ]] && continue
    printf '%s\n' "$short"
    return 0
  done < <(
    printf '%s\n' "$output" \
      | sed $'s/\033\\[[0-9;]*[[:alpha:]]//g' \
      | grep -Eio '[[:xdigit:]]{8}(-[[:xdigit:]]{4}){3}-[[:xdigit:]]{12}|[[:xdigit:]]{8}' \
      || true
  )
  return 1
}

newest_background_id() {
  local cwd="$1" name="${2:-}" excluded="${3:-}" all
  all=$(agents_json) || return 1
  jq -r --arg cwd "$cwd" --arg name "$name" --arg excluded "$excluded" '
    [ .[]
      | select((.kind // "background") == "background")
      | select((.cwd // "") == $cwd)
      | select($name == "" or (.name // "") == $name)
      | . + {short: (.id // ((.sessionId // "")[0:8]))}
      | select(.short != "" and .short != ($excluded[0:8]))
    ]
    | sort_by(.startedAt // 0)
    | last
    | .short // empty
  ' <<<"$all"
}

launch_and_report() {
  local auth_mode="$1" cwd="$2" name="$3" excluded="$4"
  shift 4
  local output code job_id

  if output=$(run_claude "$auth_mode" "$@" 2>&1); then
    code=0
  else
    code=$?
  fi
  if (( code != 0 )); then
    printf '%s\n' "$output" >&2
    printf 'claude-worker: Claude launch failed (exit %d)\n' "$code" >&2
    return "$code"
  fi

  job_id=$(parse_launch_id "$output" "$excluded") || \
    job_id=$(newest_background_id "$cwd" "$name" "$excluded") || true
  if [[ -z "$job_id" ]]; then
    printf '%s\n' "$output" >&2
    printf 'claude-worker: launch succeeded but no background job ID was found\n' >&2
    return 1
  fi
  printf 'JOB=%s\n' "$job_id"
  printf '%s\n' "$job_id"
}

show_last() {
  local id="$1" state_json short detail
  if state_json=$(state_json_for_id "$id"); then
    if jq -e '(.output | type) == "object" and (.output | has("result"))' \
      <<<"$state_json" >/dev/null 2>&1; then
      jq -r '.output.result | if type == "string" then . else tojson end' <<<"$state_json"
      return 0
    fi
    detail=$(jq -r '.detail // empty' <<<"$state_json")
    [[ -n "$detail" ]] && printf 'DETAIL=%s\n' "$detail" >&2
  fi

  short=$(short_id_for "$id") || {
    printf 'claude-worker: cannot resolve daemon ID for job: %s\n' "$id" >&2
    return 1
  }
  if ! run_claude default logs "$short"; then
    printf 'claude-worker: no result or readable logs for %s\n' "$id" >&2
    return 1
  fi
}

interrupt_wait() {
  local exit_code="$1" signal_name="$2"
  trap - INT TERM
  if [[ -n "$WAIT_JOB_ID" ]]; then
    run_claude default stop "$WAIT_JOB_ID" >/dev/null 2>&1 || true
    printf 'claude-worker: %s received; stopped job %s\n' "$signal_name" "$WAIT_JOB_ID" >&2
  fi
  exit "$exit_code"
}

wait_for_job() {
  local id="$1" state_json state detail short
  short=$(short_id_for "$id") || {
    printf 'claude-worker: cannot resolve daemon ID for job: %s\n' "$id" >&2
    return 1
  }
  WAIT_JOB_ID="$short"
  trap 'interrupt_wait 130 INT' INT
  trap 'interrupt_wait 143 TERM' TERM

  while true; do
    if ! state_json=$(state_json_for_id "$id"); then
      trap - INT TERM
      WAIT_JOB_ID=""
      printf 'claude-worker: job not found: %s\n' "$id" >&2
      return 1
    fi
    state=$(jq -r '.state // .status // "unknown"' <<<"$state_json")
    case "$state" in
      done|complete|completed)
        trap - INT TERM
        WAIT_JOB_ID=""
        show_last "$id" || true
        return 0
        ;;
      failed|error)
        detail=$(jq -r '.detail // .error // empty' <<<"$state_json")
        [[ -n "$detail" ]] && printf 'DETAIL=%s\n' "$detail" >&2
        show_last "$id" || true
        trap - INT TERM
        WAIT_JOB_ID=""
        return 3
        ;;
      stopped|cancelled|canceled|killed)
        detail=$(jq -r '.detail // empty' <<<"$state_json")
        [[ -n "$detail" ]] && printf 'DETAIL=%s\n' "$detail" >&2
        trap - INT TERM
        WAIT_JOB_ID=""
        return 4
        ;;
      working|running|starting|queued|waiting|idle|blocked)
        sleep "$POLL_INTERVAL"
        ;;
      *)
        trap - INT TERM
        WAIT_JOB_ID=""
        printf 'claude-worker: unrecognized state %q for job %s\n' "$state" "$id" >&2
        return 1
        ;;
    esac
  done
}

command -v "$CLAUDE_BIN" >/dev/null 2>&1 || fail "Claude CLI not found: $CLAUDE_BIN"
command -v jq >/dev/null 2>&1 || fail "jq not found"

verb="${1:-}"
[[ -n "$verb" ]] || { usage; exit 2; }
shift || true

case "$verb" in
  -h|--help|help)
    usage
    ;;

  run)
    should_wait=false
    workdir="$PWD"
    model=""
    name=""
    permission_mode="dontAsk"
    auth_mode="default"
    while (( $# > 0 )); do
      case "$1" in
        --wait) should_wait=true; shift ;;
        -C) require_value "$1" "${2:-}"; workdir="$2"; shift 2 ;;
        -m|--model) require_value "$1" "${2:-}"; model="$2"; shift 2 ;;
        --name) require_value "$1" "${2:-}"; name="$2"; shift 2 ;;
        --permission-mode) require_value "$1" "${2:-}"; permission_mode="$2"; shift 2 ;;
        --subscription) auth_mode="subscription"; shift ;;
        --) shift; break ;;
        -*) usage_error "run: unknown option $1" ;;
        *) break ;;
      esac
    done
    [[ -n "$model" ]] || usage_error "run: -m/--model is required"
    (( $# == 1 )) || usage_error "run: expected exactly one <brief|->"
    [[ -d "$workdir" ]] || usage_error "run: directory does not exist: $workdir"
    workdir=$(cd -- "$workdir" && pwd -P) || fail "cannot enter directory: $workdir"
    brief=$(read_prompt "$1")
    cd -- "$workdir" || fail "cannot enter directory: $workdir"

    launch_args=(--bg --model "$model" --permission-mode "$permission_mode")
    [[ -n "$name" ]] && launch_args+=(--name "$name")
    launch_args+=(-- "$brief")
    launch_report=$(launch_and_report "$auth_mode" "$workdir" "$name" "" "${launch_args[@]}") || exit $?
    job_id=$(tail -n 1 <<<"$launch_report")
    head -n 1 <<<"$launch_report"
    if [[ "$should_wait" == true ]]; then
      wait_for_job "$job_id"
      exit $?
    fi
    ;;

  cont)
    should_wait=false
    auth_mode="default"
    while (( $# > 0 )); do
      case "$1" in
        --wait) should_wait=true; shift ;;
        --subscription) auth_mode="subscription"; shift ;;
        --) shift; break ;;
        -*) usage_error "cont: unknown option $1" ;;
        *) break ;;
      esac
    done
    (( $# == 2 )) || usage_error "cont: expected [--wait] [--subscription] <id> <prompt|->"
    validate_id "$1"
    original_id="$1"
    prompt=$(read_prompt "$2")
    full_session=$(full_session_for "$original_id") || fail "cannot resolve session for job: $original_id"
    workdir=$(cwd_for "$original_id") || fail "cannot resolve working directory for job: $original_id"
    [[ -d "$workdir" ]] || fail "continuation working directory no longer exists: $workdir"
    old_short=$(short_id_for "$original_id") || fail "cannot resolve daemon ID for job: $original_id"
    cd -- "$workdir" || fail "cannot enter directory: $workdir"

    launch_report=$(launch_and_report "$auth_mode" "$workdir" "" "$old_short" \
      --resume "$full_session" --bg -- "$prompt") || exit $?
    job_id=$(tail -n 1 <<<"$launch_report")
    head -n 1 <<<"$launch_report"
    if [[ "$should_wait" == true ]]; then
      wait_for_job "$job_id"
      exit $?
    fi
    ;;

  list)
    (( $# == 0 )) || usage_error "list: takes no arguments"
    all=$(agents_json) || fail "could not read Claude agent registry"
    printf 'ID\tSTATE\tNAME\tCWD\tSESSION\n'
    jq -r '.[] | select((.kind // "background") == "background") |
      [(.id // ((.sessionId // "")[0:8])), (.state // .status // "unknown"),
       (.name // ""), (.cwd // ""), (.sessionId // "")] | @tsv' <<<"$all"
    ;;

  status)
    (( $# == 1 )) || usage_error "status: expected <id>"
    validate_id "$1"
    state_json=$(state_json_for_id "$1") || fail "job not found: $1"
    jq -r '
      "ID=" + (.daemonShort // .id // ((.sessionId // "")[0:8]) // "unknown"),
      "STATE=" + (.state // .status // "unknown"),
      "SESSION=" + (.sessionId // .resumeSessionId // "unknown"),
      "NAME=" + (.name // ""),
      "CWD=" + (.cwd // ""),
      (if (.detail // "") != "" then "DETAIL=" + .detail else empty end)
    ' <<<"$state_json"
    ;;

  last)
    (( $# == 1 )) || usage_error "last: expected <id>"
    validate_id "$1"
    show_last "$1"
    ;;

  logs|stop|rm)
    (( $# == 1 )) || usage_error "$verb: expected <id>"
    validate_id "$1"
    short=$(short_id_for "$1") || fail "cannot resolve daemon ID for job: $1"
    run_claude default "$verb" "$short"
    ;;

  wait)
    (( $# == 1 )) || usage_error "wait: expected <id>"
    validate_id "$1"
    wait_for_job "$1"
    ;;

  *)
    usage_error "unknown command: $verb"
    ;;
esac
