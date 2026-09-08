# -*- mode: python ; coding: utf-8 -*-
#
# The gateway launcher exe -- the ONLY file a recipient ever sees or
# double-clicks. Bundles only fastapi/uvicorn/httpx/starlette (gateway/
# main.py's own dependencies) -- no pandas/sklearn/scipy here at all, since
# this process never imports either backend's code, only reverse-proxies
# HTTP to two engine exes at runtime (see run_app_frozen.py's docstring for
# why they can't be merged into one process). That keeps THIS exe's own
# analysis small and fast; the two engine exes are pulled in below as opaque
# `datas` (not `binaries` -- they're independent, already-linked executables,
# not DLL dependencies of this one, so they should be copied verbatim, not
# fed through PyInstaller's PE import scanner).
#
# Embedding them as datas is what makes the shipped product a single .exe
# instead of a folder of three: PyInstaller's onefile bootloader packs
# everything datas/binaries/pyz reference into the one output exe, then
# unpacks it all to a temp dir at every launch. run_app_frozen.py copies
# these two specific files out to a PERSISTENT folder
# (%LOCALAPPDATA%/Horizon/bin) on first run instead of launching them
# straight from that temp dir, since re-copying ~300MB out of the temp
# extraction on every single launch would make every startup slow, not just
# the first -- see run_app_frozen.py's _ensure_engine() docstring.
#
# package_all.py stages renamed copies of both engine builds into
# .\embed\ (this directory, alongside this spec file) before invoking
# PyInstaller for this exe specifically so they land in the bundle already
# correctly named -- `datas` copies verbatim, it cannot rename on the way in.
#
# `main` is listed explicitly in hiddenimports: run_app_frozen.py imports it
# with a plain `from main import app` at runtime (gateway/main.py, its own
# reverse-proxy ASGI app, sitting right next to this entry script) -- PyInstaller's
# static analysis should already trace that, but a silent gap here would mean
# the ONE exe a recipient actually runs fails outright, so it's listed anyway.
hiddenimports = ['main']

datas = [
    ('embed\\HorizonRenewalEngine.exe', '.'),
    ('embed\\HorizonNewBusinessEngine.exe', '.'),
]

a = Analysis(
    ['..\\run_app_frozen.py'],
    pathex=[],
    binaries=[],
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
