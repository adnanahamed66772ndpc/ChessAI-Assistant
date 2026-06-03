"""Image -> FEN board detector.

Pipeline
--------
1. Find the chessboard region in the input image (largest square-ish region
   with strong contour). Falls back to centred square crop if detection is
   inconclusive.
2. Warp it to a fixed 640x640 canvas so each square is 80x80.
3. For every square:
   - sample the empty-square colour from the corners
   - render each piece template (RGBA) onto a synthetic background of that
     colour, then compare to the actual square with normalised cross
     correlation. Best match wins.
   - the empty / occupied decision falls out of the score: if the best
     "occupied" match is below a threshold AND foreground mass is small,
     mark the square empty.

Detection is best-effort. The frontend lets the user click any square to
correct the position before sending it to the engine.
"""
from __future__ import annotations

import os
import base64
import threading
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple

import cv2
import numpy as np

TEMPLATES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "piece_templates",
)

PIECE_CODES = ["wP", "wN", "wB", "wR", "wQ", "wK", "bP", "bN", "bB", "bR", "bQ", "bK"]
CODE_TO_FEN = {
    "wP": "P", "wN": "N", "wB": "B", "wR": "R", "wQ": "Q", "wK": "K",
    "bP": "p", "bN": "n", "bB": "b", "bR": "r", "bQ": "q", "bK": "k",
}

BOARD_SIZE = 640
SQUARE_PX = BOARD_SIZE // 8  # 80


@dataclass
class DetectionResult:
    fen_placement: str
    grid: List[List[str]]
    board_b64: str
    confidence: List[List[float]]
    flipped_guess: bool


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------

_TEMPLATES_CACHE: Optional[list] = None
_TEMPLATES_LOCK = threading.Lock()


def _load_one_set(dir_path: str) -> dict:
    """Load templates from a directory. Each is a (BGR, alpha[0..1]) pair
    at SQUARE_PX resolution. Templates with degenerate (near-empty or
    full-coverage) alpha masks are dropped — they're either the result of
    the piece covering the bg-sample corners, or a warp misalignment."""
    out = {}
    for code in PIECE_CODES:
        p = os.path.join(dir_path, f"{code}.png")
        img = cv2.imread(p, cv2.IMREAD_UNCHANGED)
        if img is None:
            continue
        # Need RGBA (4 channels); skip grayscale, BGR, or anything malformed.
        if img.ndim != 3 or img.shape[2] != 4:
            continue
        img = cv2.resize(img, (SQUARE_PX, SQUARE_PX), interpolation=cv2.INTER_AREA)
        bgr = img[:, :, :3]
        alpha = img[:, :, 3].astype(np.float32) / 255.0
        nonzero_ratio = float((alpha > 0.05).mean())
        if nonzero_ratio < 0.05 or nonzero_ratio > 0.92:
            continue  # broken template
        out[code] = (bgr, alpha)
    # Allow partial sets — better to have 11/12 than 0.
    return out if len(out) >= 6 else {}


def _load_templates() -> list:
    """Load all template sets. The base set lives directly in TEMPLATES_DIR;
    additional sets live in TEMPLATES_DIR/<set_name>/. Returns a list of
    (set_name, dict[code -> (bgr, alpha)])."""
    sets: list = []
    base = _load_one_set(TEMPLATES_DIR)
    if base:
        sets.append(("merida", base))
    if os.path.isdir(TEMPLATES_DIR):
        for name in sorted(os.listdir(TEMPLATES_DIR)):
            sub = os.path.join(TEMPLATES_DIR, name)
            if not os.path.isdir(sub):
                continue
            extra = _load_one_set(sub)
            if extra:
                sets.append((name, extra))
    if not sets:
        raise RuntimeError("No template sets loaded")
    return sets


def _templates() -> list:
    global _TEMPLATES_CACHE
    if _TEMPLATES_CACHE is not None:
        return _TEMPLATES_CACHE
    with _TEMPLATES_LOCK:
        if _TEMPLATES_CACHE is None:
            _TEMPLATES_CACHE = _load_templates()
        return _TEMPLATES_CACHE


# ---------------------------------------------------------------------------
# Board detection
# ---------------------------------------------------------------------------

