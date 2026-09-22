# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['openpyxl', 'orjson', 'requests', 'yaml']
hiddenimports += collect_submodules('GalTransl')
# mcp SDK 含动态导入（server.stdio / streamable_http 等），与 mcp_types 一并整包收集
hiddenimports += collect_submodules('mcp')
hiddenimports += collect_submodules('mcp_types')


a = Analysis(
    ['D:/解包或汉化用/my-galtransl/my-GalTransl/run_mcp_server.py'],
    pathex=['D:/解包或汉化用/my-galtransl/my-GalTransl'],
    binaries=[],
    datas=[],
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
    name='galtransl_mcp',
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
