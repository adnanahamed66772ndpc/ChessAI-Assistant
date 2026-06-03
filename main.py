"""Chess AI Assistant - local Flask server.

Routes
------
  GET  /                    → main UI
  POST /api/detect          → upload an image, returns detected position
  POST /api/analyze         → {fen, multipv, depth, elo?} → top moves
  POST /api/move            → {fen, from, to, promotion?, multipv?, depth?, elo?}
  POST /api/legal-moves     → {fen, from} → list of legal destinations
  POST /api/fix-fen         → {fen|placement, turn?, castling?} → cleaned FEN
"""
from __future__ import annotations

import logging
import os
import sys
import traceback
import webbrowser
from threading import Timer
from typing import Optional, Tuple

import chess
from flask import Flask, jsonify, render_template, request

from app.engine import ChessAnalyzer, to_dict as engine_to_dict, ELO_MIN, ELO_MAX
from app.detector import detect_position, to_dict as detector_to_dict

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
# Don't cache static files in dev so JS/CSS edits show up after a normal refresh.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

_analyzer: ChessAnalyzer | None = None


def get_analyzer() -> ChessAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = ChessAnalyzer()
    return _analyzer


# ---------------------------------------------------------------------------
# FEN repair
# ---------------------------------------------------------------------------

_FEN_DEFAULTS = ["", "w", "-", "-", "0", "1"]


def _normalise_fen(fen: str) -> Tuple[str, list]:
    """Take a possibly-rough FEN and return (cleaned_fen, notes).

    Strips castling rights that don't match the actual king/rook positions —
    the frontend always sends KQkq from its checkboxes which python-chess
    rejects as invalid for many positions otherwise.
    """
    notes: list = []
    parts = fen.split()
    if not parts:
        raise ValueError("empty fen")
    # Pad missing trailing fields with sensible defaults so users can paste
    # bare placements ("rnbq..." with no side-to-move etc).
    while len(parts) < 6:
        parts.append(_FEN_DEFAULTS[len(parts)])
    placement = parts[0]
    turn = parts[1] if parts[1] in ("w", "b") else "w"
    castling = parts[2] if parts[2] else "-"

    try:
        board = chess.Board.empty()
        board.set_board_fen(placement)
    except Exception as e:
        raise ValueError(f"invalid placement: {e}") from e
    board.turn = chess.WHITE if turn == "w" else chess.BLACK

    # Filter castling rights to only those the position can support.
    valid = ""
    if castling != "-":
        wk = board.king(chess.WHITE)
        bk = board.king(chess.BLACK)
        if "K" in castling and wk == chess.E1 and board.piece_type_at(chess.H1) == chess.ROOK and board.color_at(chess.H1) == chess.WHITE:
            valid += "K"
        if "Q" in castling and wk == chess.E1 and board.piece_type_at(chess.A1) == chess.ROOK and board.color_at(chess.A1) == chess.WHITE:
            valid += "Q"
        if "k" in castling and bk == chess.E8 and board.piece_type_at(chess.H8) == chess.ROOK and board.color_at(chess.H8) == chess.BLACK:
            valid += "k"
        if "q" in castling and bk == chess.E8 and board.piece_type_at(chess.A8) == chess.ROOK and board.color_at(chess.A8) == chess.BLACK:
            valid += "q"
    if not valid:
        valid = "-"
    if valid != castling:
        notes.append(f"castling adjusted {castling}->{valid}")

    board.set_castling_fen(valid)
    fixed = board.fen()
    # board.fen() doesn't preserve the side-to-move set above? It does — the
    # field is rendered. Counts default to 0 1 which is fine.
    return fixed, notes


def _parse_elo(body: dict) -> Optional[int]:
    raw = body.get("elo")
    if raw in (None, "", "max", 0, "0"):
        return None
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return None
    if v < ELO_MIN or v > ELO_MAX:
        return None
    return v


