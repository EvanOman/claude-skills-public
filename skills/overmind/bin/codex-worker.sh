#!/usr/bin/env bash
# codex-worker — scriptable delegation interface to OpenAI Codex CLI (`codex exec`).
# Used by the codex-worker skill: the orchestrator (Claude) writes briefs, this script
# runs them on a Codex worker and returns SESSION id + final message + token usage.
#
# Verbs:
#   run  [-C dir] [-m model] [-p profile] [-s sandbox] [--full-access] [--schema f] [--label name] <brief|->
#   cont <session-id> <prompt|->        continue an existing worker session
#   last <session-id>                   print the worker's final message from its last turn
#   log  <session-id>                   print path to the raw JSONL event log
#   list                                list tracked worker sessions (newest last)
#
# Brief passed as '-' is read from stdin (preferred for multi-line briefs).
set -uo pipefail

STATE_DIR="${CODEX_WORKER_STATE:-$HOME/.cache/codex-worker}"
mkdir -p "$STATE_DIR"
REGISTRY="$STATE_DIR/registry.tsv"

die() { echo "codex-worker: $*" >&2; exit 1; }

command -v codex >/dev/null || die "codex CLI not found (bun install -g @openai/codex)"
command -v jq >/dev/null || die "jq not found"

extract_and_report() {
  # $1 = jsonl log file, $2 = stderr file, $3 = codex exit code, $4 = label, $5 = workdir
  local log="$1" errf="$2" code="$3" label="$4" workdir="$5"
  local sid
  sid=$(jq -r 'select(.type=="thread.started") | .thread_id' "$log" 2>/dev/null | head -1)
  if [[ -z "$sid" ]]; then
    echo "codex-worker: run failed before a session started (exit $code). stderr:" >&2
    tail -20 "$errf" >&2
    exit "${code:-1}"
  fi
  mv "$log" "$STATE_DIR/$sid.jsonl" 2>/dev/null || cp "$log" "$STATE_DIR/$sid.jsonl"
  mv "$errf" "$STATE_DIR/$sid.err" 2>/dev/null || true
  jq -rs '[.[] | select(.type=="item.completed") | .item | select(.type=="agent_message")] | (last.text // "")' \
    "$STATE_DIR/$sid.jsonl" > "$STATE_DIR/$sid.last.md"
  local usage
  usage=$(jq -r 'select(.type=="turn.completed") | .usage | "in=\(.input_tokens) cached=\(.cached_input_tokens) out=\(.output_tokens)"' \
    "$STATE_DIR/$sid.jsonl" | tail -1)
  printf '%s\t%s\t%s\t%s\t%s\n' "$(date -Iseconds)" "$sid" "$label" "$workdir" "exit=$code $usage" >> "$REGISTRY"
  echo "SESSION=$sid"
  echo "EXIT=$code  TOKENS: ${usage:-unknown}"
  echo "--- final message ---"
  cat "$STATE_DIR/$sid.last.md"
  exit "$code"
}

verb="${1:-}"; shift || true

case "$verb" in
  run)
    workdir="$PWD" model="" profile="worker" sandbox="" schema="" label="task" extra=()
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -C) workdir="$2"; shift 2 ;;
        -m) model="$2"; shift 2 ;;
        -p) profile="$2"; shift 2 ;;
        -s) sandbox="$2"; shift 2 ;;
        --full-access) sandbox="danger-full-access"; shift ;;
        --schema) schema="$2"; shift 2 ;;
        --label) label="$2"; shift 2 ;;
        --) shift; extra+=("$@"); break ;;
        *) break ;;
      esac
    done
    brief="${1:-}"
    [[ -n "$brief" ]] || die "run: missing brief (use '-' to read stdin)"
    args=(exec -p "$profile" -C "$workdir" --skip-git-repo-check --json
          -c 'skills.enabled=false')
    [[ -n "$model" ]] && args+=(-m "$model")
    [[ -n "$sandbox" ]] && args+=(-s "$sandbox")
    [[ -n "$schema" ]] && args+=(--output-schema "$schema")
    [[ ${#extra[@]} -gt 0 ]] && args+=("${extra[@]}")
    log="$STATE_DIR/pending.$$.jsonl"; errf="$STATE_DIR/pending.$$.err"
    if [[ "$brief" == "-" ]]; then
      codex "${args[@]}" - > "$log" 2> "$errf"
    else
      printf '%s' "$brief" | codex "${args[@]}" - > "$log" 2> "$errf"
    fi
    extract_and_report "$log" "$errf" "$?" "$label" "$workdir"
    ;;

  cont)
    sid="${1:-}"; shift || true
    prompt="${1:-}"
    [[ -n "$sid" && -n "$prompt" ]] || die "usage: cont <session-id> <prompt|->"
    log="$STATE_DIR/pending.$$.jsonl"; errf="$STATE_DIR/pending.$$.err"
    if [[ "$prompt" == "-" ]]; then
      codex exec resume "$sid" --json --skip-git-repo-check -c 'skills.enabled=false' - > "$log" 2> "$errf"
    else
      printf '%s' "$prompt" | codex exec resume "$sid" --json --skip-git-repo-check -c 'skills.enabled=false' - > "$log" 2> "$errf"
    fi
    code=$?
    # resume re-reports the same thread id; reuse extract path (appends registry row)
    label=$(awk -F'\t' -v s="$sid" '$2==s {print $3}' "$REGISTRY" 2>/dev/null | tail -1)
    workdir=$(awk -F'\t' -v s="$sid" '$2==s {print $4}' "$REGISTRY" 2>/dev/null | tail -1)
    extract_and_report "$log" "$errf" "$code" "${label:-cont}" "${workdir:-$PWD}"
    ;;

  last)
    sid="${1:-}"; [[ -n "$sid" ]] || die "usage: last <session-id>"
    cat "$STATE_DIR/$sid.last.md"
    ;;

  log)
    sid="${1:-}"; [[ -n "$sid" ]] || die "usage: log <session-id>"
    echo "$STATE_DIR/$sid.jsonl"
    ;;

  list)
    [[ -f "$REGISTRY" ]] && column -t -s$'\t' "$REGISTRY" || echo "no sessions yet"
    ;;

  *)
    sed -n '2,12p' "$0"
    exit 1
    ;;
esac
