# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ['vector_embedded_finder.daemon', 'vector_embedded_finder.connectors.gmail', 'vector_embedded_finder.connectors.gcal', 'vector_embedded_finder.connectors.gdrive', 'vector_embedded_finder.connectors.calai', 'vector_embedded_finder.connectors.canvas', 'vector_embedded_finder.connectors.schoology', 'vector_embedded_finder.connectors.notion']
datas += collect_data_files('chromadb')
datas += collect_data_files('google')
datas += collect_data_files('faster_whisper')
tmp_ret = collect_all('chromadb')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('rfc3987_syntax')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['../../../vector_embedded_finder/_sidecar_entry.py'],
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
    name='recall-daemon-aarch64-apple-darwin',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch='arm64',
    codesign_identity=None,
    entitlements_file=None,
)
