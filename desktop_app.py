"""Chess AI Assistant — native desktop app (PySide6 / Qt 6).

Single-file entry. Reuses app/detector.py and app/engine.py so detection and
analysis logic stay in sync with the Flask version.

Features
--------
- Native chessboard widget with drag-and-drop (no browser needed).
- Live screen capture via `mss` (very fast, no tab/window switching).
  Multi-monitor picker + ROI selector overlay = capture exactly the board area.
- In-process Stockfish (no HTTP roundtrip; sub-second moves at depth 12).
- Move history + Undo / Redo / PGN clipboard export.
- ELO selector for difficulty.
- Image-file load (drag a screenshot in or use the toolbar).
- Single-instance lock: a second launch focuses the existing window instead
  of spawning a duplicate (which would race on Stockfish I/O and confuse the
  move history).

Run:  python desktop_app.py
"""
from __future__ import annotations

import atexit
import os
import socket
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import chess
import cv2
import numpy as np
from PySide6.QtCore import (
    QPoint, QPointF, QRect, QRectF, QSize, Qt, QThread, QTimer, Signal, Slot,
)
from PySide6.QtGui import (
    QAction, QBrush, QColor, QGuiApplication, QImage, QKeySequence, QMouseEvent,
    QPainter, QPen, QPixmap, QShortcut, QPolygonF,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QPushButton, QSizePolicy, QSpinBox,
    QStatusBar, QToolBar, QVBoxLayout, QWidget,
)

import mss

from app.detector import detect_position
from app.engine import ChessAnalyzer, ELO_MIN, ELO_MAX

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PIECE_DIR = os.path.join(os.path.dirname(__file__), "static", "pieces")
PIECE_CODES = ["wP", "wN", "wB", "wR", "wQ", "wK", "bP", "bN", "bB", "bR", "bQ", "bK"]
FEN_TO_CODE = {"P": "wP", "N": "wN", "B": "wB", "R": "wR", "Q": "wQ", "K": "wK",
               "p": "bP", "n": "bN", "b": "bB", "r": "bR", "q": "bQ", "k": "bK"}
CODE_TO_FEN = {v: k for k, v in FEN_TO_CODE.items()}
STARTING_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

LIGHT_SQ = QColor(240, 217, 181)
DARK_SQ  = QColor(181, 136, 99)
PRIMARY_ARROW = QColor(255, 170, 28, 220)
ALT_ARROW = QColor(78, 163, 255, 200)
PICKED_BG = QColor(120, 255, 120, 90)
LEGAL_DOT = QColor(0, 0, 0, 70)
LEGAL_CAP = QColor(255, 80, 80, 160)
LAST_FROM = QColor(255, 255, 80, 110)
LAST_TO = QColor(255, 165, 0, 130)
LOWCONF_DOT = QColor(232, 76, 76)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def fen_placement_to_grid(placement: str) -> List[List[str]]:
    grid = [["" for _ in range(8)] for _ in range(8)]
    rows = placement.split("/")
    if len(rows) != 8:
        return grid
    for r, row in enumerate(rows):
        c = 0
        for ch in row:
            if ch.isdigit():
                c += int(ch)
            else:
                grid[r][c] = FEN_TO_CODE.get(ch, "")
                c += 1
    return grid


def grid_to_placement(grid: List[List[str]]) -> str:
    rows = []
    for row in grid:
        s = ""
        empty = 0
        for cell in row:
            if not cell:
                empty += 1
            else:
                if empty:
                    s += str(empty)
                    empty = 0
                s += CODE_TO_FEN[cell]
        if empty:
            s += str(empty)
        rows.append(s)
    return "/".join(rows)


def grid_to_full_fen(grid: List[List[str]], turn: str, castling_str: str = "KQkq") -> str:
    placement = grid_to_placement(grid)
    fen = f"{placement} {turn} {castling_str or '-'} - 0 1"
    return normalise_fen(fen)


def normalise_fen(fen: str) -> str:
    """Strip castling rights that the position doesn't support — frontend
    always sends KQkq from the checkboxes which python-chess would reject."""
    parts = fen.split()
    if len(parts) < 6:
        parts += ["w", "-", "-", "0", "1"][len(parts) - 1:]
    placement, turn = parts[0], parts[1]
    castling = parts[2] if parts[2] != "-" else ""
    try:
        board = chess.Board.empty()
        board.set_board_fen(placement)
    except Exception as e:
        raise ValueError(f"invalid placement: {e}")
    valid = ""
    wk, bk = board.king(chess.WHITE), board.king(chess.BLACK)
    if "K" in castling and wk == chess.E1 and board.piece_type_at(chess.H1) == chess.ROOK and board.color_at(chess.H1) == chess.WHITE:
        valid += "K"
    if "Q" in castling and wk == chess.E1 and board.piece_type_at(chess.A1) == chess.ROOK and board.color_at(chess.A1) == chess.WHITE:
        valid += "Q"
    if "k" in castling and bk == chess.E8 and board.piece_type_at(chess.H8) == chess.ROOK and board.color_at(chess.H8) == chess.BLACK:
        valid += "k"
    if "q" in castling and bk == chess.E8 and board.piece_type_at(chess.A8) == chess.ROOK and board.color_at(chess.A8) == chess.BLACK:
        valid += "q"
    board.turn = chess.WHITE if turn == "w" else chess.BLACK
    board.set_castling_fen(valid or "-")
    return board.fen()


def square_name(r: int, c: int) -> str:
    return chr(97 + c) + str(8 - r)


def rotate180(grid: List[List[str]]) -> List[List[str]]:
    return [row[::-1] for row in grid[::-1]]


# ---------------------------------------------------------------------------
# Engine worker — runs analysis in a background thread so the UI stays smooth.
# ---------------------------------------------------------------------------

@dataclass
class AnalysisJob:
    fen: str
    multipv: int
    depth: Optional[int]
    movetime_ms: Optional[int]
    elo: Optional[int]
    job_id: int


