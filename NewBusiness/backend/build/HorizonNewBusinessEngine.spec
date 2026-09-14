# -*- mode: python ; coding: utf-8 -*-
#
# Builds HorizonNewBusinessEngine.exe -- the other engine half of the
# packaged combined product. Mirrors
# backend/build/HorizonRenewalEngine.spec's structure exactly -- same shape
# (FastAPI + sklearn/pandas + a seeded data/models pair) -- just no
# frontend_dist to bundle (this backend never serves a frontend of its own;
# the renewal engine's static mount does that job for the whole product).
#
# `seed_bundle.zip` (staged by ../package_app.py from publish.py's
# dist_bundles output) is embedded as plain `datas`, applied on first launch
# via app/bundle.py -- see ../run_app.py's _ensure_seeded().
#
# hiddenimports lists build_book, etl_nb, and nb_encoders explicitly even
# though app/nb_mode.py's routes already import build_book (which
# PyInstaller's bytecode scanner should already trace) -- belt and
# suspenders, because nb_encoders.SmoothedTargetEncoder is also referenced
# INDIRECTLY the moment nb_latest.joblib is unpickled at startup, and a
# silent gap here would surface as a confusing unpickling crash on someone
# else's machine with no dev environment to debug it in, not a clean import
# error.
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = []
seed_zip = 'C:/Users/AbdumalikDalerzoda/Desktop/ForecastEngine/NewBusiness/backend/build/embed_seed/seed_bundle.zip'
import os
if os.path.exists(seed_zip):
    datas.append((seed_zip, '.'))

binaries = []
hiddenimports = ['build_book', 'etl_nb', 'nb_encoders']
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
    icon=['C:\\Users\\AbdumalikDalerzoda\\Desktop\\ForecastEngine\\icon.ico'],
)
