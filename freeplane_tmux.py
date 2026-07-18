#!/usr/bin/env python3
# ruff: noqa: E402, I001
"""Compatibility entry point for users of the original single-file script."""

from __future__ import annotations

import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
_PACKAGE = _SRC / "freeplane_tmux"

if __name__ == "freeplane_tmux":
    # Let imports from a source checkout treat this compatibility file as a
    # package facade instead of shadowing src/freeplane_tmux.
    __path__ = [str(_PACKAGE)]  # type: ignore[name-defined]
else:
    sys.path.insert(0, str(_SRC))

from freeplane_tmux.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
