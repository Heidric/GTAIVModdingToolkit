# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


_SPEC_LOCATION = Path(SPECPATH).resolve()
_SPEC_DIRECTORY = _SPEC_LOCATION if _SPEC_LOCATION.is_dir() else _SPEC_LOCATION.parent
PROJECT_ROOT = _SPEC_DIRECTORY.parent

datas = [
    (str(PROJECT_ROOT / "assets"), "assets"),
    (str(PROJECT_ROOT / "tools"), "tools"),
    (str(PROJECT_ROOT / "README.md"), "."),
    (str(PROJECT_ROOT / "LICENSE"), "."),
]
binaries = []
hiddenimports = collect_submodules("vendor")

for package in ("PIL", "qt_material", "qt_material_icons", "qtawesome", "texfury"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports


a = Analysis(
    [str(PROJECT_ROOT / "app.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GTAIVModdingToolkit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(PROJECT_ROOT / "packaging" / "windows_version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="GTAIVModdingToolkit",
)
