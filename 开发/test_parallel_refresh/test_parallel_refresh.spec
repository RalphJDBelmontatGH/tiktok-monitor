# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['test_parallel_refresh.py'],
    pathex=[r'D:\Program Files of mine\TikTok监控器\开发\test_refresh_filter'],
    binaries=[],
    datas=[],
    hiddenimports=['test_refresh_filter'],
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
    a.binaries,
    a.datas,
    [],
    name='test_parallel_refresh',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
