# -*- mode: python ; coding: utf-8 -*-
# test_auto_close.spec —— 基于 test_refresh_filter.spec 模板（2026-09-04）
# 主脚本 test_auto_close.py 运行时 sys.path 动态插入 test_refresh_filter 目录，
# 因此需 pathex 指向 test_refresh_filter 并显式 hiddenimports 收集。
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all('uiautomation')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('PIL')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
hiddenimports += ['test_refresh_filter']


a = Analysis(
    ['test_auto_close/test_auto_close.py'],
    pathex=['test_refresh_filter'],
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
    a.binaries,
    a.datas,
    [],
    name='test_auto_close',
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
