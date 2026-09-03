# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Kepler73 — builds a single-file Windows executable.
#
#   pip install -r requirements.txt pyinstaller
#   pyinstaller --clean --noconfirm kepler73.spec
#   -> dist/Kepler73.exe
#
# (or just double-click build.bat)

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# flask-socketio runs in "threading" async mode; python-engineio pulls these in
# by dynamic import, which PyInstaller's static analysis can't see.
hiddenimports = [
    "engineio.async_drivers.threading",
    "simple_websocket",
]
hiddenimports += collect_submodules("sgp4")

# Bundle the whole web UI next to the exe's internal root; backend.config.BASE_DIR
# resolves to sys._MEIPASS when frozen, so api/__init__.py finds frontend/ there.
datas = [("frontend", "frontend")]
datas += collect_data_files("sgp4")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "PIL", "Pillow",
        "pandas", "scipy", "pytest", "IPython", "notebook",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Kepler73",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # UPX often trips antivirus heuristics; ship the exe unpacked
    runtime_tmpdir=None,
    console=True,       # shows the startup banner + any errors (port in use, etc.).
                       # Set to False for a windowless app once you're happy with it.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon="kepler73.ico",   # drop a .ico in the project root and uncomment
)