def _find_board_quad(img_bgr: np.ndarray) -> Optional[np.ndarray]:
    """Find a board-shaped quadrilateral. Returns None if no good candidate
    is found OR if the image already appears to be a tightly-cropped board."""
    h, w = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    img_area = h * w
    img_aspect = max(w, h) / max(min(w, h), 1)
    best, best_score = None, 0.0
    for c in contours:
        area = cv2.contourArea(c)
        if area < img_area * 0.20:
            continue
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) != 4:
            continue
        pts = approx.reshape(4, 2).astype(np.float32)
        rect = cv2.minAreaRect(pts)
        rw, rh = rect[1]
        if min(rw, rh) <= 1:
            continue
        ar = max(rw, rh) / min(rw, rh)
        if ar > 1.25:
            continue
        angle = abs(rect[2])
        if angle > 6 and abs(angle - 90) > 6:
            continue
        # If the image is already nearly square the contour detector tends
        # to grab a slightly-sheared near-image-sized quad off the edge
        # labels. Skip warping in that case — the centred-square crop
        # fallback produces a cleaner grid.
        if img_aspect < 1.10 and area > img_area * 0.65:
            continue
        # Reject any quad covering almost the whole image regardless.
        if area > img_area * 0.95:
            continue
        score = area / ar
        if score > best_score:
            best_score = score
            best = pts
    return best


def _order_quad(pts: np.ndarray) -> np.ndarray:
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).flatten()
    return np.array([
        pts[np.argmin(s)],     # TL
        pts[np.argmin(diff)],  # TR
        pts[np.argmax(s)],     # BR
        pts[np.argmax(diff)],  # BL
    ], dtype=np.float32)


