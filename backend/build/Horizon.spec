# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('C:/Users/AbdumalikDalerzoda/Desktop/ForecastEngine/frontend/dist', 'frontend_dist'), ('C:/Users/AbdumalikDalerzoda/Desktop/ForecastEngine/backend/_seed_stage/data', 'seed/data'), ('C:/Users/AbdumalikDalerzoda/Desktop/ForecastEngine/backend/_seed_stage/models', 'seed/models')]
binaries = []
hiddenimports = ['train_real']
hiddenimports += collect_submodules('app')
tmp_ret = collect_all('scipy')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['..\\run_app.py'],
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
    name='Horizon',
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
    icon=['C:\\Users\\AbdumalikDalerzoda\\Desktop\\ForecastEngine\\frontend\\public\\favicon.ico'],
)
