# -*- mode: python ; coding: utf-8 -*-
# v0.2：对齐主程序打包方式——uiautomation 依赖 comtypes 等运行时子模块，
# 空 hiddenimports 会导致 exe 里缺 COM 依赖，import 能过但首次调
# ControlFromHandle 时崩溃/挂死（症状：日志停在"命中关键词=A419"无后续）。
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []

tmp_ret = collect_all('uiautomation')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('PIL')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

a = Analysis(
    ['test_refresh_filter.py'],
    pathex=[],
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
    name='test_refresh_filter_v0.10.1',
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
