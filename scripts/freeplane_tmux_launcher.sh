#!/usr/bin/env bash
set -Eeuo pipefail

BINARY="/usr/local/bin/freeplane-tmux"
TERMINAL_ARGS=()
FORWARD_ARGS=()

while (($#)); do
  case "$1" in
    --freeplane-tmux-bin)
      shift
      [[ $# -gt 0 ]] || { echo "--freeplane-tmux-bin requires a path" >&2; exit 2; }
      BINARY="$1"
      ;;
    --terminal-command-b64)
      shift
      [[ $# -gt 0 ]] || { echo "--terminal-command-b64 requires a value" >&2; exit 2; }
      TERMINAL_ARGS+=("--terminal-command-b64=$1")
      ;;
    --terminal-part)
      shift
      [[ $# -gt 0 ]] || { echo "--terminal-part requires a value" >&2; exit 2; }
      TERMINAL_ARGS+=("--terminal-part=$1")
      ;;
    *)
      FORWARD_ARGS+=("$1")
      ;;
  esac
  shift
done

exec "$BINARY" --_launch-gui-terminal "${FORWARD_ARGS[@]}" "${TERMINAL_ARGS[@]}"
