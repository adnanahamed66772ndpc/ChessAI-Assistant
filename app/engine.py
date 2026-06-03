"""Stockfish engine wrapper using python-chess.

Configures Stockfish 17.1 for high strength by default (all CPU cores, 1 GB
hash, NNUE evaluation enabled). Optional ELO limit lets the user pick a
weaker setting for casual play.

Concurrency: only one analyse() call at a time per ChessAnalyzer (UCI is a
serial protocol). The Lock serialises requests; if multiple HTTP requests
arrive at once they queue rather than corrupting the engine.

Robustness: every analyse() catches engine errors and transparently restarts
the Stockfish subprocess so a single bad position doesn't take down the app.
"""
from __future__ import annotations

import logging
import os
import platform
import sys
import threading
from dataclasses import dataclass, asdict
from typing import List, Optional

import chess
import chess.engine


def _default_stockfish_path() -> str:
    """Locate the bundled Stockfish binary.

    PyInstaller `--onedir` builds expose ``sys._MEIPASS`` pointing at the
    extracted bundle root; in that mode the binary sits under
    ``<bundle>/stockfish/``. When running from source we look one level up
    from this file (project root) instead.

    The executable name is OS-specific (``stockfish.exe`` on Windows,
    plain ``stockfish`` on Linux/macOS)."""
    bin_name = "stockfish.exe" if platform.system() == "Windows" else "stockfish"
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return os.path.join(bundle_root, "stockfish", bin_name)
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "stockfish",
        bin_name,
    )


STOCKFISH_PATH = _default_stockfish_path()

# Stockfish 17.1 ELO range from `uci` probe.
ELO_MIN = 1320
ELO_MAX = 3190


@dataclass
class MoveAnalysis:
    rank: int
    uci: str
    san: str
    from_square: str
    to_square: str
    score_cp: Optional[int]
    score_mate: Optional[int]
    eval_text: str
    pv: List[str]
    pv_san: List[str]
    depth: int
    wdl: Optional[List[int]]  # [win, draw, loss] in 0..1000, side-to-move POV


