#!/usr/bin/env bash
# worker.sh — thin wrapper that lets an orchestrator drive an opencode worker.
#
# The orchestrator keeps the judgment; this script submits well-specified briefs to
# the configured worker model running inside opencode and returns output plus session id.
#
# Verbs:
#   worker.sh run   "<brief>"              Fresh EPHEMERAL worker (clean context). Prints SESSION=<id>.
#   worker.sh cont  <session-id> "<brief>" Continue a PERSISTENT worker (keeps its accumulated context).
#   worker.sh last  "<brief>"              Continue the most-recent session (-c).
#   worker.sh fork  <session-id> "<brief>" Branch a session (explore without polluting the original).
#   worker.sh list                         List live worker sessions (id + title).
#   worker.sh stats                        Token usage + cost so far.
#   worker.sh kill  <session-id>           Delete a session.
#   worker.sh serve [port]                 Start a persistent backend (warm MCP) to --attach to.
#
# Env knobs:
#   WORKER_MODEL   default: glm/glm-5.2   (e.g. glm/glm-5-turbo for trivial tasks)
#   WORKER_DIR     default: $PWD          (project the worker operates in)
#   WORKER_EFFORT  default: (unset)       reasoning effort variant: minimal|low|medium|high|max
#   WORKER_ATTACH  default: (unset)       http://localhost:PORT of a `serve` backend to reuse
#   OPENCODE_KEYFILE default: ~/.config/opencode/zai.key
#
# The worker runs with --dangerously-skip-permissions so it does NOT block on approval
# prompts. That auto-approve is scoped to THIS invocation only; your interactive
# opencode config is untouched. The worker is confined to WORKER_DIR — always point it
# at a project dir, and always review its diff (that's the orchestrator's job).

set -euo pipefail

MODEL="${WORKER_MODEL:-glm/glm-5.2}"
DIR="${WORKER_DIR:-$PWD}"
KEYFILE="${OPENCODE_KEYFILE:-$HOME/.config/opencode/zai.key}"

# --- API key (only needed for verbs that actually call the model) ----------
require_key() {
  [[ -n "${ZAI_API_KEY:-}" ]] && return 0
  if [[ -f "$KEYFILE" ]]; then
    ZAI_API_KEY="$(tr -d '[:space:]' < "$KEYFILE")"; export ZAI_API_KEY
  else
    echo "worker.sh: no API key. Set ZAI_API_KEY or write it to $KEYFILE" >&2
    echo "  Get a key: https://z.ai/manage-apikey/apikey-list (GLM Coding Plan)" >&2
    exit 1
  fi
}

# --- assemble common opencode run flags ------------------------------------
common_flags=( run -m "$MODEL" --dir "$DIR" --dangerously-skip-permissions )
[[ -n "${WORKER_EFFORT:-}" ]] && common_flags+=( --variant "$WORKER_EFFORT" )
[[ -n "${WORKER_ATTACH:-}" ]] && common_flags+=( --attach "$WORKER_ATTACH" )

# Run opencode, tee output so the orchestrator sees it, then resolve the session
# id from the freshest session (the one we just created/continued).
run_and_report() {
  local logf; logf="$(mktemp -t worker.XXXXXX.log)"
  # shellcheck disable=SC2068
  opencode ${common_flags[@]} "$@" 2>&1 | tee "$logf"
  local sid
  sid="$(opencode session list --json 2>/dev/null \
        | grep -oE '"id"[[:space:]]*:[[:space:]]*"[^"]+"' | head -1 \
        | sed -E 's/.*"([^"]+)"$/\1/')" || true
  echo
  echo "----------------------------------------------------------------------"
  [[ -n "$sid" ]] && echo "SESSION=$sid   (reuse with: worker.sh cont $sid \"...\")"
  echo "MODEL=$MODEL  DIR=$DIR  LOG=$logf"
}

cmd="${1:-}"; shift || true
case "$cmd" in
  run)   require_key; run_and_report "$@" ;;
  cont)  require_key; sid="$1"; shift; common_flags+=( -s "$sid" ); run_and_report "$@" ;;
  last)  require_key; common_flags+=( -c ); run_and_report "$@" ;;
  fork)  require_key; sid="$1"; shift; common_flags+=( -s "$sid" --fork ); run_and_report "$@" ;;
  serve) require_key; opencode serve --port "${1:-4096}"; exit 0 ;;
  list)  opencode session list ;;
  stats) opencode stats ;;
  kill)  opencode session delete "$1" ;;
  ""|-h|--help)
    sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) echo "worker.sh: unknown verb '$cmd' (try: run|cont|last|fork|list|stats|kill|serve)" >&2; exit 2 ;;
esac
