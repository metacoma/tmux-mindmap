from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


def test_launcher_passes_arguments_without_shell_requoting(tmp_path: Path) -> None:
    source = Path("scripts/freeplane_tmux_launcher.sh").read_text(encoding="utf-8")
    result_path = tmp_path / "arguments.txt"
    terminal_path = tmp_path / "terminal"
    executable_path = tmp_path / "freeplane-tmux"
    launcher_path = tmp_path / "launcher.sh"

    terminal_path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "exec \"$@\"\n",
        encoding="utf-8",
    )
    executable_path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"printf '%s\\n' \"$@\" > {result_path!s}\n",
        encoding="utf-8",
    )
    terminal_path.chmod(0o755)
    executable_path.chmod(0o755)

    source = source.replace(
        "TERMINAL=(x-terminal-emulator -e)",
        f"TERMINAL=({terminal_path!s})",
        1,
    )
    source = source.replace(
        "FREEPLANE_TMUX=(/usr/local/bin/freeplane-tmux --load)",
        f"FREEPLANE_TMUX=({executable_path!s} --load)",
        1,
    )
    source = source.replace("PAUSE_ON_ERROR=1", "PAUSE_ON_ERROR=0", 1)
    launcher_path.write_text(source, encoding="utf-8")
    launcher_path.chmod(0o755)

    subprocess.run(
        [str(launcher_path), "--output-dir", "directory with spaces"],
        check=True,
        env={**os.environ, "XDG_RUNTIME_DIR": str(tmp_path)},
    )

    deadline = time.monotonic() + 3
    while not result_path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)

    assert result_path.read_text(encoding="utf-8").splitlines() == [
        "--load",
        "--output-dir",
        "directory with spaces",
    ]