class ChessAnalyzer:
    """Long-lived analyzer that keeps a Stockfish process running."""

    def __init__(
        self,
        path: str = STOCKFISH_PATH,
        threads: Optional[int] = None,
        hash_mb: int = 1024,
    ):
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Stockfish not found at {path}")
        self.path = path
        cpu = os.cpu_count() or 1
        self._configured_threads = int(threads) if threads else max(1, cpu - 1)
        self._configured_hash = int(hash_mb)
        self._lock = threading.Lock()
        self._engine: Optional[chess.engine.SimpleEngine] = None
        self._elo_limit: Optional[int] = None
        self._spawn_engine()

    def _spawn_engine(self) -> None:
        # Hide Stockfish's console on Windows. Without this, a windowed parent
        # (pythonw / PyInstaller --windowed exe) that has no console of its
        # own will get a fresh black cmd window every time it spawns the
        # engine subprocess. CREATE_NO_WINDOW (0x08000000) suppresses it.
        popen_kwargs = {}
        if platform.system() == "Windows":
            popen_kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        self._engine = chess.engine.SimpleEngine.popen_uci(self.path, **popen_kwargs)
        self._engine.configure({
            "Threads": self._configured_threads,
            "Hash": self._configured_hash,
            "UCI_ShowWDL": True,
            "Skill Level": 20,
            "UCI_LimitStrength": False,
        })
        # Reapply ELO limit if there was one before the restart.
        if self._elo_limit is not None:
            try:
                self._engine.configure({
                    "UCI_LimitStrength": True,
                    "UCI_Elo": self._elo_limit,
                })
            except chess.engine.EngineError:
                self._elo_limit = None

    def _ensure_alive(self) -> None:
        """Re-spawn the engine if the previous process died."""
        if self._engine is None:
            self._spawn_engine()
            return
        try:
            # Touch the engine; if it's dead this raises.
            _ = self._engine.options
        except (chess.engine.EngineTerminatedError, AttributeError, OSError):
            try:
                self._engine.close()
            except Exception:
                pass
            self._engine = None
            self._spawn_engine()

    def close(self) -> None:
        with self._lock:
            if self._engine is not None:
                try:
                    self._engine.quit()
                except Exception:
                    pass
                self._engine = None

    def set_elo(self, elo: Optional[int]) -> None:
        """Cap engine strength to a target ELO. None or 0 disables the cap
        and runs at full strength. Safe to call before the engine is spawned;
        in that case the limit is stored and applied at next spawn."""
        if elo is not None:
            try:
                elo = int(elo)
            except (TypeError, ValueError):
                elo = None
        if elo is not None and not (ELO_MIN <= elo <= ELO_MAX):
            elo = None
        # Store the desired limit even if the engine isn't up yet, so it
        # survives a restart via _spawn_engine().
        self._elo_limit = elo
        if self._engine is None:
            return
        try:
            if elo is not None:
                self._engine.configure({
                    "UCI_LimitStrength": True,
                    "UCI_Elo": elo,
                })
            else:
                self._engine.configure({"UCI_LimitStrength": False})
        except (chess.engine.EngineError, chess.engine.EngineTerminatedError):
            # Engine refused / died — let _ensure_alive() handle on next call.
            pass

    def analyze(
        self,
        fen: str,
        multipv: int = 5,
        depth: int = 20,
        movetime_ms: Optional[int] = None,
        elo: Optional[int] = None,
        timeout_s: float = 30.0,
    ) -> List[MoveAnalysis]:
        with self._lock:
            return self._analyze_locked(fen, multipv, depth, movetime_ms, elo, timeout_s)

    def _analyze_locked(
        self,
        fen: str,
        multipv: int,
        depth: int,
        movetime_ms: Optional[int],
        elo: Optional[int],
        timeout_s: float,
    ) -> List[MoveAnalysis]:
        self._ensure_alive()

        if elo != self._elo_limit:
            self.set_elo(elo)

        board = chess.Board(fen)
        if board.is_game_over():
            return []
        # Reject positions Stockfish can't reason about — multiple kings of
        # the same colour, missing kings, opponent already in check on our
        # move, etc. The board editor can produce these; we don't want to
        # crash the engine subprocess over a malformed setup.
        status = board.status()
        if status & (
            chess.STATUS_NO_WHITE_KING
            | chess.STATUS_NO_BLACK_KING
            | chess.STATUS_TOO_MANY_WHITE_KINGS
            | chess.STATUS_TOO_MANY_BLACK_KINGS
            | chess.STATUS_OPPOSITE_CHECK
            | chess.STATUS_TOO_MANY_CHECKERS
        ):
            logging.warning("Skipping invalid position: status=%s fen=%s", status, fen)
            return []

        # Always cap by absolute wall time so a degenerate search can't hang
        # the request. When the caller asked for a fixed depth we still pass
        # a generous time bound as a safety net.
        if movetime_ms is not None:
            limit = chess.engine.Limit(time=min(movetime_ms / 1000.0, timeout_s))
        else:
            limit = chess.engine.Limit(depth=depth, time=timeout_s)

        try:
            info = self._engine.analyse(board, limit, multipv=multipv)
        except chess.engine.EngineTerminatedError:
            logging.warning("Stockfish died mid-analysis; restarting")
            try:
                self._engine.close()
            except Exception:
                pass
            self._engine = None
            self._spawn_engine()
            try:
                info = self._engine.analyse(board, limit, multipv=multipv)
            except chess.engine.EngineTerminatedError as e:
                # Restart didn't help — surface a clean error rather than
                # leaving the caller with a half-dead engine reference.
                raise RuntimeError(
                    "Stockfish failed twice in a row; check the engine binary"
                ) from e

        results: List[MoveAnalysis] = []
        for i, line in enumerate(info, start=1):
            pv_moves: List[chess.Move] = line.get("pv", [])
            if not pv_moves:
                continue
            best = pv_moves[0]

            score = line.get("score")
            cp_val: Optional[int] = None
            mate_val: Optional[int] = None
            eval_text = "0.00"
            if score is not None:
                pov = score.pov(board.turn)
                if pov.is_mate():
                    mate_val = pov.mate()
                    eval_text = f"M{mate_val}" if mate_val and mate_val > 0 else f"-M{abs(mate_val)}"
                else:
                    cp_val = pov.score()
                    eval_text = f"{cp_val/100:+.2f}"

            wdl_data = line.get("wdl")
            wdl_list: Optional[List[int]] = None
            if wdl_data is not None:
                pov_w = wdl_data.pov(board.turn)
                wdl_list = [pov_w.wins, pov_w.draws, pov_w.losses]

            tmp = board.copy()
            pv_uci: List[str] = []
            pv_san: List[str] = []
            for m in pv_moves:
                pv_uci.append(m.uci())
                try:
                    pv_san.append(tmp.san(m))
                    tmp.push(m)
                except Exception:
                    break

            san = board.san(best)
            results.append(
                MoveAnalysis(
                    rank=i,
                    uci=best.uci(),
                    san=san,
                    from_square=chess.square_name(best.from_square),
                    to_square=chess.square_name(best.to_square),
                    score_cp=cp_val,
                    score_mate=mate_val,
                    eval_text=eval_text,
                    pv=pv_uci,
                    pv_san=pv_san,
                    depth=line.get("depth", 0),
                    wdl=wdl_list,
                )
            )

        return results


def to_dict(moves: List[MoveAnalysis]) -> List[dict]:
    return [asdict(m) for m in moves]
