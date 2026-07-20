from __future__ import annotations

import json
from pathlib import Path

from conftest import node

from freeplane_tmux.addon import build_addon_actions, render_action_script
from freeplane_tmux.cli import main
from freeplane_tmux.diagnostics import build_explain_plan, compile_with_diagnostics, explain_text
from freeplane_tmux.doctor import DoctorCheck, DoctorReport, format_doctor_text, run_doctor
from freeplane_tmux.freeplane_projector import FreeplaneDiagnosticProjector, capability_probe_script
from freeplane_tmux.models import CompileResult, Diagnostic, RawNode


def _raw(value: dict) -> RawNode:
    return RawNode.model_validate(value)


def test_diagnostic_and_compile_result_json() -> None:
    diagnostic = Diagnostic(
        severity="warning",
        code="UNUSED_HELPER",
        message="helper is unused",
        node_id="helper",
        node_path="demo / helper",
    )
    result = CompileResult(session=None, diagnostics=(diagnostic,))

    assert result.ok is True
    assert result.to_json_dict() == {
        "ok": True,
        "diagnostics": [
            {
                "severity": "warning",
                "code": "UNUSED_HELPER",
                "message": "helper is unused",
                "node_id": "helper",
                "node_path": "demo / helper",
            }
        ],
    }


def test_compile_with_diagnostics_reports_undefined_template_variable() -> None:
    raw = _raw(
        node(
            "root",
            "demo",
            children=[
                node(
                    "window",
                    "ops",
                    tags=["WINDOW"],
                    children=[node("cmd", "cmd", detail="echo {{ vars.missing.value }}")],
                )
            ],
        )
    )

    result = compile_with_diagnostics(raw)

    assert result.ok is False
    assert result.diagnostics[0].code == "UNDEFINED_TEMPLATE_VARIABLE"
    assert result.diagnostics[0].node_id == "cmd"
    assert result.diagnostics[0].node_path.endswith("demo / ops / cmd")


def test_compile_with_diagnostics_reports_relationship_target_not_found() -> None:
    raw = _raw(
        node(
            "root",
            "demo",
            children=[
                node(
                    "window",
                    "ops",
                    tags=["WINDOW"],
                    children=[node("cmd", "cmd", relationship="missing")],
                )
            ],
        )
    )

    result = compile_with_diagnostics(raw)

    assert result.ok is False
    assert result.diagnostics[0].code == "RELATIONSHIP_TARGET_NOT_FOUND"
    assert result.diagnostics[0].relationship_target_id == "missing"


def test_compile_with_diagnostics_reports_relationship_cycle() -> None:
    raw = _raw(
        node(
            "root",
            "demo",
            children=[
                node(
                    "window",
                    "ops",
                    tags=["WINDOW"],
                    children=[node("cmd", "cmd", relationship="helper-a")],
                ),
                node("helper-a", "A", relationship="helper-b"),
                node("helper-b", "B", relationship="helper-a"),
            ],
        )
    )

    result = compile_with_diagnostics(raw)

    assert result.ok is False
    assert result.diagnostics[0].code == "RELATIONSHIP_CYCLE"


def test_compile_with_diagnostics_collects_expected_warnings() -> None:
    raw = _raw(
        node(
            "root",
            "demo",
            children=[
                node(
                    "window",
                    "ops",
                    tags=["WINDOW"],
                    attributes={"tmux.layout": "main-horizontal"},
                    children=[
                        node(
                            "pane",
                            "second pane",
                            detail="ssh host uptime",
                            attributes={"env.TOKEN": "secret"},
                        )
                    ],
                ),
                node("helper", "helper", detail="echo helper"),
            ],
        )
    )

    result = compile_with_diagnostics(raw)
    codes = {diagnostic.code for diagnostic in result.diagnostics}

    assert result.ok is True
    assert {
        "INFERRED_PANE",
        "INEFFECTIVE_LAYOUT",
        "UNUSED_HELPER",
        "CONTEXT_PROPAGATION_SKIPPED",
    } <= codes


def test_compile_with_diagnostics_warns_on_untyped_relationship_and_empty_window() -> None:
    raw = _raw(
        node(
            "root",
            "demo",
            children=[
                node("helper", "helper", detail="echo helper"),
                node(
                    "window",
                    "ops",
                    tags=["WINDOW"],
                    children=[node("pane", "pane", relationship="helper")],
                ),
                node("empty-window", "empty", tags=["WINDOW"]),
            ],
        )
    )

    result = compile_with_diagnostics(raw)
    codes = {diagnostic.code for diagnostic in result.diagnostics}

    assert result.ok is True
    assert "UNTYPED_RELATIONSHIP" in codes
    assert "EMPTY_WINDOW" in codes


