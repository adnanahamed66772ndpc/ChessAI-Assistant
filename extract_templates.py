"""Extract piece templates from a chessboard image.

Two modes:
    python extract_templates.py <image> <set_name>
    python extract_templates.py <image> <set_name> --fen "<placement>"

The extractor looks at the warped 640x640 board, picks one representative
square per piece type, and writes an RGBA PNG where alpha approximates the
piece silhouette. The crucial detail is the background-colour estimate: if
we sample BG from the same square, a large piece (e.g. a bishop covering
the corners) produces a degenerate template. Instead we sample BG from the
nearest empty square of the same colour (light/dark) and use that for all
piece extractions.
"""
from __future__ import annotations

import os
import sys

import cv2
import numpy as np

from app.detector import _warp_to_board, SQUARE_PX, TEMPLATES_DIR


FEN_TO_CODE = {
    "P": "wP", "N": "wN", "B": "wB", "R": "wR", "Q": "wQ", "K": "wK",
    "p": "bP", "n": "bN", "b": "bB", "r": "bR", "q": "bQ", "k": "bK",
}


def _starting_picks() -> dict:
    return {
        "bR": (0, 0), "bN": (0, 1), "bB": (0, 2), "bQ": (0, 3),
        "bK": (0, 4), "bP": (1, 3),
        "wR": (7, 0), "wN": (7, 1), "wB": (7, 2), "wQ": (7, 3),
        "wK": (7, 4), "wP": (6, 3),
    }


def _grid_from_fen(fen_placement: str) -> list:
    """Return an 8x8 list where each cell is the FEN char or ''."""
    g = [["" for _ in range(8)] for _ in range(8)]
    rows = fen_placement.strip().split("/")
    if len(rows) != 8:
        raise ValueError(f"FEN placement must have 8 rows, got {len(rows)}")
    for r, row in enumerate(rows):
        c = 0
        for ch in row:
            if ch.isdigit():
                c += int(ch)
            else:
                g[r][c] = ch
                c += 1
    return g


def _picks_from_fen(grid: list) -> dict:
    """Walk the grid and pick the first occurrence of each piece type."""
    picks: dict = {}
    for r in range(8):
        for c in range(8):
            ch = grid[r][c]
            if ch:
                code = FEN_TO_CODE.get(ch)
                if code and code not in picks:
                    picks[code] = (r, c)
    missing = set(FEN_TO_CODE.values()) - set(picks)
    if missing:
        raise ValueError(f"FEN missing pieces: {sorted(missing)}")
    return picks


def _empty_square_bg(board_bgr: np.ndarray, grid: list, square_color: int) -> np.ndarray:
    """Find the nearest empty square of the requested colour (0=light, 1=dark)
    and return its mean BGR. Falls back to median across all empty squares
    of that colour."""
    samples = []
    for r in range(8):
        for c in range(8):
            if grid[r][c]:
                continue
            if ((r + c) % 2) != square_color:
                continue
            sq = board_bgr[r * SQUARE_PX:(r + 1) * SQUARE_PX,
                           c * SQUARE_PX:(c + 1) * SQUARE_PX]
            samples.append(sq.reshape(-1, 3))
    if not samples:
        # No empty squares of that colour — sample corners of every piece
        # square of that colour as a degraded fallback.
        for r in range(8):
            for c in range(8):
                if ((r + c) % 2) != square_color:
                    continue
                sq = board_bgr[r * SQUARE_PX:(r + 1) * SQUARE_PX,
                               c * SQUARE_PX:(c + 1) * SQUARE_PX]
                pad = max(2, SQUARE_PX // 12)
                samples.append(sq[0:pad, 0:pad].reshape(-1, 3))
                samples.append(sq[0:pad, -pad:].reshape(-1, 3))
                samples.append(sq[-pad:, 0:pad].reshape(-1, 3))
                samples.append(sq[-pad:, -pad:].reshape(-1, 3))
    stack = np.concatenate(samples, axis=0).astype(np.float32)
    return np.median(stack, axis=0)


def _square_to_rgba(square_bgr: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """Build an RGBA template using the supplied BG colour. Alpha ramps from
    0 (matching bg) to 255 (clearly different)."""
    diff = np.linalg.norm(square_bgr.astype(np.float32) - bg, axis=2)
    alpha = np.clip((diff - 8.0) * (255.0 / 32.0), 0, 255).astype(np.uint8)
    binary = (alpha > 30).astype(np.uint8) * 255
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    alpha = np.where(binary > 0, alpha, 0).astype(np.uint8)
    return np.dstack([square_bgr, alpha])


def extract(image_path: str, set_name: str, fen: str | None = None) -> None:
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(image_path)
    board = _warp_to_board(img)

    if fen:
        grid = _grid_from_fen(fen)
        picks = _picks_from_fen(grid)
    else:
        # Starting position: build the grid manually so empty rows are known.
        grid = [["" for _ in range(8)] for _ in range(8)]
        layout = "rnbqkbnr"
        for c in range(8):
            grid[0][c] = layout[c]; grid[1][c] = "p"
            grid[6][c] = "P"; grid[7][c] = layout[c].upper()
        picks = _starting_picks()

    bg_light = _empty_square_bg(board, grid, square_color=0)
    bg_dark = _empty_square_bg(board, grid, square_color=1)

    out_dir = os.path.join(TEMPLATES_DIR, set_name)
    os.makedirs(out_dir, exist_ok=True)

    for code, (r, c) in picks.items():
        sq = board[r * SQUARE_PX:(r + 1) * SQUARE_PX,
                   c * SQUARE_PX:(c + 1) * SQUARE_PX]
        bg = bg_light if (r + c) % 2 == 0 else bg_dark
        rgba = _square_to_rgba(sq, bg)
        path = os.path.join(out_dir, f"{code}.png")
        cv2.imwrite(path, rgba)
        ratio = float((rgba[:, :, 3] > 0).mean())
        print(f"  {code} <- ({r},{c})  alpha={ratio:.2f}")


if __name__ == "__main__":
    args = sys.argv[1:]
    fen_value: str | None = None
    if "--fen" in args:
        i = args.index("--fen")
        fen_value = args[i + 1]
        del args[i:i + 2]
    if len(args) != 2:
        print('Usage: python extract_templates.py <image> <set_name> [--fen "<placement>"]')
        sys.exit(1)
    extract(args[0], args[1], fen_value)
    print("Done.")
