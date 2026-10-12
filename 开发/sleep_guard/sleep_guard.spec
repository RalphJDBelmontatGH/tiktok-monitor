# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['sleep_guard.py'],
    pathex=['D:\\Program Files of mine\\TikTok监控器\\开发\\test_refresh_filter',
            'D:\\Program Files of mine\\TikTok监控器\\开发\\test_auto_close'],
    binaries=[],
    datas=[],
    hiddenimports=['test_refresh_filter', 'test_auto_close'],
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
    name='sleep_guard',
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
