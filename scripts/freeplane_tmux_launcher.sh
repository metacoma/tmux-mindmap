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

# Command executed inside the new terminal. An absolute path is deliberate:
# GUI applications often start with a smaller PATH than an interactive shell.
FREEPLANE_TMUX=(/usr/local/bin/freeplane-tmux --load)

# Keep the terminal open when startup fails so the error remains visible.
PAUSE_ON_ERROR=1

set -Eeuo pipefail

readonly INTERNAL_FLAG="--_freeplane-tmux-inside-terminal"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SCRIPT_PATH="${SCRIPT_DIR}/$(basename -- "${BASH_SOURCE[0]}")"
LAUNCH_LOG="${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}/freeplane-tmux-launcher.log"

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

if [[ "${1:-}" == "$INTERNAL_FLAG" ]]; then
    shift
    run_inside_terminal "$@"
    exit $?
fi

((${#TERMINAL[@]} > 0)) || fail 'TERMINAL is empty'
command_exists "${TERMINAL[0]}" || fail \
    "terminal executable not found: ${TERMINAL[0]} (edit TERMINAL at the top of $SCRIPT_PATH)"

# Freeplane must not wait for the terminal window to close. Errors produced before
# the terminal starts are retained in LAUNCH_LOG for troubleshooting.
nohup "${TERMINAL[@]}" "$SCRIPT_PATH" "$INTERNAL_FLAG" "$@" \
    >>"$LAUNCH_LOG" 2>&1 &