def _find_inner_corners_quad(img_bgr: np.ndarray) -> Optional[np.ndarray]:
    """Use OpenCV's findChessboardCorners to find the 7x7 inner intersections
    of the board, then extrapolate the 4 outer corners. Returns a 4x2 float32
    array in TL, TR, BR, BL order, or None if detection fails. This finder is
    pixel-precise but only works when enough inner corners are visible (mid-
    game positions); pieces in the starting position obscure the centre and
    it tends to fail there."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    flags = cv2.CALIB_CB_ADAPTIVE_THRESH + cv2.CALIB_CB_NORMALIZE_IMAGE
    ok, corners = cv2.findChessboardCorners(gray, (7, 7), flags=flags)
    if not ok or corners is None:
        return None
    pts = corners.reshape(7, 7, 2).astype(np.float32)
    tl_in, tr_in = pts[0, 0], pts[0, 6]
    bl_in, br_in = pts[6, 0], pts[6, 6]
    horiz_step = (tr_in - tl_in) / 6.0
    vert_step = (bl_in - tl_in) / 6.0
    tl = tl_in - horiz_step - vert_step
    tr = tr_in + horiz_step - vert_step
    bl = bl_in - horiz_step + vert_step
    br = br_in + horiz_step + vert_step
    return np.array([tl, tr, br, bl], dtype=np.float32)


def _find_grid_quad_by_projection(img_bgr: np.ndarray) -> Optional[np.ndarray]:
    """Detect the board grid using Sobel edge projections.

    The chessboard's 9 grid lines (8 squares = 9 dividers) show up as the
    9 strongest peaks in the per-column / per-row sum of |gradient|. Once we
    find them we know the exact pixel boundaries of every square. This works
    on starting positions and on slightly-cropped boards where the
    OpenCV chess pattern detector fails.
    """
    h, w = img_bgr.shape[:2]
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    sx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    sy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    vproj = np.abs(sx).sum(axis=0)
    hproj = np.abs(sy).sum(axis=1)

    def find_peaks(arr: np.ndarray, expected: int = 9) -> Optional[List[int]]:
        a = arr.astype(np.float32).copy()
        th = a.max() * 0.30
        out: List[int] = []
        # Spacing must be at least ~1/12 of the axis to avoid double-counts.
        min_dist = max(20, int(0.06 * len(a)))
        for _ in range(expected * 3):
            i = int(np.argmax(a))
            if a[i] < th:
                break
            out.append(i)
            lo = max(0, i - min_dist)
            hi = min(len(a), i + min_dist + 1)
            a[lo:hi] = 0
        if len(out) < expected:
            return None
        out.sort()
        # Score every contiguous run of `expected` peaks by how uniformly
        # spaced it is; pick the most uniform run.
        best, best_score = None, -1.0
        for s in range(len(out) - expected + 1):
            window = out[s:s + expected]
            gaps = np.diff(window)
            if gaps.min() <= 0:
                continue
            uniformity = float(gaps.min() / gaps.max())
            if uniformity > best_score:
                best_score = uniformity
                best = window
        if best is None or best_score < 0.7:
            return None
        return list(best)

    vp = find_peaks(vproj)
    hp = find_peaks(hproj)
    if vp is None or hp is None:
        return None

    # Sanity: the bounding region should be roughly square.
    bw = vp[-1] - vp[0]
    bh = hp[-1] - hp[0]
    if bw <= 0 or bh <= 0:
        return None
    aspect = max(bw, bh) / min(bw, bh)
    if aspect > 1.15:
        return None

    tl = (float(vp[0]), float(hp[0]))
    tr = (float(vp[-1]), float(hp[0]))
    br = (float(vp[-1]), float(hp[-1]))
    bl = (float(vp[0]), float(hp[-1]))
    return np.array([tl, tr, br, bl], dtype=np.float32)


def _warp_to_board(img_bgr: np.ndarray) -> np.ndarray:
    """Try three increasingly aggressive strategies to locate the board.

    Order matters: projection-based detection is the most robust on real
    screenshots because it handles starting positions where pieces obscure
    the inner corners; the OpenCV chessboard finder is pixel-precise when it
    works (mid-game) so we use it second; the contour finder is the last
    fallback.
    """
    dst = np.array(
        [[0, 0], [BOARD_SIZE - 1, 0],
         [BOARD_SIZE - 1, BOARD_SIZE - 1], [0, BOARD_SIZE - 1]],
        dtype=np.float32,
    )

    quad = _find_grid_quad_by_projection(img_bgr)
    if quad is None:
        quad = _find_inner_corners_quad(img_bgr)
    if quad is None:
        quad = _find_board_quad(img_bgr)
        if quad is not None:
            quad = _order_quad(quad)

    if quad is not None:
        M = cv2.getPerspectiveTransform(quad, dst)
        return cv2.warpPerspective(img_bgr, M, (BOARD_SIZE, BOARD_SIZE))

    # Last resort: centred-square crop.
    h, w = img_bgr.shape[:2]
    side = min(h, w)
    y0 = (h - side) // 2
    x0 = (w - side) // 2
    crop = img_bgr[y0:y0 + side, x0:x0 + side]
    return cv2.resize(crop, (BOARD_SIZE, BOARD_SIZE), interpolation=cv2.INTER_AREA)


# ---------------------------------------------------------------------------
# Per-square classification
# ---------------------------------------------------------------------------

def _bg_color(square_bgr: np.ndarray) -> np.ndarray:
    """Estimate empty-square colour by sampling the four corner regions."""
    s = SQUARE_PX
    pad = max(2, s // 12)
    samples = np.concatenate([
        square_bgr[0:pad, 0:pad].reshape(-1, 3),
        square_bgr[0:pad, s - pad:s].reshape(-1, 3),
        square_bgr[s - pad:s, 0:pad].reshape(-1, 3),
        square_bgr[s - pad:s, s - pad:s].reshape(-1, 3),
    ])
    return np.median(samples, axis=0).astype(np.float32)


def _foreground_ratio(square_bgr: np.ndarray, bg: np.ndarray) -> float:
    diff = np.linalg.norm(square_bgr.astype(np.float32) - bg, axis=2)
    return float((diff > 25).mean())


def _render_on_bg(tpl_bgr: np.ndarray, alpha: np.ndarray, bg: np.ndarray) -> np.ndarray:
    a = alpha[:, :, None]
    bg_layer = np.broadcast_to(bg, tpl_bgr.shape).astype(np.float32)
    out = a * tpl_bgr.astype(np.float32) + (1.0 - a) * bg_layer
    return out


def _ncc(a: np.ndarray, b: np.ndarray) -> float:
    """Normalised cross correlation in [-1, 1] between two same-shape arrays."""
    af = a.astype(np.float32).ravel()
    bf = b.astype(np.float32).ravel()
    af -= af.mean()
    bf -= bf.mean()
    na = np.linalg.norm(af)
    nb = np.linalg.norm(bf)
    if na < 1e-6 or nb < 1e-6:
        return 0.0
    return float(np.dot(af, bf) / (na * nb))


def _classify_square(square_bgr: np.ndarray, sets: list) -> Tuple[str, float]:
    """Return (piece_code or '', confidence in 0..1).

    Tries every template across every loaded set. Best NCC across all sets
    wins, but the square is only flagged as occupied when there's
    *meaningful* visual content (foreground mass + pixel variance + decent
    template similarity). Otherwise the square is empty regardless of which
    template happened to match best.
    """
    bg = _bg_color(square_bgr)
    fg_ratio = _foreground_ratio(square_bgr, bg)
    # Pixel-intensity variance: truly empty squares are nearly uniform colour
    # so their grayscale std is < ~5; a piece on a square produces std > 25
    # (interior pixels + outline + background mix).
    gray = cv2.cvtColor(square_bgr, cv2.COLOR_BGR2GRAY)
    std = float(gray.std())

    # Empty fast paths — never run template matching for these.
    # 1. Almost-uniform colour: empty.
    if std < 6.0 and fg_ratio < 0.04:
        return "", 1.0
    # 2. Slight noise but nothing the corner-bg can't explain: empty.
    if std < 12.0 and fg_ratio < 0.08:
        return "", 1.0

    best_code = ""
    best_score = -1.0
    for _set_name, templates in sets:
        for code, (tpl_bgr, alpha) in templates.items():
            rendered = _render_on_bg(tpl_bgr, alpha, bg)
            s = _ncc(square_bgr, rendered)
            if s > best_score:
                best_score = s
                best_code = code

    # When variance is low AND the best template scores poorly, treat as
    # empty even if some piece is the "least bad" match. This is the case
    # that produces the over-detection screenshots — every empty square gets
    # whichever template happens to noise-match best.
    if std < 18.0 and best_score < 0.85:
        return "", max(0.0, 1.0 - best_score)
    if fg_ratio < 0.10 and best_score < 0.75:
        return "", max(0.0, 1.0 - best_score)
    if fg_ratio < 0.04:
        return "", max(0.0, 1.0 - best_score)

    return best_code, max(0.0, best_score)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _png_b64(img_bgr: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", img_bgr)
    if not ok:
        return ""
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


def detect_position(image_bytes: bytes) -> DetectionResult:
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Could not decode image")

    board = _warp_to_board(img)
    templates = _templates()

    grid: List[List[str]] = [["" for _ in range(8)] for _ in range(8)]
    conf: List[List[float]] = [[0.0 for _ in range(8)] for _ in range(8)]

    for r in range(8):
        for c in range(8):
            y0, y1 = r * SQUARE_PX, (r + 1) * SQUARE_PX
            x0, x1 = c * SQUARE_PX, (c + 1) * SQUARE_PX
            sq = board[y0:y1, x0:x1]
            code, score = _classify_square(sq, templates)
            grid[r][c] = code
            conf[r][c] = round(float(score), 3)

    rows: List[str] = []
    for r in range(8):
        empty_run = 0
        s = ""
        for c in range(8):
            cell = grid[r][c]
            if cell == "":
                empty_run += 1
            else:
                if empty_run:
                    s += str(empty_run)
                    empty_run = 0
                s += CODE_TO_FEN[cell]
        if empty_run:
            s += str(empty_run)
        rows.append(s)
    fen_placement = "/".join(rows)

    flipped = False
    wk = next(((r, c) for r in range(8) for c in range(8) if grid[r][c] == "wK"), None)
    bk = next(((r, c) for r in range(8) for c in range(8) if grid[r][c] == "bK"), None)
    if wk and bk and wk[0] < bk[0]:
        flipped = True

    return DetectionResult(
        fen_placement=fen_placement,
        grid=grid,
        board_b64=_png_b64(board),
        confidence=conf,
        flipped_guess=flipped,
    )


def to_dict(d: DetectionResult) -> dict:
    return asdict(d)
