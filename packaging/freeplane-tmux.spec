# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

project_root = Path(SPECPATH).parent

# tmuxp's built-in workspace builder is imported through its registry. Collect
# tmuxp's own modules explicitly, but do not drag libtmux's pytest helpers into
# the release executable.
hiddenimports = collect_submodules("tmuxp")
datas = copy_metadata("tmuxp") + copy_metadata("libtmux")

a = Analysis(
    [str(project_root / "packaging" / "pyinstaller_entry.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "libtmux.pytest_plugin",
        "libtmux.test",
        "IPython",
        "jedi",
        "matplotlib",
        "numpy",
        "pandas",
        "scipy",
        "PIL",
        "tkinter",
        "gi",
        "lxml",
        "cryptography",
        "urllib3",
        "certifi",
        "psutil",
        "jsonschema",
        "rich",
        "pygments",
        "chardet",
        "anyio",
        "httpx",
        "httpcore",
        "h11",
        "sniffio",
        "dns",
        "idna",
        "socksio",
        "trio",
        "trio_asyncio",
        "importlib_metadata",
        "zipp",
    ],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="freeplane-tmux-linux-x86_64",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
