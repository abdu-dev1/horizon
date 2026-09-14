# -*- mode: python ; coding: utf-8 -*-
#
# Builds HorizonRenewalEngine.exe -- one of the two "engine" halves of the
# packaged combined product (see gateway/build/Horizon.spec, which embeds
# this build's output as opaque data). Never the exe a recipient runs
# directly; package_all.py stages it into gateway/build/embed/ under that
# name before building the gateway launcher.
#
# `seed_bundle.zip` (staged by ../package_app.py, one directory up, from
# publish.py's dist_bundles output) is embedded as plain `datas` -- the
# runtime data/models this engine reads on first launch, applied via the
# SAME app/bundle.py code path a live deployment's Admin page uses (see
# ../run_app.py's _ensure_seeded()). frontend_dist is the merged SPA this
# engine serves at "/" for the whole combined product (both dashboards --
# see frontend/src/App.jsx).
#
# hiddenimports lists build_book and upload_feed explicitly even though
# app/main.py already imports both (one at module level, the others inside
# route bodies, which PyInstaller's bytecode scanner should already trace)
# -- belt and suspenders, because app/main.py's `sys.path.insert(0,
# str(backend_dir()))` before those imports points at this build's
# PER-USER data folder at runtime, not at this source tree, so if either
# import ever stopped being traced automatically it would fail on someone
# else's machine with no dev environment to debug it in.
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [
    ('C:/Users/AbdumalikDalerzoda/Desktop/ForecastEngine/frontend/dist', 'frontend_dist'),
    # Hand-curated rsd/am name aliases. Shipped SEPARATELY from seed_bundle.zip
    # because it is a bundle DELTA file (app/bundle.py DELTA_FILES) -- a bundle
    # may never carry one, and apply() refuses a bundle that does. But a fresh
    # laptop's per-user folder starts empty, so without this the packaged app
    # would seed data with no alias table and show the fragmented names again
    # ('Scott' / 'Scott B' / 'Scott Brendamour' as three people). run_app.py's
    # _ensure_seeded() copies these in only when absent, so a recipient's own
    # edits are never overwritten.
    ('C:/Users/AbdumalikDalerzoda/Desktop/ForecastEngine/backend/data/people_aliases.csv', 'seed_delta'),
]
seed_zip = 'C:/Users/AbdumalikDalerzoda/Desktop/ForecastEngine/backend/build/embed_seed/seed_bundle.zip'
import os
if os.path.exists(seed_zip):
    datas.append((seed_zip, '.'))

binaries = []
hiddenimports = ['build_book', 'upload_feed']
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
    name='HorizonRenewalEngine',
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