class EngineWorker(QThread):
    result_ready = Signal(int, list)   # (job_id, list of MoveAnalysis dicts)
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self._job: Optional[AnalysisJob] = None
        self._analyzer: Optional[ChessAnalyzer] = None
        self._stop = False
        self._cv = self.create_lock()

    @staticmethod
    def create_lock():
        from threading import Condition
        return Condition()

    def submit(self, job: AnalysisJob):
        with self._cv:
            self._job = job
            self._cv.notify()

    def stop(self):
        with self._cv:
            self._job = None
            self._stop = True
            self._cv.notify()

    def run(self):
        try:
            self._analyzer = ChessAnalyzer()
        except Exception as e:
            self.error.emit(f"Engine failed to start: {e}")
            return
        while True:
            with self._cv:
                while self._job is None and not self._stop:
                    self._cv.wait()
                if self._stop:
                    break
                job = self._job
                self._job = None
            try:
                moves = self._analyzer.analyze(
                    job.fen,
                    multipv=job.multipv,
                    depth=job.depth or 24,
                    movetime_ms=job.movetime_ms,
                    elo=job.elo,
                    timeout_s=20.0,
                )
                serial = []
                for m in moves:
                    serial.append({
                        "rank": m.rank, "uci": m.uci, "san": m.san,
                        "from_square": m.from_square, "to_square": m.to_square,
                        "score_cp": m.score_cp, "score_mate": m.score_mate,
                        "eval_text": m.eval_text, "pv_san": m.pv_san,
                        "depth": m.depth, "wdl": m.wdl,
                    })
                self.result_ready.emit(job.job_id, serial)
            except Exception as e:
                self.error.emit(str(e))
        if self._analyzer:
            self._analyzer.close()


# ---------------------------------------------------------------------------
# Live capture worker — uses mss to grab a region, hashes it, signals the UI
# only when the frame meaningfully changes.
# ---------------------------------------------------------------------------

class LiveCaptureWorker(QThread):
    frame_changed = Signal(np.ndarray)   # cropped BGR np array
    status = Signal(str)

    def __init__(self):
        super().__init__()
        self._monitor_idx = 1
        self._roi = None  # dict {x, y, w, h} in monitor pixels
        self._poll_ms = 200
        self._running = False
        self._busy = False  # set by UI when it's processing a frame

    def configure(self, monitor_idx: int, roi: dict, poll_ms: int):
        self._monitor_idx = monitor_idx
        self._roi = roi
        self._poll_ms = poll_ms

    def set_busy(self, on: bool):
        self._busy = on

    def stop(self):
        self._running = False

    def run(self):
        self._running = True
        prev_hash = None
        with mss.mss() as sct:
            while self._running:
                t0 = time.monotonic()
                if self._roi is None:
                    self.msleep(50); continue
                if self._busy:
                    self.msleep(self._poll_ms); continue
                mon = sct.monitors[self._monitor_idx]
                grab_box = {
                    "left": mon["left"] + self._roi["x"],
                    "top":  mon["top"]  + self._roi["y"],
                    "width":  self._roi["w"],
                    "height": self._roi["h"],
                }
                try:
                    img = np.asarray(sct.grab(grab_box))  # BGRA
                except Exception:
                    self.msleep(self._poll_ms); continue
                bgr = img[:, :, :3].copy()
                # Cheap perceptual hash on a tiny thumbnail.
                small = cv2.resize(bgr, (32, 32), interpolation=cv2.INTER_AREA)
                gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
                h = int(gray.sum())
                if h != prev_hash:
                    prev_hash = h
                    self.frame_changed.emit(bgr)
                # Sleep the remainder of poll_ms.
                elapsed_ms = (time.monotonic() - t0) * 1000
                remaining = max(1, int(self._poll_ms - elapsed_ms))
                self.msleep(remaining)


# ---------------------------------------------------------------------------
# ROI picker — full-screen translucent overlay for selecting the board region
# on a chosen monitor.
# ---------------------------------------------------------------------------

class RoiPicker(QDialog):
    """Borderless full-monitor overlay; the user drags a rectangle to define
    the capture region, then it returns it via accept() with .roi set."""

    def __init__(self, monitor_pix: QPixmap, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setCursor(Qt.CrossCursor)
        self._pix = monitor_pix
        self.setFixedSize(monitor_pix.size())
        self._origin: Optional[QPoint] = None
        self._end: Optional[QPoint] = None
        self.roi: Optional[Tuple[int, int, int, int]] = None  # (x,y,w,h) in monitor pixels
        # ESC cancels.
        QShortcut(QKeySequence("Escape"), self, activated=self.reject)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.drawPixmap(0, 0, self._pix)
        # Dim everywhere first.
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        if self._origin and self._end:
            r = QRect(self._origin, self._end).normalized()
            # Re-show the selected area at full brightness.
            p.drawPixmap(r, self._pix, r)
            pen = QPen(QColor(108, 224, 108), 3)
            p.setPen(pen)
            p.drawRect(r)
            label = f"{r.width()} × {r.height()}  — release to confirm"
            p.fillRect(QRect(r.left(), r.top() - 22, 260, 22), QColor(0, 0, 0, 200))
            p.setPen(QColor(255, 255, 255))
            p.drawText(r.left() + 6, r.top() - 6, label)

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.LeftButton:
            self._origin = e.position().toPoint()
            self._end = self._origin
            self.update()

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._origin:
            self._end = e.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, e: QMouseEvent):
        if not (self._origin and self._end):
            return
        r = QRect(self._origin, self._end).normalized()
        if r.width() < 50 or r.height() < 50:
            self.reject()
            return
        self.roi = (r.x(), r.y(), r.width(), r.height())
        self.accept()


# ---------------------------------------------------------------------------
# Chess board widget — paints squares, pieces, arrows, handles drag/drop.
# ---------------------------------------------------------------------------

