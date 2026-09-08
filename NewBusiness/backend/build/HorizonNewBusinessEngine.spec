# -*- mode: python ; coding: utf-8 -*-
#
# Mirrors ForecastEngine/backend/build/Horizon.spec's structure exactly -- the
# New Business engine has the same shape (FastAPI + sklearn/pandas + a seeded
# data/models pair), just no frontend_dist to bundle (this backend never
# serves a frontend of its own; the gateway's engine does that job).
#
# hiddenimports lists train_nb/build_book/etl_nb/nb_encoders explicitly even
# though app/main.py's retrain() route imports three of them inside a function
# body (which PyInstaller's bytecode scanner does already trace) -- belt and
# suspenders, because nb_encoders.SmoothedTargetEncoder is also referenced
# INDIRECTLY the moment nb_latest.joblib is unpickled at startup, and a silent
# gap here would surface as a confusing unpickling crash on someone else's
# machine with no dev environment to debug it in, not a clean import error.
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('C:/Users/AbdumalikDalerzoda/Desktop/ForecastEngine/NewBusiness/backend/_seed_stage/data', 'seed/data'), ('C:/Users/AbdumalikDalerzoda/Desktop/ForecastEngine/NewBusiness/backend/_seed_stage/models', 'seed/models')]
binaries = []
hiddenimports = ['train_nb', 'build_book', 'etl_nb', 'nb_encoders']
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
    name='HorizonNewBusinessEngine',
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