def test_validate_json_and_exit_code(tmp_path: Path, capsys) -> None:
    raw = {
        "id": "root",
        "text": "demo",
        "children": [
            {
                "id": "window",
                "text": "ops",
                "tags": ["WINDOW"],
                "children": [{"id": "cmd", "text": "cmd", "detail": "echo {{ vars.missing }}"}],
            }
        ],
    }
    map_path = tmp_path / "map.json"
    map_path.write_text(json.dumps(raw), encoding="utf-8")

    exit_code = main(["validate", "--map-json", str(map_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["diagnostics"][0]["code"] == "UNDEFINED_TEMPLATE_VARIABLE"


def test_explain_text_and_json_include_relationships_and_provenance(tmp_path: Path, capsys) -> None:
    raw = {
        "id": "root",
        "text": "demo",
        "children": [
            {
                "id": "vars",
                "text": "vars",
                "children": [
                    {
                        "id": "db",
                        "text": "db",
                        "attributes": {"host": "db.internal"},
                    }
                ],
            },
            {
                "id": "window",
                "text": "ops",
                "tags": ["WINDOW"],
                "children": [
                    {"id": "pane", "text": "pane", "detail": "echo {{ vars.db.host }}"},
                    {
                        "id": "call",
                        "text": "call",
                        "relationships": [{"target_id": "helper", "type": "call"}],
                    },
                ],
            },
            {"id": "helper", "text": "helper", "detail": "echo helper"},
        ],
    }
    map_path = tmp_path / "map.json"
    map_path.write_text(json.dumps(raw), encoding="utf-8")

    exit_code = main(["explain", "--map-json", str(map_path)])
    text = capsys.readouterr().out
    assert exit_code == 0
    assert "Session: demo" in text
    assert "defined at: demo / vars / db [attribute host]" in text
    assert "kind: call" in text

    exit_code = main(["explain", "--map-json", str(map_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["relationships"][0]["kind"] == "call"


def test_build_explain_plan_directly() -> None:
    raw = _raw(
        node(
            "root",
            "demo",
            children=[
                node(
                    "window",
                    "ops",
                    tags=["WINDOW"],
                    attributes={"exec.workdir": "/srv/demo"},
                    children=[node("pane", "pane", detail="echo hello")],
                )
            ],
        )
    )
    result = compile_with_diagnostics(raw)

    plan = build_explain_plan(raw, result.session)
    rendered = explain_text(plan)

    assert plan["windows"][0]["workdir"]["defined_at"].endswith("[attributes.exec.workdir]")
    assert "Workdir: /srv/demo" in rendered


def test_projector_uses_groovy_rpc(monkeypatch) -> None:
    calls: list[str] = []

    def fake_execute_groovy(*, address: str, timeout: float, groovy_code: str):
        calls.append(groovy_code)
        return "{}", {
            "ok": True,
            "can_set_status": True,
            "can_set_attribute": True,
            "can_add_icon": True,
        }

    monkeypatch.setattr("freeplane_tmux.freeplane_projector.execute_groovy", fake_execute_groovy)
    projector = FreeplaneDiagnosticProjector(address="127.0.0.1:50051", timeout=1)

    capabilities = projector.detect_capabilities()
    projector.apply([Diagnostic(severity="error", code="X", message="boom", node_id="n1")])
    projector.clear()

    assert capabilities.ok is True
    assert "can_add_icon" in capability_probe_script()
    assert len(calls) == 3
    assert "tmux_mindmap.diag" in calls[1]


def test_doctor_text_and_json(monkeypatch) -> None:
    monkeypatch.setattr(
        "freeplane_tmux.doctor.shutil.which",
        lambda name: "/usr/bin/" + name if name in {"tmux", "x-terminal-emulator"} else None,
    )
    monkeypatch.setattr(
        "freeplane_tmux.doctor.subprocess.run",
        lambda *args, **kwargs: type("P", (), {"returncode": 0, "stderr": ""})(),
    )
    monkeypatch.setattr(
        "freeplane_tmux.doctor.FreeplaneDiagnosticProjector.detect_capabilities",
        lambda self: type(
            "R",
            (),
            {
                "capabilities": {
                    "ok": True,
                    "can_set_status": True,
                    "can_set_attribute": True,
                    "can_add_icon": False,
                }
            },
        )(),
    )

    report = run_doctor(address="127.0.0.1:50051", timeout=1)
    text = format_doctor_text(report)

    assert report.ok is True
    assert "[OK] Freeplane gRPC connection" in text
    assert report.to_json_dict()["ok"] is True


def test_doctor_partial_failure() -> None:
    report = DoctorReport(
        checks=(
            DoctorCheck("tmux executable", True, "/usr/bin/tmux"),
            DoctorCheck("Freeplane gRPC connection", False, "connection refused"),
        )
    )

    assert report.ok is False
    assert "[FAIL] Freeplane gRPC connection" in format_doctor_text(report)


def test_addon_command_generation() -> None:
    actions = build_addon_actions(["/opt/freeplane-tmux"])
    titles = [action.name for action in actions]
    script = render_action_script(actions[0])

    assert titles == [
        "Validate map",
        "Explain map",
        "Load session",
        "Clear diagnostics",
        "Doctor",
    ]
    assert "validate" in script
    assert "/opt/freeplane-tmux" in render_action_script(actions[1])