class BoardWidget(QWidget):
    move_made = Signal(str, str)  # from-square, to-square (e.g. "e2", "e4")

    def __init__(self):
        super().__init__()
        self.grid = fen_placement_to_grid("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR")
        self.flipped = False
        self.confidence: Optional[List[List[float]]] = None
        self.picked: Optional[Tuple[int, int]] = None
        self.legal_dests: List[str] = []
        self.last_move: Optional[Tuple[str, str]] = None
        self.brush: Optional[str] = None  # None = move mode; '' = eraser; piece code = place
        self.arrows: List[Tuple[str, str, QColor, int]] = []
        self.setMinimumSize(400, 400)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._pieces: dict = {}
        for code in PIECE_CODES:
            p = QPixmap(os.path.join(PIECE_DIR, f"{code}.png"))
            self._pieces[code] = p
        self.setMouseTracking(True)
        self._drag_origin: Optional[Tuple[int, int]] = None

    # ---- coordinate helpers ----------------------------------------------

    def _cell_size(self) -> float:
        return min(self.width(), self.height()) / 8.0

    def _board_origin(self) -> Tuple[float, float]:
        s = self._cell_size() * 8
        return ((self.width() - s) / 2, (self.height() - s) / 2)

    def _square_at(self, p: QPointF) -> Optional[Tuple[int, int]]:
        cs = self._cell_size()
        ox, oy = self._board_origin()
        x = p.x() - ox
        y = p.y() - oy
        if x < 0 or y < 0 or x >= cs * 8 or y >= cs * 8:
            return None
        dc = int(x // cs)
        dr = int(y // cs)
        r = 7 - dr if self.flipped else dr
        c = 7 - dc if self.flipped else dc
        return (r, c)

    def _square_rect(self, r: int, c: int) -> QRectF:
        cs = self._cell_size()
        ox, oy = self._board_origin()
        dr = 7 - r if self.flipped else r
        dc = 7 - c if self.flipped else c
        return QRectF(ox + dc * cs, oy + dr * cs, cs, cs)

    def _square_center(self, file_idx: int, rank_idx: int) -> QPointF:
        cs = self._cell_size()
        ox, oy = self._board_origin()
        dc = 7 - file_idx if self.flipped else file_idx
        dr = rank_idx if self.flipped else 7 - rank_idx
        return QPointF(ox + dc * cs + cs / 2, oy + dr * cs + cs / 2)

    # ---- painting --------------------------------------------------------

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        cs = self._cell_size()
        ox, oy = self._board_origin()
        # Squares.
        for dr in range(8):
            for dc in range(8):
                r = 7 - dr if self.flipped else dr
                c = 7 - dc if self.flipped else dc
                color = LIGHT_SQ if (r + c) % 2 == 0 else DARK_SQ
                rect = QRectF(ox + dc * cs, oy + dr * cs, cs, cs)
                p.fillRect(rect, color)
        # Last move highlight.
        if self.last_move:
            for sq, col in [(self.last_move[0], LAST_FROM), (self.last_move[1], LAST_TO)]:
                file_idx = ord(sq[0]) - 97
                rank_idx = int(sq[1]) - 1
                r = 7 - rank_idx
                c = file_idx
                p.fillRect(self._square_rect(r, c), col)
        # Picked square highlight.
        if self.picked is not None:
            p.fillRect(self._square_rect(*self.picked), PICKED_BG)
        # Legal destinations (dots / capture borders).
        if self.picked is not None and self.legal_dests:
            for sq in self.legal_dests:
                file_idx = ord(sq[0]) - 97
                rank_idx = int(sq[1]) - 1
                r = 7 - rank_idx
                c = file_idx
                rect = self._square_rect(r, c)
                if self.grid[r][c]:
                    pen = QPen(LEGAL_CAP, max(3, cs * 0.06))
                    p.setPen(pen)
                    p.setBrush(Qt.NoBrush)
                    p.drawRect(rect.adjusted(2, 2, -2, -2))
                else:
                    p.setPen(Qt.NoPen)
                    p.setBrush(LEGAL_DOT)
                    cx = rect.center().x()
                    cy = rect.center().y()
                    p.drawEllipse(QPointF(cx, cy), cs * 0.13, cs * 0.13)
        # Pieces.
        for r in range(8):
            for c in range(8):
                code = self.grid[r][c]
                if not code:
                    continue
                pix = self._pieces.get(code)
                if pix is None:
                    continue
                rect = self._square_rect(r, c)
                margin = cs * 0.04
                p.drawPixmap(rect.adjusted(margin, margin, -margin, -margin).toRect(), pix)
                if self.confidence and self.confidence[r][c] < 0.55:
                    p.setBrush(LOWCONF_DOT)
                    p.setPen(Qt.NoPen)
                    p.drawEllipse(QPointF(rect.right() - cs * 0.13, rect.top() + cs * 0.13),
                                  cs * 0.06, cs * 0.06)
        # Arrows.
        for from_sq, to_sq, color, width in self.arrows:
            self._draw_arrow(p, from_sq, to_sq, color, width)
        # Coords (lower-left).
        p.setPen(QColor(60, 60, 60, 150))
        font = p.font(); font.setPointSize(max(8, int(cs * 0.10))); p.setFont(font)
        for i in range(8):
            file_lbl = chr(97 + (7 - i if self.flipped else i))
            rank_lbl = str((i + 1) if self.flipped else (8 - i))
            p.drawText(QPointF(ox + i * cs + 4, oy + 8 * cs - 4), file_lbl)
            p.drawText(QPointF(ox + 4, oy + i * cs + 14), rank_lbl)

    def _draw_arrow(self, p: QPainter, from_sq: str, to_sq: str, color: QColor, width: int):
        f_file = ord(from_sq[0]) - 97
        f_rank = int(from_sq[1]) - 1
        t_file = ord(to_sq[0]) - 97
        t_rank = int(to_sq[1]) - 1
        a = self._square_center(f_file, f_rank)
        b = self._square_center(t_file, t_rank)
        cs = self._cell_size()
        w = max(width, int(cs * 0.16))
        dx, dy = b.x() - a.x(), b.y() - a.y()
        ln = (dx * dx + dy * dy) ** 0.5 or 1
        ux, uy = dx / ln, dy / ln
        tip_back = QPointF(b.x() - ux * cs * 0.30, b.y() - uy * cs * 0.30)
        pen = QPen(color, w, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.drawLine(a, tip_back)
        # Arrowhead.
        head = w * 1.3
        px, py = -uy, ux
        head_poly = QPolygonF([
            b,
            QPointF(tip_back.x() + px * head, tip_back.y() + py * head),
            QPointF(tip_back.x() - px * head, tip_back.y() - py * head),
        ])
        p.setBrush(color)
        p.setPen(Qt.NoPen)
        p.drawPolygon(head_poly)

    # ---- mouse interaction -----------------------------------------------

    def mousePressEvent(self, e: QMouseEvent):
        sq = self._square_at(e.position())
        if sq is None:
            return
        r, c = sq
        if self.brush is not None:
            # Palette mode: place / erase.
            self.grid[r][c] = self.brush
            if self.confidence:
                self.confidence[r][c] = 1.0
            self.picked = None
            self.legal_dests = []
            self.update()
            return
        if e.button() == Qt.RightButton:
            if self.grid[r][c]:
                self.grid[r][c] = ""
                self.update()
            return
        if self.picked is None:
            if self.grid[r][c]:
                self.picked = (r, c)
                self._drag_origin = (r, c)
                self.update()
            return
        # Drop on second click.
        if self.picked == (r, c):
            self.picked = None
            self.legal_dests = []
            self.update()
            return
        from_sq = square_name(*self.picked)
        to_sq = square_name(r, c)
        self.picked = None
        self.legal_dests = []
        self.update()
        self.move_made.emit(from_sq, to_sq)

    def mouseMoveEvent(self, e: QMouseEvent):
        # Could implement drag-with-piece-following-cursor here for extra
        # polish; click-to-pick / click-to-drop already covers the workflow.
        pass

    # ---- public state setters --------------------------------------------

    def set_grid(self, grid, flipped=False, confidence=None):
        self.grid = grid
        self.flipped = flipped
        self.confidence = confidence
        self.picked = None
        self.legal_dests = []
        self.update()

    def set_arrows(self, arrows):
        self.arrows = arrows
        self.update()

    def set_last_move(self, from_sq, to_sq):
        self.last_move = (from_sq, to_sq)
        self.update()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Chess AI Assistant — desktop")
        self.resize(1280, 820)

        self.engine_worker = EngineWorker()
        self.engine_worker.result_ready.connect(self.on_engine_result)
        self.engine_worker.error.connect(lambda e: self.statusBar().showMessage("Engine: " + e, 5000))
        self.engine_worker.start()

        self.live_worker = LiveCaptureWorker()
        self.live_worker.frame_changed.connect(self.on_live_frame)
        self.live_worker.status.connect(lambda s: self.statusBar().showMessage(s, 3000))

        self._job_id = 0
        self._last_moves: list = []
        self._history: list = []  # each: dict with fen_before, fen_after, san
        self._history_idx = -1

        self._build_ui()
        self.load_fen(STARTING_FEN)

    # ---- UI construction --------------------------------------------------

    def _build_ui(self):
        # Big colourful toolbar — emoji glyphs render in colour on Windows
        # without needing custom icon files.
        tb = QToolBar()
        tb.setMovable(False)
        tb.setIconSize(QSize(28, 28))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.setStyleSheet("""
            QToolBar { spacing: 4px; padding: 6px; background: #16161a; border: 0; }
            QToolButton {
                font-size: 14px; padding: 8px 14px; border-radius: 6px;
                color: #e8e8ec; background: #2a2a30;
                border: 1px solid #383840;
            }
            QToolButton:hover { background: #34343c; }
            QToolButton:pressed { background: #4f7be8; color: white; }
            QToolButton:disabled { color: #6b6b75; background: #1f1f23; }
        """)
        self.addToolBar(tb)

        def add_action(text, slot, shortcut=None, color=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            tb.addAction(a)
            if color:
                # Tint the specific button using a per-action stylesheet on
                # the QToolButton that gets created for this action.
                btn = tb.widgetForAction(a)
                if btn:
                    btn.setStyleSheet(f"""
                        QToolButton {{
                            font-size: 14px; padding: 8px 14px; border-radius: 6px;
                            background: {color}; color: white;
                            border: 1px solid {color};
                        }}
                        QToolButton:hover {{ filter: brightness(1.15); }}
                    """)
            return a

        add_action("📁  Open image", self.open_image, color="#4f7be8")
        self.live_action = add_action("🎥  Live capture", self.start_live_capture, color="#e84c84")
        tb.addSeparator()
        add_action("🧹  Empty", self.empty_board)
        add_action("♟  Start", self.starting_position)
        self.undo_action = add_action("⟲  Undo", self.undo, "Ctrl+Z")
        self.redo_action = add_action("⟳  Redo", self.redo, "Ctrl+Y")
        add_action("📋  Copy PGN", self.copy_pgn)
        tb.addSeparator()
        add_action("↕  Flip board", self.flip_board, "F")
        add_action("⚡  Analyse", self.analyse_now, "A", color="#1ea672")

        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # Stylesheet for the position + engine panels: bigger labels and
        # combos with a pop of colour so they stand out against the dark
        # window. Applied at the group-box level so it cascades to children.
        panel_qss = """
            QGroupBox {
                font-size: 16px; font-weight: 700; color: #e8e8ec;
                background: #16161a;
                border: 1px solid #2a2a30; border-radius: 8px;
                margin-top: 10px; padding: 18px 12px 12px 12px;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 12px; padding: 0 6px;
                color: #ffd866;
            }
            QGroupBox QLabel { font-size: 14px; color: #c8c8d0; }
            QGroupBox QLineEdit, QGroupBox QComboBox {
                font-size: 14px; padding: 8px 10px;
                background: #0f0f12; color: #e8e8ec;
                border: 1px solid #383840; border-radius: 5px;
                min-height: 24px;
            }
            QGroupBox QComboBox::drop-down { border: 0; width: 24px; }
            QGroupBox QComboBox QAbstractItemView {
                background: #16161a; color: #e8e8ec;
                selection-background-color: #4f7be8;
            }
            QGroupBox QCheckBox {
                font-size: 13px; color: #c8c8d0; spacing: 8px;
                padding: 4px;
            }
            QGroupBox QCheckBox::indicator {
                width: 18px; height: 18px;
            }
            QGroupBox QCheckBox::indicator:unchecked {
                background: #0f0f12; border: 1.5px solid #383840; border-radius: 3px;
            }
            QGroupBox QCheckBox::indicator:checked {
                background: #4f7be8; border: 1.5px solid #4f7be8; border-radius: 3px;
            }
        """

        # Left: board takes most of the space.
        self.board_widget = BoardWidget()
        self.board_widget.move_made.connect(self.on_user_move)
        layout.addWidget(self.board_widget, stretch=5)

        # Right: info panel — bigger fonts, fixed minimum width so it doesn't
        # eat the board on resize.
        right_widget = QWidget()
        right_widget.setStyleSheet(panel_qss)
        right_widget.setMinimumWidth(380)
        right_widget.setMaximumWidth(520)
        right = QVBoxLayout(right_widget)
        right.setSpacing(10)
        layout.addWidget(right_widget, stretch=2)

        # Position settings.
        pos_box = QGroupBox("Position")
        pos_lay = QGridLayout(pos_box)
        self.fen_input = QLineEdit()
        self.fen_input.editingFinished.connect(self.on_fen_input)
        pos_lay.addWidget(QLabel("FEN"), 0, 0)
        pos_lay.addWidget(self.fen_input, 0, 1, 1, 3)
        pos_lay.addWidget(QLabel("Side to move"), 1, 0)
        self.turn_combo = QComboBox()
        self.turn_combo.addItem("White", "w")
        self.turn_combo.addItem("Black", "b")
        self.turn_combo.currentIndexChanged.connect(self.on_turn_changed)
        pos_lay.addWidget(self.turn_combo, 1, 1)
        pos_lay.addWidget(QLabel("I play as"), 1, 2)
        self.myside_combo = QComboBox()
        self.myside_combo.addItem("White", "w")
        self.myside_combo.addItem("Black", "b")
        self.myside_combo.currentIndexChanged.connect(self.on_my_side_changed)
        pos_lay.addWidget(self.myside_combo, 1, 3)
        pos_lay.addWidget(QLabel("ELO"), 2, 0)
        self.elo_combo = QComboBox()
        for label, val in [("Maximum", "max"), ("3190", 3190), ("3000", 3000),
                           ("2800", 2800), ("2500", 2500), ("2200", 2200),
                           ("1900", 1900), ("1600", 1600), ("1320", 1320)]:
            self.elo_combo.addItem(label, val)
        pos_lay.addWidget(self.elo_combo, 2, 1, 1, 3)
        self.cr_K = QCheckBox("White O-O"); self.cr_K.setChecked(True)
        self.cr_Q = QCheckBox("White O-O-O"); self.cr_Q.setChecked(True)
        self.cr_k = QCheckBox("Black O-O"); self.cr_k.setChecked(True)
        self.cr_q = QCheckBox("Black O-O-O"); self.cr_q.setChecked(True)
        pos_lay.addWidget(self.cr_K, 3, 0); pos_lay.addWidget(self.cr_Q, 3, 1)
        pos_lay.addWidget(self.cr_k, 3, 2); pos_lay.addWidget(self.cr_q, 3, 3)
        pos_lay.setColumnStretch(1, 1)
        pos_lay.setColumnStretch(3, 1)
        right.addWidget(pos_box)

        # Engine settings.
        eng_box = QGroupBox("Engine")
        eng_lay = QHBoxLayout(eng_box)
        eng_lay.addWidget(QLabel("Top"))
        self.multipv_combo = QComboBox()
        for v in [1, 3, 5, 7]:
            self.multipv_combo.addItem(str(v), v)
        self.multipv_combo.setCurrentText("1")  # default = best single move
        eng_lay.addWidget(self.multipv_combo)
        eng_lay.addWidget(QLabel("Time"))
        self.time_combo = QComboBox()
        for label, ms in [("0.3 s (instant)", 300), ("0.6 s (fast)", 600),
                          ("1.5 s", 1500), ("3 s", 3000), ("8 s (strong)", 8000)]:
            self.time_combo.addItem(label, ms)
        self.time_combo.setCurrentIndex(0)  # default 0.3s for snappiest response
        eng_lay.addWidget(self.time_combo)
        eng_lay.addStretch()
        right.addWidget(eng_box)

        # Analysis output.
        ana_box = QGroupBox("Analysis")
        ana_lay = QVBoxLayout(ana_box)
        self.eval_label = QLabel("Eval: –")
        self.eval_label.setStyleSheet("font-weight: 600; color: #4f7be8;")
        ana_lay.addWidget(self.eval_label)
        self.moves_list = QListWidget()
        self.moves_list.itemClicked.connect(self.on_move_clicked)
        ana_lay.addWidget(self.moves_list)
        right.addWidget(ana_box, stretch=1)

        # Move history.
        hist_box = QGroupBox("Move history")
        hist_lay = QVBoxLayout(hist_box)
        self.history_list = QListWidget()
        self.history_list.itemClicked.connect(self.on_history_clicked)
        hist_lay.addWidget(self.history_list)
        right.addWidget(hist_box, stretch=1)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("Ready. Open an image, start the live capture, or just click Analyse.")
        self._update_undoredo()

    # ---- FEN handling ----------------------------------------------------

    def current_fen(self) -> str:
        rights = ""
        if self.cr_K.isChecked(): rights += "K"
        if self.cr_Q.isChecked(): rights += "Q"
        if self.cr_k.isChecked(): rights += "k"
        if self.cr_q.isChecked(): rights += "q"
        return grid_to_full_fen(self.board_widget.grid, self.turn_combo.currentData(), rights or "-")

    def load_fen(self, fen: str, update_side: bool = True):
        """Load a FEN into the UI. When ``update_side`` is False the side-to-
        move combo is left alone so automatic flows (after-move push, undo,
        history jump) don't override the user's manual choice."""
        try:
            fen = normalise_fen(fen)
        except Exception as e:
            self.statusBar().showMessage(f"Invalid FEN: {e}", 5000)
            return
        parts = fen.split()
        grid = fen_placement_to_grid(parts[0])
        self.board_widget.set_grid(grid, self.board_widget.flipped, None)
        if update_side:
            self.turn_combo.setCurrentIndex(0 if parts[1] == "w" else 1)
        cr = parts[2] if len(parts) > 2 else "-"
        self.cr_K.setChecked("K" in cr); self.cr_Q.setChecked("Q" in cr)
        self.cr_k.setChecked("k" in cr); self.cr_q.setChecked("q" in cr)
        self.fen_input.setText(fen)
        self.fen_input.setCursorPosition(0)

    def refresh_fen_display(self):
        try:
            self.fen_input.setText(self.current_fen())
            self.fen_input.setCursorPosition(0)
        except Exception:
            pass

    def on_fen_input(self):
        self.load_fen(self.fen_input.text())
        self.clear_history()

    @Slot()
    def on_my_side_changed(self):
        """User changed which colour they're playing. Flip the board so
        their pieces sit at the bottom and re-run the analyser so the eval
        bar / arrows update for the new perspective."""
        my_side = self.myside_combo.currentData()
        # Convention: white-perspective = not flipped, black-perspective = flipped.
        self.board_widget.flipped = (my_side == "b")
        self.board_widget.update()
        self.refresh_fen_display()
        self.statusBar().showMessage(
            f"Switched to playing as {'white' if my_side == 'w' else 'black'}.", 3000)
        self.analyse_now()

    @Slot()
    def on_turn_changed(self):
        """Side-to-move changed manually — re-analyse from the new POV."""
        self.refresh_fen_display()
        self.analyse_now()

    # ---- Image → board ---------------------------------------------------

    def open_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open chess board image",
                                              "", "Images (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        with open(path, "rb") as f:
            data = f.read()
        self.detect_bytes(data, source="file")

    def detect_bytes(self, data: bytes, source: str = "image"):
        """Update the grid from a screenshot. Side-to-move is **never**
        changed automatically — it stays whatever the user set in the combo.
        Only the piece layout updates."""
        try:
            res = detect_position(data)
        except Exception as e:
            self.statusBar().showMessage(f"Detection failed: {e}", 5000)
            return
        grid = res.grid
        conf = res.confidence
        flipped = res.flipped_guess
        if flipped:
            grid = rotate180(grid)
            conf = rotate180(conf) if conf else None
        new_fen = grid_to_placement(grid)
        cur_fen = grid_to_placement(self.board_widget.grid)
        self.board_widget.set_grid(grid, flipped=flipped, confidence=conf)
        self.refresh_fen_display()
        if new_fen == cur_fen:
            self.statusBar().showMessage("Same position — no change.", 2000)
            return
        side = self.turn_combo.currentData()
        self.statusBar().showMessage(
            f"Position updated. Analysing as {'white' if side == 'w' else 'black'} to move…", 3000)
        self.analyse_now()

    def _infer_turn(self, old, new):
        white_moved = black_moved = False
        for r in range(8):
            for c in range(8):
                if old[r][c] != new[r][c]:
                    for cell in (old[r][c], new[r][c]):
                        if cell.startswith("w"): white_moved = True
                        elif cell.startswith("b"): black_moved = True
        if white_moved and not black_moved: return "b"
        if black_moved and not white_moved: return "w"
        return None

    # ---- Engine analysis -------------------------------------------------

    def analyse_now(self):
        """All analyses are bounded by the user-selected time (movetime cap),
        not by depth. Stockfish iterates as deep as it can in that window —
        at 0.6 s on a multi-core machine that's typically depth 18-22, more
        than strong enough for a real-time assistant."""
        try:
            fen = self.current_fen()
        except Exception as e:
            self.statusBar().showMessage(f"FEN error: {e}", 4000)
            return
        elo_data = self.elo_combo.currentData()
        elo = None if elo_data == "max" else int(elo_data)
        self._job_id += 1
        movetime = int(self.time_combo.currentData())
        self.engine_worker.submit(AnalysisJob(
            fen=fen,
            multipv=int(self.multipv_combo.currentData()),
            depth=None,
            movetime_ms=movetime,
            elo=elo,
            job_id=self._job_id,
        ))
        self.statusBar().showMessage(f"Analysing ({movetime} ms)…")

    # alias kept for live-capture compatibility
    analyse_fast = analyse_now

    @Slot(int, list)
    def on_engine_result(self, job_id: int, moves: list):
        if job_id != self._job_id:
            return
        self._last_moves = moves
        self.moves_list.clear()
        for m in moves:
            wdl = ""
            if m["wdl"]:
                w, d, l = m["wdl"]
                tot = max(1, w + d + l)
                wdl = f"   W {round(100*w/tot)}% D {round(100*d/tot)}% L {round(100*l/tot)}%"
            text = f'{m["rank"]}.  {m["san"]:>6}   {m["eval_text"]:>6}   {" ".join(m["pv_san"][:6])}{wdl}'
            it = QListWidgetItem(text)
            self.moves_list.addItem(it)
        if moves:
            top = moves[0]
            stm = self.turn_combo.currentData()
            mine = self.myside_combo.currentData()
            tag = "YOUR move" if stm == mine else "Opponent's move"
            self.eval_label.setText(f'{tag} → {top["san"]}   {top["eval_text"]}   d{top["depth"]}')
            arrows = []
            for i, m in enumerate(moves):
                color = PRIMARY_ARROW if i == 0 else ALT_ARROW
                w = 18 if i == 0 else 9
                arrows.append((m["from_square"], m["to_square"], color, w))
            self.board_widget.set_arrows(arrows)
            self.statusBar().showMessage(f'Best: {top["san"]}  ({top["eval_text"]})', 4000)
        else:
            self.eval_label.setText("Eval: – (game over?)")
            self.board_widget.set_arrows([])

    @Slot(QListWidgetItem)
    def on_move_clicked(self, item: QListWidgetItem):
        idx = self.moves_list.row(item)
        if idx < 0 or idx >= len(self._last_moves):
            return
        arrows = []
        for i, m in enumerate(self._last_moves):
            color = PRIMARY_ARROW if i == idx else ALT_ARROW
            w = 18 if i == idx else 9
            arrows.append((m["from_square"], m["to_square"], color, w))
        self.board_widget.set_arrows(arrows)

    # ---- Move handling ---------------------------------------------------

    @Slot(str, str)
    def on_user_move(self, from_sq: str, to_sq: str):
        try:
            fen = self.current_fen()
            board = chess.Board(fen)
        except Exception as e:
            self.statusBar().showMessage(f"FEN error: {e}", 4000)
            return
        sq_src = chess.parse_square(from_sq)
        sq_dst = chess.parse_square(to_sq)
        piece = board.piece_at(sq_src)
        promo = ""
        if piece and piece.piece_type == chess.PAWN and chess.square_rank(sq_dst) in (0, 7):
            promo = "q"
        try:
            move = chess.Move.from_uci(from_sq + to_sq + promo)
        except Exception:
            self.statusBar().showMessage(f"Invalid UCI {from_sq}{to_sq}", 4000)
            return
        if move not in board.legal_moves:
            legals = sorted(m.uci() for m in board.legal_moves if m.from_square == sq_src)
            self.statusBar().showMessage(
                f"Illegal {from_sq}-{to_sq}. Legal from {from_sq}: {', '.join(legals) or 'none'}", 6000)
            return
        san = board.san(move)
        fen_before = board.fen()
        board.push(move)
        fen_after = board.fen()
        self._push_history({"fen_before": fen_before, "fen_after": fen_after, "san": san, "uci": move.uci()})
        self.load_fen(fen_after, update_side=False)
        self.board_widget.set_last_move(from_sq, to_sq)
        self.analyse_now()
        suffix = ""
        if board.is_checkmate(): suffix = " — checkmate!"
        elif board.is_stalemate(): suffix = " — stalemate."
        elif board.is_check(): suffix = " — check."
        self.statusBar().showMessage(f"You played {san}{suffix}", 4000)

    # ---- History ---------------------------------------------------------

    def _push_history(self, entry: dict):
        self._history = self._history[: self._history_idx + 1]
        self._history.append(entry)
        self._history_idx = len(self._history) - 1
        self._render_history()
        self._update_undoredo()

    def _render_history(self):
        self.history_list.clear()
        for i, h in enumerate(self._history):
            num = (i // 2) + 1
            prefix = f"{num}. " if i % 2 == 0 else "   …"
            it = QListWidgetItem(f'{prefix} {h["san"]}')
            if i == self._history_idx:
                it.setBackground(QBrush(QColor(79, 123, 232)))
                it.setForeground(QBrush(QColor(255, 255, 255)))
            self.history_list.addItem(it)

    def _update_undoredo(self):
        self.undo_action.setEnabled(self._history_idx >= 0)
        self.redo_action.setEnabled(self._history_idx < len(self._history) - 1)

    def clear_history(self):
        self._history = []
        self._history_idx = -1
        self._render_history()
        self._update_undoredo()

    def undo(self):
        if self._history_idx < 0:
            return
        self._history_idx -= 1
        fen = self._history[0]["fen_before"] if self._history_idx < 0 else self._history[self._history_idx]["fen_after"]
        self.load_fen(fen, update_side=False)
        self._render_history()
        self._update_undoredo()
        self.analyse_now()

    def redo(self):
        if self._history_idx >= len(self._history) - 1:
            return
        self._history_idx += 1
        self.load_fen(self._history[self._history_idx]["fen_after"], update_side=False)
        self._render_history()
        self._update_undoredo()
        self.analyse_now()

    @Slot(QListWidgetItem)
    def on_history_clicked(self, item: QListWidgetItem):
        idx = self.history_list.row(item)
        if idx < 0 or idx >= len(self._history):
            return
        self._history_idx = idx
        self.load_fen(self._history[idx]["fen_after"], update_side=False)
        self._render_history()
        self._update_undoredo()
        self.analyse_now()

    # ---- PGN -------------------------------------------------------------

    def copy_pgn(self):
        if not self._history:
            self.statusBar().showMessage("No moves played yet.", 3000)
            return
        start_fen = self._history[0]["fen_before"]
        is_standard = start_fen.startswith("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq")
        lines = ['[Event "Chess AI Assistant"]', f'[Date "{time.strftime("%Y-%m-%d")}"]',
                 '[White "?"]', '[Black "?"]', '[Result "*"]']
        if not is_standard:
            lines += ['[SetUp "1"]', f'[FEN "{start_fen}"]']
        lines.append("")
        body = ""
        for i, h in enumerate(self._history):
            if i % 2 == 0:
                body += f'{(i // 2) + 1}. '
            body += h["san"] + " "
        body += "*"
        lines.append(body.strip())
        pgn = "\n".join(lines)
        QGuiApplication.clipboard().setText(pgn)
        self.statusBar().showMessage(f"PGN copied ({len(self._history)} plies).", 4000)

    # ---- Misc actions ----------------------------------------------------

    def empty_board(self):
        self.board_widget.set_grid([["" for _ in range(8)] for _ in range(8)])
        for cb in (self.cr_K, self.cr_Q, self.cr_k, self.cr_q):
            cb.setChecked(False)
        self.turn_combo.setCurrentIndex(0)
        self.refresh_fen_display()
        self.clear_history()
        self.board_widget.set_arrows([])
        self.moves_list.clear()
        self.eval_label.setText("Eval: –")
        self.statusBar().showMessage("Empty board.", 2000)

    def starting_position(self):
        self.board_widget.flipped = False
        self.load_fen(STARTING_FEN)
        self.clear_history()
        self.statusBar().showMessage("Starting position. White to move.", 2000)

    def flip_board(self):
        self.board_widget.flipped = not self.board_widget.flipped
        self.board_widget.update()

    # ---- Live capture ----------------------------------------------------

    def start_live_capture(self):
        # Pick monitor + ROI.
        with mss.mss() as sct:
            monitors = sct.monitors[1:]  # index 0 is the union of all
            if not monitors:
                self.statusBar().showMessage("No monitor detected.", 4000)
                return
            # Show the first monitor's full bitmap and let user pick.
            chosen_idx = 1
            if len(monitors) > 1:
                # Cycle through monitors with a tiny dialog.
                from PySide6.QtWidgets import QInputDialog
                items = [f"Monitor {i+1}: {m['width']}×{m['height']}" for i, m in enumerate(monitors)]
                choice, ok = QInputDialog.getItem(self, "Choose monitor",
                    "Which screen has the chess board?", items, 0, False)
                if not ok:
                    return
                chosen_idx = items.index(choice) + 1
            grab = sct.grab(sct.monitors[chosen_idx])
            arr = np.asarray(grab)  # BGRA
            h, w = arr.shape[:2]
            qimg = QImage(arr.data, w, h, arr.strides[0], QImage.Format_RGB32)
            pix = QPixmap.fromImage(qimg.copy())
        # Position the picker over the chosen monitor.
        screens = QGuiApplication.screens()
        target_screen = screens[min(chosen_idx - 1, len(screens) - 1)]
        picker = RoiPicker(pix, self)
        picker.setGeometry(target_screen.geometry())
        if picker.exec() != QDialog.Accepted or picker.roi is None:
            self.statusBar().showMessage("Live capture cancelled.", 3000)
            return
        x, y, w, h = picker.roi
        self.live_worker.configure(chosen_idx, {"x": x, "y": y, "w": w, "h": h}, poll_ms=200)
        if not self.live_worker.isRunning():
            self.live_worker.start()
        self.live_action.setEnabled(False)
        self.statusBar().showMessage(f"Live capture running on monitor {chosen_idx}, ROI {w}×{h}. Press 'Stop live' to end.")
        # Add a stop button to the toolbar lazily.
        if not hasattr(self, "_stop_live_action"):
            self._stop_live_action = QAction("⏹ Stop live", self)
            self._stop_live_action.triggered.connect(self.stop_live_capture)
            self.findChildren(QToolBar)[0].addAction(self._stop_live_action)

    def stop_live_capture(self):
        self.live_worker.stop()
        self.live_action.setEnabled(True)
        if hasattr(self, "_stop_live_action"):
            self.findChildren(QToolBar)[0].removeAction(self._stop_live_action)
            del self._stop_live_action
        self.statusBar().showMessage("Live capture stopped.", 3000)

    @Slot(np.ndarray)
    def on_live_frame(self, bgr: np.ndarray):
        # Push detection through the same path as a file image.
        ok, buf = cv2.imencode(".png", bgr)
        if not ok:
            return
        self.live_worker.set_busy(True)
        try:
            self.detect_bytes(buf.tobytes(), source="live")
        finally:
            self.live_worker.set_busy(False)

    # ---- Lifecycle -------------------------------------------------------

    def closeEvent(self, e):
        # Tear down workers in order: live capture first (releases the screen
        # grab), then the engine (which sends "quit" to Stockfish so the
        # subprocess can exit cleanly). If the engine is mid-search the call
        # may need a couple of seconds — wait long enough that Stockfish gets
        # the quit, but force-kill on timeout so we never leave zombies.
        try:
            self.live_worker.stop()
            self.live_worker.wait(2000)
        except Exception:
            pass
        try:
            self.engine_worker.stop()
            self.engine_worker.wait(3000)
        except Exception:
            pass
        super().closeEvent(e)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

_SINGLE_INSTANCE_PORT = 53217


def acquire_single_instance() -> Optional[socket.socket]:
    """Return a bound listening socket if no other instance is running, else
    None. The socket is intentionally leaked for the lifetime of the process
    so the OS releases it when we exit (clean OR crashed)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    try:
        s.bind(("127.0.0.1", _SINGLE_INSTANCE_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def main():
    lock = acquire_single_instance()
    if lock is None:
        # Another copy is already running. We could pop a Qt dialog, but a
        # bare QApplication with no event loop crashes the second pythonw
        # process; a Windows MessageBoxW (built into user32) is reliable and
        # avoids spinning up Qt at all.
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0,
                "Chess AI Assistant is already running.\n\n"
                "Look for the existing window in your taskbar.",
                "Chess AI Assistant",
                0x40,  # MB_ICONINFORMATION
            )
        except Exception:
            print("Chess AI Assistant is already running.", file=sys.stderr)
        sys.exit(0)
    atexit.register(lambda: lock.close() if lock else None)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # Dark palette for a less-shouty look.
    from PySide6.QtGui import QPalette
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(31, 31, 35))
    pal.setColor(QPalette.WindowText, Qt.white)
    pal.setColor(QPalette.Base, QColor(22, 22, 26))
    pal.setColor(QPalette.AlternateBase, QColor(31, 31, 35))
    pal.setColor(QPalette.Text, Qt.white)
    pal.setColor(QPalette.Button, QColor(40, 40, 46))
    pal.setColor(QPalette.ButtonText, Qt.white)
    pal.setColor(QPalette.Highlight, QColor(79, 123, 232))
    pal.setColor(QPalette.HighlightedText, Qt.white)
    app.setPalette(pal)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
