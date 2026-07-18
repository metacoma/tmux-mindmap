#!/usr/bin/env bash

# =============================================================================
# TERMINAL CONFIGURATION
# Edit this one line to define exactly how the terminal emulator is started.
# The launcher appends the command to run inside the terminal.
# =============================================================================
TERMINAL=(x-terminal-emulator -e)

# Common alternatives:
# TERMINAL=(gnome-terminal --)
# TERMINAL=(konsole -e)
# TERMINAL=(kitty --)
# TERMINAL=(alacritty -e)
# TERMINAL=(foot --)
# TERMINAL=(xterm -e)

# Default command executed inside the new terminal.
# Map-local scripts can override the binary path via --freeplane-tmux-bin.
FREEPLANE_TMUX_DEFAULT=(/usr/local/bin/freeplane-tmux --load)

# Keep the terminal open when startup fails so the error remains visible.
PAUSE_ON_ERROR=1

set -Eeuo pipefail

readonly INTERNAL_FLAG="--_freeplane-tmux-inside-terminal"
readonly BINARY_FLAG="--freeplane-tmux-bin"
readonly TERMINAL_FLAG="--terminal-part"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SCRIPT_PATH="${SCRIPT_DIR}/$(basename -- "${BASH_SOURCE[0]}")"
LAUNCH_LOG="${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}/freeplane-tmux-launcher.log"

FREEPLANE_TMUX=()
TERMINAL_OVERRIDE=()
FORWARD_ARGS=()
BINARY_OVERRIDE=""
INSIDE_TERMINAL=0

fail() {
    printf 'freeplane-tmux launcher: %s\n' "$*" >&2
    exit 1
}

command_exists() {
    local command_name=$1
    if [[ "$command_name" == */* ]]; then
        [[ -x "$command_name" ]]
    else
        command -v -- "$command_name" >/dev/null 2>&1
    fi
}

parse_args() {
    local argv=("$@")
    local index=0

    FREEPLANE_TMUX=()
    TERMINAL_OVERRIDE=()
    FORWARD_ARGS=()
    BINARY_OVERRIDE=""
    INSIDE_TERMINAL=0

    while (( index < ${#argv[@]} )); do
        case "${argv[index]}" in
            "$BINARY_FLAG")
                ((index + 1 < ${#argv[@]})) || fail "$BINARY_FLAG requires a path"
                BINARY_OVERRIDE="${argv[index + 1]}"
                index=$((index + 2))
                ;;
            "$TERMINAL_FLAG")
                ((index + 1 < ${#argv[@]})) || fail "$TERMINAL_FLAG requires a value"
                TERMINAL_OVERRIDE+=("${argv[index + 1]}")
                index=$((index + 2))
                ;;
            "$INTERNAL_FLAG")
                INSIDE_TERMINAL=1
                index=$((index + 1))
                ;;
            *)
                FORWARD_ARGS+=("${argv[index]}")
                index=$((index + 1))
                ;;
        esac
    done

    if [[ -n "$BINARY_OVERRIDE" ]]; then
        FREEPLANE_TMUX=("$BINARY_OVERRIDE" --load)
    else
        FREEPLANE_TMUX=("${FREEPLANE_TMUX_DEFAULT[@]}")
    fi
}

run_inside_terminal() {
    command_exists "${FREEPLANE_TMUX[0]}" || {
        printf 'Executable not found: %s\n' "${FREEPLANE_TMUX[0]}" >&2
        return 127
    }

    local status=0
    "${FREEPLANE_TMUX[@]}" "$@" || status=$?

    if ((status != 0 && PAUSE_ON_ERROR)); then
        printf '\nfreeplane-tmux exited with status %d.\n' "$status" >&2
        printf 'Press Enter to close this terminal...' >&2
        read -r _ </dev/tty || true
    fi

    return "$status"
}

parse_args "$@"

if (( INSIDE_TERMINAL )); then
    run_inside_terminal "${FORWARD_ARGS[@]}"
    exit $?
fi

((${#TERMINAL[@]} > 0)) || fail 'TERMINAL is empty'

SELECTED_TERMINAL=("${TERMINAL[@]}")
if ((${#TERMINAL_OVERRIDE[@]} > 0)); then
    SELECTED_TERMINAL=("${TERMINAL_OVERRIDE[@]}")
fi

command_exists "${SELECTED_TERMINAL[0]}" || fail \
    "terminal executable not found: ${SELECTED_TERMINAL[0]}"

LAUNCH_ARGS=()
if [[ -n "$BINARY_OVERRIDE" ]]; then
    LAUNCH_ARGS+=("$BINARY_FLAG" "$BINARY_OVERRIDE")
fi
for terminal_part in "${TERMINAL_OVERRIDE[@]}"; do
    LAUNCH_ARGS+=("$TERMINAL_FLAG" "$terminal_part")
done
LAUNCH_ARGS+=("${FORWARD_ARGS[@]}")

# Freeplane must not wait for the terminal window to close. Errors produced before
# the terminal starts are retained in LAUNCH_LOG for troubleshooting.
nohup "${SELECTED_TERMINAL[@]}" "$SCRIPT_PATH" "$INTERNAL_FLAG" \
    "${LAUNCH_ARGS[@]}" >>"$LAUNCH_LOG" 2>&1 &
