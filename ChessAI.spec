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
# resulting ChessAI binary with a commercial certificate.
#
# Cross-platform notes
# --------------------
# PyInstaller does not cross-compile. Run this spec on the OS you want to
# target. The Stockfish binary name is OS-specific:
#   - Windows: stockfish/stockfish.exe
#   - Linux/macOS: stockfish/stockfish
# The CI workflow (.github/workflows/release.yml) downloads the correct
# binary on each platform before invoking PyInstaller.

import os
import platform as _platform
from PyInstaller.utils.hooks import collect_submodules

# python-chess loads submodules lazily; PyInstaller's static analysis misses
# them. Force-include the whole package.
hiddenimports = collect_submodules('chess')

_is_windows = _platform.system() == 'Windows'
_stockfish_name = 'stockfish.exe' if _is_windows else 'stockfish'
_stockfish_src = os.path.join('stockfish', _stockfish_name)


a = Analysis(
    ['desktop_app.py'],
    pathex=[],
    # Bundle Stockfish next to the script. The Analysis copies it into the
    # build's `stockfish/` directory; engine.py finds it there via sys._MEIPASS.
    binaries=[(_stockfish_src, 'stockfish')],
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
