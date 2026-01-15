# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

pyrpfiv_datas = collect_data_files('pyrpfiv')

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets/radio/*.png', 'assets/radio'),
        ('assets/fonts/*.ttf', 'assets/fonts'),
        ('tools/IVAudioConv.exe', 'tools'),
        ('tools/bass.dll', 'tools'),
        ('tools/bassenc.dll', 'tools'),
        ('tools/bassmix.dll', 'tools'),
        ('tools/ivam.exe', 'tools'),
    ] + pyrpfiv_datas,  
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'qt_material',
        'pydub',
        'pyrpfiv',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='IVRadioEditor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False, 
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
