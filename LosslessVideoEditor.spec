# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['lossless_editor.py'],
    pathex=[],
    binaries=[],
    datas=[('ffmpeg-bundle.zip', 'ffmpeg_bundle')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# onedir: EXE 只含脚本与解释器引导,二进制/datas 放进 _internal 目录(COLLECT)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LosslessVideoEditor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LosslessVideoEditor',
)
