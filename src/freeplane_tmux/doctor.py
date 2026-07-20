from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .freeplane_projector import FreeplaneDiagnosticProjector


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    ok: bool
    details: str
    required: bool = True

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "details": self.details,
            "required": self.required,
        }


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok or not check.required for check in self.checks)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checks": [check.to_json_dict() for check in self.checks],
        }


def _program_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).expanduser().resolve()
    return Path(sys.argv[0] if sys.argv else sys.executable).expanduser().resolve()


def run_doctor(
    *, address: str, timeout: float, terminal_command: str | None = None
) -> DoctorReport:
    checks: list[DoctorCheck] = []

    program_path = _program_path()
    checks.append(
        DoctorCheck(
            name="binary path",
            ok=program_path.exists(),
            details=str(program_path),
        )
    )

    log_dir = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        test_log = log_dir / "freeplane-tmux-doctor.log"
        test_log.write_text("doctor\n", encoding="utf-8")
        test_log.unlink(missing_ok=True)
        checks.append(DoctorCheck("launcher log path", True, str(log_dir)))
    except OSError as exc:
        checks.append(DoctorCheck("launcher log path", False, str(exc)))

    tmux_path = shutil.which("tmux")
    checks.append(DoctorCheck("tmux executable", tmux_path is not None, tmux_path or "not found"))

    try:
        import tmuxp.cli  # noqa: F401

        checks.append(DoctorCheck("tmuxp runtime", True, "bundled import succeeded"))
    except ImportError as exc:
        checks.append(DoctorCheck("tmuxp runtime", False, str(exc)))

    terminal_value = terminal_command or os.environ.get("TERMINAL") or "x-terminal-emulator -e"
    terminal_binary = terminal_value.split()[0]
    terminal_ok = shutil.which(terminal_binary) is not None or Path(terminal_binary).exists()
    checks.append(DoctorCheck("terminal command", terminal_ok, terminal_value, required=False))

    if tmux_path is not None:
        session_name = "freeplane-tmux-doctor-session"
        create = subprocess.run(
            [tmux_path, "new-session", "-d", "-s", session_name, "printf doctor"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if create.returncode == 0:
            subprocess.run(
                [tmux_path, "kill-session", "-t", f"={session_name}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            checks.append(DoctorCheck("temporary tmux session", True, session_name))
        else:
            checks.append(
                DoctorCheck(
                    "temporary tmux session",
                    False,
                    create.stderr.strip() or f"exit code {create.returncode}",
                )
            )

    try:
        projector = FreeplaneDiagnosticProjector(address=address, timeout=timeout)
        capabilities = projector.detect_capabilities().capabilities
        checks.append(
            DoctorCheck(
                "Freeplane gRPC connection", bool(capabilities.get("ok")), str(capabilities)
            )
        )
        for name in ("can_set_status", "can_set_attribute", "can_add_icon"):
            checks.append(
                DoctorCheck(
                    f"required Freeplane RPC {name}",
                    bool(capabilities.get(name)),
                    str(capabilities.get(name)),
                    required=name != "can_add_icon",
                )
            )
    except Exception as exc:  # pragma: no cover - exercised via tests with monkeypatch
        checks.append(DoctorCheck("Freeplane gRPC connection", False, str(exc)))

    resource_path = Path(__file__).resolve().parent.parent / "freeplane_tmux"
    checks.append(
        DoctorCheck("PyInstaller resource paths", resource_path.exists(), str(resource_path))
    )

    return DoctorReport(checks=tuple(checks))


def format_doctor_text(report: DoctorReport) -> str:
    lines = []
    for check in report.checks:
        prefix = "[OK]" if check.ok else "[FAIL]"
        lines.append(f"{prefix} {check.name}: {check.details}")
    return "\n".join(lines) + "\n"