def _safe_int(value, default: int, lo: int, hi: int) -> int:
    """Coerce ``value`` to int and clamp to [lo, hi]. Falls back to ``default``
    if value is missing or unparsable. Never raises — used for HTTP body
    fields where a malformed input should not turn into a 500."""
    if value is None or value == "":
        v = default
    else:
        try:
            v = int(value)
        except (TypeError, ValueError):
            v = default
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/detect", methods=["POST"])
def api_detect():
    if "image" not in request.files:
        return jsonify({"error": "no image uploaded"}), 400
    f = request.files["image"]
    data = f.read()
    if not data:
        return jsonify({"error": "empty file"}), 400
    try:
        res = detect_position(data)
        return jsonify({"ok": True, **detector_to_dict(res)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/fix-fen", methods=["POST"])
def api_fix_fen():
    body = request.get_json(silent=True) or {}
    fen = (body.get("fen") or "").strip()
    if not fen:
        return jsonify({"error": "fen required"}), 400
    try:
        fixed, notes = _normalise_fen(fen)
        return jsonify({"ok": True, "fen": fixed, "notes": notes})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    body = request.get_json(silent=True) or {}
    fen = (body.get("fen") or "").strip()
    multipv = _safe_int(body.get("multipv"), 5, 1, 10)
    depth = _safe_int(body.get("depth"), 20, 6, 28)
    elo = _parse_elo(body)
    if not fen:
        return jsonify({"error": "fen required"}), 400
    try:
        fen, notes = _normalise_fen(fen)
        chess.Board(fen)  # final sanity
    except Exception as e:
        return jsonify({"error": f"invalid fen: {e}"}), 400
    try:
        moves = get_analyzer().analyze(fen, multipv=multipv, depth=depth, elo=elo)
        return jsonify({
            "ok": True,
            "moves": engine_to_dict(moves),
            "fen": fen,
            "notes": notes,
            "elo": elo or "max",
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/move", methods=["POST"])
def api_move():
    body = request.get_json(silent=True) or {}
    fen = (body.get("fen") or "").strip()
    src = (body.get("from") or "").strip().lower()
    dst = (body.get("to") or "").strip().lower()
    promotion = (body.get("promotion") or "").strip().lower()
    multipv = _safe_int(body.get("multipv"), 5, 1, 10)
    depth = _safe_int(body.get("depth"), 20, 6, 28)
    elo = _parse_elo(body)

    if not (fen and len(src) == 2 and len(dst) == 2):
        return jsonify({"error": "fen, from, to required"}), 400

    try:
        fen, _ = _normalise_fen(fen)
        board = chess.Board(fen)
    except Exception as e:
        return jsonify({"error": f"invalid fen: {e}"}), 400

    try:
        sq_src = chess.parse_square(src)
        sq_dst = chess.parse_square(dst)
    except ValueError:
        return jsonify({"error": f"invalid squares {src}->{dst}"}), 400

    piece = board.piece_at(sq_src)
    is_promotion = (
        piece is not None
        and piece.piece_type == chess.PAWN
        and chess.square_rank(sq_dst) in (0, 7)
    )
    promo_char = ""
    if is_promotion:
        promo_char = promotion if promotion in ("q", "r", "b", "n") else "q"

    try:
        move = chess.Move.from_uci(src + dst + promo_char)
    except Exception:
        return jsonify({"error": f"invalid uci: {src}{dst}{promo_char}"}), 400

    if move not in board.legal_moves:
        piece_str = piece.symbol() if piece else "(empty)"
        logging.warning(
            "ILLEGAL: %s->%s side=%s piece=%s fen=%s",
            src, dst, ("white" if board.turn else "black"), piece_str, fen,
        )
        legal = sorted(m.uci() for m in board.legal_moves if m.from_square == sq_src)
        return jsonify({
            "error": f"illegal move {src}->{dst} for side {('white' if board.turn else 'black')}; piece on {src}: {piece_str}",
            "legal_from_source": legal,
            "fen_received": fen,
        }), 400

    san = board.san(move)
    board.push(move)
    new_fen = board.fen()

    moves = []
    if not board.is_game_over():
        moves = engine_to_dict(get_analyzer().analyze(new_fen, multipv=multipv, depth=depth, elo=elo))

    return jsonify({
        "ok": True,
        "fen": new_fen,
        "san": san,
        "moves": moves,
        "elo": elo or "max",
        "is_check": board.is_check(),
        "is_checkmate": board.is_checkmate(),
        "is_stalemate": board.is_stalemate(),
        "is_game_over": board.is_game_over(),
    })


@app.route("/api/legal-moves", methods=["POST"])
def api_legal_moves():
    body = request.get_json(silent=True) or {}
    fen = (body.get("fen") or "").strip()
    src = (body.get("from") or "").strip().lower()
    if not fen or len(src) != 2:
        return jsonify({"error": "fen and from required"}), 400
    try:
        fen, _ = _normalise_fen(fen)
        board = chess.Board(fen)
        sq = chess.parse_square(src)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    dests = sorted({chess.square_name(m.to_square) for m in board.legal_moves if m.from_square == sq})
    return jsonify({"ok": True, "destinations": dests})


# ---------------------------------------------------------------------------
# Server lifecycle
# ---------------------------------------------------------------------------

@app.teardown_appcontext
def _shutdown(_=None):
    pass


def _open_browser(port: int):
    try:
        webbrowser.open_new_tab(f"http://127.0.0.1:{port}/")
    except Exception:
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    if "--no-browser" not in sys.argv:
        Timer(1.2, _open_browser, args=(port,)).start()
    try:
        app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
    finally:
        if _analyzer is not None:
            _analyzer.close()
