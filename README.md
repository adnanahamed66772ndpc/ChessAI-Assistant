# Chess AI Assistant

A desktop chess assistant that captures the board from your screen (or a
screenshot), recognises the position, and suggests the best moves using a
bundled Stockfish 17.1 engine. Includes a Flask web UI for the same workflow.

> **Note for Bengali speakers:** full feature walkthrough is in
> [`HELP.md`](HELP.md) (Banglish + English mix).

## Features

- **Native desktop app** (PySide6 / Qt 6) with drag-and-drop board, move history,
  PGN export, single-instance lock.
- **Live screen capture** via `mss` — pick a monitor, drag a ROI over the
  board, and arrows update in real time as the position changes.
- **Image-to-FEN detection** — drop a screenshot in, the detector finds the
  board, warps it to 640×640, and classifies every square against bundled
  piece templates.
- **Bundled Stockfish 17.1** at full strength (NNUE, all cores, 1 GB hash) with
  optional ELO cap (1320–3190).
- **Multiple template themes:** chess.com, worldchess.com, lichess.org, and a
  default Merida set. The detector picks the best-matching set automatically
  per square, so you can switch sites without changing any setting.
- **Flask web UI** (`main.py`) — same engine + detector exposed through
  `/api/detect`, `/api/analyze`, `/api/move`, `/api/legal-moves`, `/api/fix-fen`.

## Quick start

### Windows users — pre-built .exe

Download the latest `ChessAI-windows.zip` from
[Releases](../../releases/latest), extract anywhere, and double-click
`ChessAI.exe`. No Python install required.

### Run from source

```bat
git clone https://github.com/adnanahamed66772ndpc/ChessAI-Assistant
cd ChessAI-Assistant
python -m venv venv
venv\Scripts\activate
pip install pyside6 mss opencv-python python-chess flask numpy

REM Desktop app
desktop.bat

REM Or the Flask web UI
run.bat
```

Stockfish.exe ships with the repo under `stockfish/`. No separate download.

## Building your own .exe

`ChessAI.spec` is configured for a single-folder Windows distribution
(PyInstaller, no UPX) — best balance of startup speed and AV false-positive
rate.

```bat
venv\Scripts\activate
pip install pyinstaller
pyinstaller ChessAI.spec --noconfirm
```

Output lands in `dist/ChessAI/`. Zip the folder and ship.

## Adding a new piece-style template set

Two extractors are included.

**From a full starting-position board screenshot:**
```bat
python extract_templates.py "path\to\board.png" my_set_name
```

**From individual square close-ups** (12+ files, one per piece):
```bat
python extract_single_pieces.py "path\to\folder" my_set_name
```

`extract_single_pieces.py` parses filenames like
`white rook on dark square.png` (spelling-tolerant — `wight`, `pon`, `horse`,
`bisab`, etc. all work). It picks the best-contrast image per piece and writes
RGBA templates to `piece_templates/my_set_name/`. The detector automatically
loads any subdirectory of `piece_templates/` — no code change needed.

## Layout

```
├── main.py                    # Flask server entry
├── desktop_app.py             # PySide6 desktop entry
├── ChessAI.spec               # PyInstaller spec
├── HELP.md                    # End-user feature guide (Banglish)
├── app/
│   ├── detector.py            # Image -> FEN pipeline (OpenCV)
│   └── engine.py              # Stockfish wrapper (python-chess)
├── extract_templates.py       # Build templates from a full board
├── extract_single_pieces.py   # Build templates from per-piece close-ups
├── stockfish/stockfish.exe    # Bundled engine (Stockfish 17.1)
├── piece_templates/           # RGBA templates (merida + chesscom + ...)
├── static/                    # Web UI assets
├── templates/                 # Flask Jinja templates
└── uploads/                   # Detector debug images (safe to delete)
```

## License

Stockfish is GPLv3. This project's source is provided as-is for personal use.
