"""Extract piece templates from single-square close-up screenshots.

Each input file contains ONE board square (piece + background). Filenames
encode the piece — e.g. ``wight rook at black squear.png`` -> wR. Tolerant
of spelling variants like "wight"/"white", "pon"/"pawn", "horse"/"knight",
"bisab"/"bishop".

For each piece type the script picks the best-contrast image available
(white pieces on dark bg, black pieces on light bg). The background colour
is inferred from the actual pixels at the corners — the filename hint can
be wrong, the image cannot.

Existing template files for pieces NOT covered by the inputs are left in
place, so a partial input set augments rather than replaces a previous one.

Usage:
    python extract_single_pieces.py <input_dir> <set_name>
"""
from __future__ import annotations

import os
import re
import sys

import cv2
import numpy as np

from app.detector import SQUARE_PX, TEMPLATES_DIR, _bg_color
from extract_templates import _square_to_rgba


COLOR_TOKENS = {
    "white": "w", "wight": "w", "wite": "w",
    "black": "b", "blak": "b",
}
PIECE_TOKENS = {
    "pawn": "P", "pon": "P", "pwan": "P",
    "rook": "R",
    "knight": "N", "horse": "N", "night": "N",
    "bishop": "B", "bisab": "B", "bisop": "B", "bishab": "B",
    "queen": "Q",
    "king": "K",
}
BG_TOKENS = {
    "white": "light", "wight": "light", "light": "light",
    "black": "dark", "dark": "dark",
}


def parse_filename(name: str):
    """Return (piece_code, filename_bg_hint or None). First color/piece token
    pair is treated as the piece itself; the bg hint is the next colour
    token that immediately precedes a "squ..." word."""
    base = os.path.splitext(name)[0].lower()
    tokens = re.split(r"[\s_\-]+", base)

    color = piece = None
    for tok in tokens:
        if color is None and tok in COLOR_TOKENS:
            color = COLOR_TOKENS[tok]
        elif piece is None and tok in PIECE_TOKENS:
            piece = PIECE_TOKENS[tok]
        if color and piece:
            break
    if not (color and piece):
        return None

    bg_hint = None
    consumed_piece_color = False
    for i, tok in enumerate(tokens):
        if tok in COLOR_TOKENS and not consumed_piece_color:
            consumed_piece_color = True
            continue
        if tok in BG_TOKENS:
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            if nxt.startswith("squ"):
                bg_hint = BG_TOKENS[tok]
                break
    return color + piece, bg_hint


def bg_kind_from_image(img_bgr: np.ndarray) -> str:
    bg = _bg_color(img_bgr)
    # Rec.601 luma. Threshold sits in the middle of the gap measured on real
    # chess.com themes: dark squares cluster around lum=120-135, light around
    # 200-215. 160 gives ~30 units of margin to either side.
    lum = 0.114 * float(bg[0]) + 0.587 * float(bg[1]) + 0.299 * float(bg[2])
    return "light" if lum > 160 else "dark"


def preferred_bg(color: str) -> str:
    return "dark" if color == "w" else "light"


def main(in_dir: str, set_name: str) -> None:
    if not os.path.isdir(in_dir):
        raise FileNotFoundError(in_dir)

    by_code: dict = {}
    for fn in sorted(os.listdir(in_dir)):
        if not fn.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")):
            continue
        parsed = parse_filename(fn)
        if parsed is None:
            print(f"  skip (cannot parse piece): {fn}")
            continue
        code, _hint = parsed
        path = os.path.join(in_dir, fn)
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            print(f"  skip (unreadable): {fn}")
            continue
        sq = cv2.resize(img, (SQUARE_PX, SQUARE_PX), interpolation=cv2.INTER_AREA)
        actual_bg = bg_kind_from_image(sq)
        by_code.setdefault(code, []).append((fn, sq, actual_bg))

    out_dir = os.path.join(TEMPLATES_DIR, set_name)
    os.makedirs(out_dir, exist_ok=True)
    print(f"Writing to {out_dir}")

    for code in sorted(by_code):
        want = preferred_bg(code[0])
        candidates = by_code[code]
        chosen = next((c for c in candidates if c[2] == want), candidates[0])
        fn, sq, actual_bg = chosen
        bg = _bg_color(sq)
        rgba = _square_to_rgba(sq, bg)
        out_path = os.path.join(out_dir, f"{code}.png")
        cv2.imwrite(out_path, rgba)
        ratio = float((rgba[:, :, 3] > 0).mean())
        flag = "OK" if 0.05 <= ratio <= 0.92 else "WARN-degenerate"
        print(f"  {code:>2} <- {fn:<55} bg={actual_bg:<5} alpha={ratio:.2f}  [{flag}]")

    expected = {"wP", "wN", "wB", "wR", "wQ", "wK",
                "bP", "bN", "bB", "bR", "bQ", "bK"}
    missing = expected - set(by_code.keys())
    if missing:
        print("\nMissing (existing templates kept untouched): "
              + ", ".join(sorted(missing)))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python extract_single_pieces.py <input_dir> <set_name>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
    print("Done.")
