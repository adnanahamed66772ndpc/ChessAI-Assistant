# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for Chess AI Assistant.
#
# Build:
#     pyinstaller ChessAI.spec --noconfirm
#
# Output: dist/ChessAI/ (one-folder layout). Zip the folder and ship.
#
# Notes on antivirus false-positives
# ----------------------------------
# PyInstaller binaries (especially `--onefile` with UPX) are frequently flagged
# by Windows Defender / heuristic AVs because the same bootloader is used by
# many real malware samples. This spec deliberately:
#   - uses --onedir (no self-extracting archive at runtime)
#   - sets upx=False (UPX-packed binaries are heavily associated with malware)
#   - keeps console=False (Qt GUI app, no terminal window)
# The remaining false-positives, if any, can be resolved by code-signing the
# resulting ChessAI.exe with a commercial certificate.

from PyInstaller.utils.hooks import collect_submodules

# python-chess loads submodules lazily; PyInstaller's static analysis misses
# them. Force-include the whole package.
hiddenimports = collect_submodules('chess')


a = Analysis(
    ['desktop_app.py'],
    pathex=[],
    # Bundled native binaries — Stockfish ships with the app.
    binaries=[('stockfish/stockfish.exe', 'stockfish')],
    # Bundled data files — board pixmaps and detector templates.
    datas=[
        ('static/pieces', 'static/pieces'),
        ('piece_templates', 'piece_templates'),
    ],
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
    [],
    exclude_binaries=True,        # one-folder mode (smaller, AV-friendly)
    name='ChessAI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                    # see "false-positives" note above
    console=False,                # Qt windowed app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ChessAI',
)
