from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_legacy_launcher_artifacts_are_absent() -> None:
    legacy_paths = [
        ROOT / "src" / "freeplane_tmux" / "launcher.py",
        ROOT / "tests" / "test_launcher.py",
        ROOT / "scripts" / "freeplane_tmux_launcher.sh",
        ROOT / "freeplane_tmux.py",
    ]

    assert [str(path.relative_to(ROOT)) for path in legacy_paths if path.exists()] == []
