from __future__ import annotations

import re
import shlex
from collections.abc import Sequence

from .models import PaneSpec

SSH_OPTIONS_WITH_ARGUMENT = {
    "-b",
    "-c",
    "-D",
    "-E",
    "-e",
    "-F",
    "-I",
    "-i",
    "-J",
    "-L",
    "-l",
    "-m",
    "-O",
    "-o",
    "-p",
    "-Q",
    "-R",
    "-S",
    "-W",
    "-w",
}
SUDO_OPTIONS_WITH_ARGUMENT = {"-a", "-C", "-g", "-h", "-p", "-r", "-T", "-t", "-u"}


def _has_ssh_tty_option(tokens: Sequence[str]) -> bool:
    for token in tokens[1:]:
        if token == "--":
            break
        if token.startswith("-") and re.fullmatch(r"-[A-Za-z]*t[A-Za-z]*", token):
            return True
    return False


def _shell_rc(env: dict[str, str], aliases: dict[str, str]) -> str:
    lines = [f"export {key}={shlex.quote(value)}" for key, value in env.items()]
    if aliases:
        lines.append("shopt -s expand_aliases")
        lines.extend(f"alias {name}={shlex.quote(body)}" for name, body in aliases.items())
    return "\n".join(lines)


def build_shell_bootstrap(env: dict[str, str], aliases: dict[str, str]) -> str:
    rc_content = _shell_rc(env, aliases)
    return (
        "tmp=$(mktemp)\n"
        "trap 'rm -f \"$tmp\"' EXIT\n"
        "cat >\"$tmp\" <<'FREEPLANE_TMUX_RC'\n"
        f"{rc_content}\n"
        "FREEPLANE_TMUX_RC\n"
        'bash --noprofile --rcfile "$tmp" -i'
    )


def rewrite_ssh_command(
    command: str,
    env: dict[str, str],
    aliases: dict[str, str],
) -> str:
    if not env and not aliases:
        return command
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return command
    if not tokens or tokens[0] != "ssh":
        return command

    host_index: int | None = None
    expect_argument = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if expect_argument:
            expect_argument = False
            index += 1
            continue
        if token == "--":
            host_index = index + 1 if index + 1 < len(tokens) else None
            index += 2
            break
        if token.startswith("-") and token != "-":
            if token in SSH_OPTIONS_WITH_ARGUMENT:
                expect_argument = True
            index += 1
            continue
        host_index = index
        index += 1
        break

    if host_index is None or index < len(tokens):
        return command

    rewritten = list(tokens)
    if not _has_ssh_tty_option(tokens):
        rewritten.insert(1, "-tt")
    rewritten.append(build_shell_bootstrap(env, aliases))
    return shlex.join(rewritten)


def rewrite_sudo_command(
    command: str,
    env: dict[str, str],
    aliases: dict[str, str],
) -> str:
    if not env and not aliases:
        return command
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return command
    if not tokens or tokens[0] != "sudo":
        return command

    command_index = len(tokens)
    expect_argument = False
    shell_flag = False
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if expect_argument:
            expect_argument = False
            index += 1
            continue
        if token == "--":
            command_index = index + 1
            break
        if token.startswith("-") and token != "-":
            if "i" in token[1:] or "s" in token[1:]:
                shell_flag = True
            if token in SUDO_OPTIONS_WITH_ARGUMENT:
                expect_argument = True
            index += 1
            continue
        command_index = index
        break

    command_tokens = tokens[command_index:]
    opens_shell = shell_flag and not command_tokens
    if command_tokens:
        executable = command_tokens[0].rsplit("/", 1)[-1]
        shell_commands = {"bash", "dash", "fish", "ksh", "sh", "zsh"}
        opens_shell = executable in shell_commands and "-c" not in command_tokens[1:]
        opens_shell = opens_shell or executable == "su"

    if not opens_shell:
        return command

    env_assignments = [f"{key}={value}" for key, value in env.items()]
    bootstrap = build_shell_bootstrap(env, aliases)
    rewritten = [
        *tokens[:command_index],
        "env",
        *env_assignments,
        "bash",
        "-lc",
        bootstrap,
    ]
    return shlex.join(rewritten)


def inject_context_into_transition(
    command: str,
    env: dict[str, str],
    aliases: dict[str, str],
) -> str:
    stripped = command.lstrip()
    if stripped == "ssh" or stripped.startswith("ssh "):
        return rewrite_ssh_command(command, env, aliases)
    if stripped == "sudo" or stripped.startswith("sudo "):
        return rewrite_sudo_command(command, env, aliases)
    return command


def _env_sync(current: dict[str, str], target: dict[str, str]) -> list[str]:
    commands = [f"unset {key}" for key in sorted(set(current) - set(target))]
    commands.extend(
        f"export {key}={shlex.quote(value)}"
        for key, value in target.items()
        if current.get(key) != value
    )
    return commands


def _alias_sync(current: dict[str, str], target: dict[str, str]) -> list[str]:
    commands = [
        f"unalias {name} 2>/dev/null || true" for name in sorted(set(current) - set(target))
    ]
    if target and target != current:
        commands.append("shopt -s expand_aliases")
    commands.extend(
        f"alias {name}={shlex.quote(body)}"
        for name, body in target.items()
        if current.get(name) != body
    )
    return commands


def _title_commands(title: str | None) -> list[str]:
    if not title:
        return []
    return ["printf '\\033]2;%s\\033\\\\' " + shlex.quote(title)]


def pane_shell_commands(pane: PaneSpec) -> list[str]:
    commands: list[str] = []
    current_env: dict[str, str] = {}
    current_aliases: dict[str, str] = {}

    commands.extend(_env_sync(current_env, pane.base_scope.env))
    current_env = dict(pane.base_scope.env)
    commands.extend(_alias_sync(current_aliases, pane.base_scope.aliases))
    current_aliases = dict(pane.base_scope.aliases)
    commands.extend(_title_commands(pane.title))

    for pre_command in pane.base_scope.pre:
        commands.append(inject_context_into_transition(pre_command, current_env, current_aliases))
    previous_pre = pane.base_scope.pre

    for step in pane.steps:
        commands.extend(_env_sync(current_env, step.effective_scope.env))
        current_env = dict(step.effective_scope.env)
        commands.extend(_alias_sync(current_aliases, step.effective_scope.aliases))
        current_aliases = dict(step.effective_scope.aliases)

        current_pre = step.effective_scope.pre
        common_prefix = 0
        for previous, current in zip(previous_pre, current_pre, strict=False):
            if previous != current:
                break
            common_prefix += 1
        for pre_command in current_pre[common_prefix:]:
            commands.append(
                inject_context_into_transition(pre_command, current_env, current_aliases)
            )

        commands.append(inject_context_into_transition(step.command, current_env, current_aliases))
        previous_pre = current_pre

    return commands or ["bash"]
