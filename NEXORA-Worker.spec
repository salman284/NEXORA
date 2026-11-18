# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['nexora_worker_gui/worker_app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('nexora_worker_gui/assets', 'assets'),
    ],
    hiddenimports=[
        # pystray backend for Windows
        'pystray._win32',
        # PIL/Pillow
        'PIL._tkinter_finder',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageFont',
        # plyer notifications
        'plyer.platforms.win.notification',
        'plyer.utils',
        # psutil internals
        'psutil._pswindows',
        # requests
        'requests.adapters',
        'requests.packages.urllib3',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'numpy', 'pandas', 'scipy',
        'IPython', 'jupyter', 'notebook',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'wx',
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
    name='NEXORA-Worker',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,                   # No console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='nexora_worker_gui/assets/worker_icon.ico',
)
