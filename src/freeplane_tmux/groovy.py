from __future__ import annotations

import shlex
from collections.abc import Sequence

DEFAULT_TERMINAL_COMMAND = "x-terminal-emulator -e"
RUNTIME_LOG_NAME = "freeplane-tmux.log"


def _groovy_string(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f"'{escaped}'"


def _groovy_list(values: Sequence[str]) -> str:
    return "[" + ", ".join(_groovy_string(value) for value in values) + "]"


def parse_terminal_command(command: str | None) -> list[str]:
    raw_command = command if command is not None else DEFAULT_TERMINAL_COMMAND
    try:
        parts = shlex.split(raw_command)
    except ValueError as exc:
        raise ValueError(f"invalid create-terminal: {exc}") from exc
    if not parts:
        raise ValueError("create-terminal command must not be empty")
    return parts


def build_root_script(
    *,
    terminal_command: str | None,
    load_command: Sequence[str],
) -> str:
    terminal_parts = parse_terminal_command(terminal_command)
    normalized_load_command = [str(part) for part in load_command]
    if not normalized_load_command:
        raise ValueError("load command must not be empty")
    if not normalized_load_command[0].strip():
        raise ValueError("load command executable must not be empty")

    terminal_literal = _groovy_list(terminal_parts)
    load_literal = _groovy_list(normalized_load_command)
    log_name_literal = _groovy_string(RUNTIME_LOG_NAME)

    return f"""
// @ExecutionModes({{ON_SELECTED_NODE}})
// @Permission_granted EXEC("execute external process")
// @Permission_granted READ("read files")


def terminalCommand = {terminal_literal}
def loadCommand = {load_literal}
def runtimeDir = System.getenv("XDG_RUNTIME_DIR") ?: System.getenv("TMPDIR") ?: "/tmp"
def launchLog = new File(runtimeDir, {log_name_literal})

if (!launchLog.parentFile?.isDirectory() && !launchLog.parentFile?.mkdirs()) {{
    throw new RuntimeException(
        "Cannot create freeplane-tmux runtime directory: ${{launchLog.parentFile}}"
    )
}}

def executableAvailable = {{ String executable ->
    if (executable == null || executable.trim().isEmpty()) {{
        return false
    }}
    if (executable.contains(File.separator)) {{
        def candidate = new File(executable)
        return candidate.isFile() && candidate.canExecute()
    }}
    def pathValue = System.getenv("PATH") ?: ""
    return pathValue.tokenize(File.pathSeparator).any {{ directory ->
        def candidate = new File(directory, executable)
        candidate.isFile() && candidate.canExecute()
    }}
}}

if (!(System.getenv("DISPLAY") || System.getenv("WAYLAND_DISPLAY"))) {{
    throw new RuntimeException("No GUI display detected (DISPLAY/WAYLAND_DISPLAY is not set)")
}}
if (!executableAvailable(terminalCommand[0].toString())) {{
    throw new RuntimeException("GUI terminal executable not found: ${{terminalCommand[0]}}")
}}
if (!executableAvailable(loadCommand[0].toString())) {{
    throw new RuntimeException("freeplane-tmux executable not found: ${{loadCommand[0]}}")
}}

def cmd = new ArrayList<String>(terminalCommand.size() + loadCommand.size())
cmd.addAll(terminalCommand.collect {{ it.toString() }})
cmd.addAll(loadCommand.collect {{ it.toString() }})

launchLog << "[groovy] background command=" + cmd + System.lineSeparator()
def pb = new ProcessBuilder(cmd)
def childEnvironment = pb.environment()
childEnvironment.remove("TMUX")
childEnvironment.remove("TMUX_PANE")
pb.redirectInput(ProcessBuilder.Redirect.from(new File("/dev/null")))
pb.redirectErrorStream(true)
pb.redirectOutput(ProcessBuilder.Redirect.appendTo(launchLog))
pb.start()

c.statusInfo = "Started freeplane-tmux in GUI terminal; log: ${{launchLog.absolutePath}}"
""".strip()


def build_create_map_script(*, map_name: str, root_script: str) -> str:
    map_name_literal = _groovy_string(map_name)
    root_script_literal = _groovy_string(root_script)
    return f"""
import groovy.json.JsonOutput


def mapName = {map_name_literal}
def rootScript = {root_script_literal}
def newMap = c.newMap()
if (newMap == null) {{
    throw new IllegalStateException("Freeplane failed to create a new map")
}}
newMap.name = mapName
newMap.root.text = mapName
newMap.root['script1'] = rootScript

def helloWindow = newMap.root.createChild("hello-win")
def helloCommand = helloWindow.createChild("echo hello world")
// Force Freeplane to materialize stable node IDs immediately so subsequent
// MindMapToJSON export contains ids for the starter branch.
def helloWindowId = helloWindow.id
def helloCommandId = helloCommand.id
helloWindow.tags.add("WINDOW")

return JsonOutput.toJson([
    hello_window_id: helloWindowId,
    hello_command_id: helloCommandId,
    name: newMap.name,
    root_text: newMap.root.text,
    script1: newMap.root['script1']?.toString(),
])
""".strip()
