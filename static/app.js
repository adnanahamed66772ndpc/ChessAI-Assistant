// Chess AI Assistant — frontend.
//
// Grid model: 8 rows × 8 cols. Row 0 = rank 8 (FEN top), row 7 = rank 1.
// Each cell is "" or a piece code (wP, wN, wB, wR, wQ, wK, bP, bN, bB, bR, bQ, bK).
// `flipped` is purely a *visual* flag: it inverts how rows/cols map to screen
// coordinates but never mutates `grid` itself. That keeps gridToPlacement
// always producing standard FEN regardless of how the board is shown.

const FEN_TO_CODE = {
  P:'wP', N:'wN', B:'wB', R:'wR', Q:'wQ', K:'wK',
  p:'bP', n:'bN', b:'bB', r:'bR', q:'bQ', k:'bK',
};
const CODE_TO_FEN = Object.fromEntries(Object.entries(FEN_TO_CODE).map(([k,v])=>[v,k]));
const PIECE_CODES = ['wP','wN','wB','wR','wQ','wK','bP','bN','bB','bR','bQ','bK'];

const STARTING_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR';

let grid = emptyGrid();
let flipped = false;
let activeBrush = null;       // null = move mode, '' = eraser, 'wP' etc = place
let pickedSquare = null;
let legalDests = [];
let confidence = null;
let lastMoves = [];
let activeMoveIdx = 0;
let analyzeInFlight = false;

// Move history. Each entry: { fen_before, fen_after, san, uci, flipped }.
// Undo replays the FEN-before; redo replays FEN-after. Editing the position
// manually clears the future stack (just like a text editor).
let history = [];
let historyIdx = -1;  // index of last applied history entry (-1 = nothing yet)

function emptyGrid() {
  return Array.from({length: 8}, () => Array(8).fill(''));
}

function rotate180(g) {
  if (!g) return g;
  return g.slice().reverse().map(row => row.slice().reverse());
}

// ---------------------------------------------------------------------------
// FEN <-> grid
// ---------------------------------------------------------------------------

function gridToPlacement(g) {
  return g.map(row => {
    let s = '', empty = 0;
    for (const c of row) {
      if (!c) { empty++; continue; }
      if (empty) { s += empty; empty = 0; }
      s += CODE_TO_FEN[c];
    }
    if (empty) s += empty;
    return s;
  }).join('/');
}

function placementToGrid(placement) {
  const g = emptyGrid();
  const rows = (placement || '').split('/');
  if (rows.length !== 8) return g;
  for (let r = 0; r < 8; r++) {
    let c = 0;
    for (const ch of rows[r]) {
      if (/\d/.test(ch)) c += parseInt(ch, 10);
      else { g[r][c] = FEN_TO_CODE[ch] || ''; c++; }
    }
  }
  return g;
}

function fullFen() {
  const placement = gridToPlacement(grid);
  const turn = document.getElementById('turn').value;
  let castle = '';
  if (document.getElementById('cr-K').checked) castle += 'K';
  if (document.getElementById('cr-Q').checked) castle += 'Q';
  if (document.getElementById('cr-k').checked) castle += 'k';
  if (document.getElementById('cr-q').checked) castle += 'q';
  if (!castle) castle = '-';
  // Backend's _normalise_fen will strip rights that the position can't support.
  return `${placement} ${turn} ${castle} - 0 1`;
}

function loadFen(fen) {
  const parts = (fen || '').trim().split(/\s+/);
  grid = placementToGrid(parts[0] || '');
  if (parts[1] === 'w' || parts[1] === 'b') {
    document.getElementById('turn').value = parts[1];
  }
  const c = parts[2] || '-';
  document.getElementById('cr-K').checked = c.includes('K');
  document.getElementById('cr-Q').checked = c.includes('Q');
  document.getElementById('cr-k').checked = c.includes('k');
  document.getElementById('cr-q').checked = c.includes('q');
  confidence = null;
  pickedSquare = null;
  legalDests = [];
  render();
}

// ---------------------------------------------------------------------------
// Square names (a1..h8)
// ---------------------------------------------------------------------------

function squareName(r, c) {
  return String.fromCharCode(97 + c) + (8 - r);
}

function uciToFileRank(sq) {
  return { file: sq.charCodeAt(0) - 97, rank: parseInt(sq[1], 10) - 1 };
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function render() {
  renderBoard();
  renderFenInput();
  drawAllArrowsFor(activeMoveIdx);
}

function renderBoard() {
  const board = document.getElementById('board');
  board.innerHTML = '';
  for (let dr = 0; dr < 8; dr++) {
    for (let dc = 0; dc < 8; dc++) {
      const r = flipped ? 7 - dr : dr;
      const c = flipped ? 7 - dc : dc;
      const sq = document.createElement('div');
      const isLight = ((r + c) % 2) === 0;
      sq.className = 'sq ' + (isLight ? 'light' : 'dark');
      sq.dataset.r = r;
      sq.dataset.c = c;
      const piece = grid[r][c];
      if (piece) {
        const img = document.createElement('img');
        img.src = `/static/pieces/${piece}.png`;
        img.alt = piece;
        img.draggable = false;
        sq.appendChild(img);
      }
      if (confidence && piece && confidence[r][c] < 0.55) {
        sq.classList.add('low-conf');
      }
      if (pickedSquare && pickedSquare.r === r && pickedSquare.c === c) {
        sq.classList.add('picked');
      }
      if (pickedSquare && legalDests.length) {
        const sqName = squareName(r, c);
        if (legalDests.includes(sqName)) {
          sq.classList.add(piece ? 'legal-cap' : 'legal');
        }
      }
      sq.addEventListener('click', onSquareClick);
      sq.addEventListener('contextmenu', onSquareRightClick);
      sq.draggable = !!piece;
      sq.addEventListener('dragstart', onDragStart);
      sq.addEventListener('dragover', onDragOver);
      sq.addEventListener('dragend', onDragEnd);
      sq.addEventListener('drop', onDrop);
      board.appendChild(sq);
    }
  }
}

function renderFenInput() {
  document.getElementById('fen-input').value = fullFen();
}

// ---------------------------------------------------------------------------
// Square interactions
// ---------------------------------------------------------------------------

function onSquareClick(e) {
  const r = +e.currentTarget.dataset.r;
  const c = +e.currentTarget.dataset.c;

  if (activeBrush !== null) {
    grid[r][c] = activeBrush;
    if (confidence) confidence[r][c] = 1.0;
    pickedSquare = null;
    legalDests = [];
    clearHistory();  // manual edits invalidate move history
    render();
    return;
  }

  if (pickedSquare) {
    if (pickedSquare.r === r && pickedSquare.c === c) {
      pickedSquare = null;
      legalDests = [];
      render();
    } else {
      const fromSq = squareName(pickedSquare.r, pickedSquare.c);
      const toSq = squareName(r, c);
      pickedSquare = null;
      legalDests = [];
      sendMove(fromSq, toSq);
    }
    return;
  }

  if (grid[r][c]) {
    pickedSquare = { r, c };
    render();
    fetchLegalMoves(squareName(r, c));
  }
}

function onSquareRightClick(e) {
  e.preventDefault();
  const r = +e.currentTarget.dataset.r;
  const c = +e.currentTarget.dataset.c;
  if (grid[r][c]) clearHistory();
  grid[r][c] = '';
  pickedSquare = null;
  legalDests = [];
  render();
}

function onDragStart(e) {
  const r = +e.currentTarget.dataset.r;
  const c = +e.currentTarget.dataset.c;
  if (!grid[r][c]) { e.preventDefault(); return; }
  e.dataTransfer.setData('text/plain', `${r},${c}`);
  e.dataTransfer.effectAllowed = 'move';
  // Set the drag image to just the piece glyph for a cleaner ghost.
  const img = e.currentTarget.querySelector('img');
  if (img && e.dataTransfer.setDragImage) {
    const rect = e.currentTarget.getBoundingClientRect();
    e.dataTransfer.setDragImage(img, rect.width / 2, rect.height / 2);
  }
  pickedSquare = { r, c };
  // After the browser captures the drag-image, hide the source piece and
  // load legal destinations.
  setTimeout(() => {
    const sourceEl = document.querySelector(`.sq[data-r="${r}"][data-c="${c}"]`);
    if (sourceEl) sourceEl.classList.add('dragging');
    fetchLegalMoves(squareName(r, c));
  }, 0);
}

function onDragEnd(_e) {
  document.querySelectorAll('.sq.dragging').forEach(s => s.classList.remove('dragging'));
}

function onDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
}

function onDrop(e) {
  e.preventDefault();
  const data = e.dataTransfer.getData('text/plain');
  if (!data) return;
  const [sr, sc] = data.split(',').map(n => +n);
  const r = +e.currentTarget.dataset.r;
  const c = +e.currentTarget.dataset.c;
  pickedSquare = null;
  legalDests = [];
  if (sr === r && sc === c) { render(); return; }
  sendMove(squareName(sr, sc), squareName(r, c));
}

// ---------------------------------------------------------------------------
// Palette
// ---------------------------------------------------------------------------

function buildPalette() {
  const p = document.getElementById('palette');
  PIECE_CODES.forEach(code => {
    const btn = document.createElement('button');
    btn.dataset.code = code;
    btn.title = code;
    const img = document.createElement('img');
    img.src = `/static/pieces/${code}.png`;
    btn.appendChild(img);
    btn.addEventListener('click', () => selectBrush(code));
    p.appendChild(btn);
  });
  const eraser = document.createElement('button');
  eraser.dataset.code = '';
  eraser.className = 'empty';
  eraser.textContent = 'erase';
  eraser.addEventListener('click', () => selectBrush(''));
  p.appendChild(eraser);
}

function selectBrush(code) {
  if (activeBrush === code) {
    activeBrush = null;
    document.querySelectorAll('#palette button.selected').forEach(b => b.classList.remove('selected'));
    return;
  }
  activeBrush = code;
  document.querySelectorAll('#palette button').forEach(b => {
    const matches = (code === '' && b.classList.contains('empty')) ||
                    (code !== '' && b.dataset.code === code);
    b.classList.toggle('selected', matches);
  });
}

// ---------------------------------------------------------------------------
// Arrows
// ---------------------------------------------------------------------------

function squareCenterPx(file, rank) {
  const dc = flipped ? 7 - file : file;
  const dr = flipped ? rank : 7 - rank;
  const cell = 800 / 8;
  return { x: dc * cell + cell / 2, y: dr * cell + cell / 2 };
}

function clearArrows() {
  document.getElementById('arrows').innerHTML = '';
  document.querySelectorAll('.sq.from, .sq.to, .sq.alt-from, .sq.alt-to')
    .forEach(s => s.classList.remove('from', 'to', 'alt-from', 'alt-to'));
}

function drawArrow(fromUci, toUci, color, width = 14) {
  const f = uciToFileRank(fromUci);
  const t = uciToFileRank(toUci);
  const a = squareCenterPx(f.file, f.rank);
  const b = squareCenterPx(t.file, t.rank);
  const svg = document.getElementById('arrows');
  const ns = 'http://www.w3.org/2000/svg';

  const dx = b.x - a.x, dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  const tipBackX = b.x - ux * 25;
  const tipBackY = b.y - uy * 25;

  const line = document.createElementNS(ns, 'line');
  line.setAttribute('x1', a.x); line.setAttribute('y1', a.y);
  line.setAttribute('x2', tipBackX); line.setAttribute('y2', tipBackY);
  line.setAttribute('stroke', color);
  line.setAttribute('stroke-width', width);
  line.setAttribute('stroke-linecap', 'round');
  line.setAttribute('opacity', '0.85');
  svg.appendChild(line);

  const px = -uy, py = ux;
  const headSize = width * 1.4;
  const head = document.createElementNS(ns, 'polygon');
  head.setAttribute('points',
    `${b.x},${b.y} ${tipBackX + px * headSize},${tipBackY + py * headSize} ${tipBackX - px * headSize},${tipBackY - py * headSize}`);
  head.setAttribute('fill', color);
  head.setAttribute('opacity', '0.85');
  svg.appendChild(head);
}

function highlightMove(uci) {
  const from = uci.slice(0, 2), to = uci.slice(2, 4);
  const ff = uciToFileRank(from), tt = uciToFileRank(to);
  const fromR = 7 - ff.rank, fromC = ff.file;
  const toR   = 7 - tt.rank, toC   = tt.file;
  document.querySelector(`.sq[data-r="${fromR}"][data-c="${fromC}"]`)?.classList.add('from');
  document.querySelector(`.sq[data-r="${toR}"][data-c="${toC}"]`)?.classList.add('to');
}

function drawAllArrowsFor(primaryIdx) {
  clearArrows();
  if (!lastMoves.length) return;
  lastMoves.forEach((m, i) => {
    if (i === primaryIdx) return;
    drawArrow(m.from_square, m.to_square, '#4ea3ff', 9);
  });
  const m = lastMoves[primaryIdx];
  if (!m) return;
  drawArrow(m.from_square, m.to_square, '#ffaa1c', 16);
  highlightMove(m.uci);
}

// ---------------------------------------------------------------------------
// Engine display
// ---------------------------------------------------------------------------

function renderMoves(moves) {
  lastMoves = moves || [];
  activeMoveIdx = 0;
  const ol = document.getElementById('moves');
  ol.innerHTML = '';
  lastMoves.forEach((m, i) => {
    const li = document.createElement('li');
    li.dataset.idx = i;
    const wdl = m.wdl ? wdlBadge(m.wdl) : '';
    li.innerHTML = `
      <span class="rank">${m.rank}.</span>
      <span class="san">${escapeHtml(m.san)}</span>
      <span class="eval">${escapeHtml(m.eval_text)}</span>
      <span class="pv">${escapeHtml(m.pv_san.slice(0, 8).join(' '))}${wdl}</span>
    `;
    li.addEventListener('click', () => selectMove(i));
    ol.appendChild(li);
  });
  if (lastMoves.length) {
    selectMove(0);
    updateEvalBar(lastMoves[0]);
  } else {
    updateEvalBar(null);
  }
}

function wdlBadge([w, d, l]) {
  const total = w + d + l || 1;
  const pct = (n) => Math.round(100 * n / total);
  return `<span class="wdl"> · W ${pct(w)}% D ${pct(d)}% L ${pct(l)}%</span>`;
}

function selectMove(idx) {
  activeMoveIdx = idx;
  document.querySelectorAll('#moves li').forEach((li, i) =>
    li.classList.toggle('active', i === idx));
  drawAllArrowsFor(idx);
}

function updateEvalBar(top) {
  const fill = document.getElementById('eval-fill');
  const text = document.getElementById('eval-text');
  if (!top) { fill.style.width = '50%'; text.textContent = '–'; return; }
  text.textContent = top.eval_text;
  let pct = 50;
  if (top.score_mate != null) {
    pct = top.score_mate > 0 ? 95 : 5;
  } else if (top.score_cp != null) {
    const cp = Math.max(-1000, Math.min(1000, top.score_cp));
    pct = 50 + (cp / 1000) * 45;
  }
  if (document.getElementById('turn').value === 'b') pct = 100 - pct;
  fill.style.width = `${pct}%`;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[c]));
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

function settingsBody() {
  const elo = document.getElementById('elo').value;
  return {
    multipv: +document.getElementById('multipv').value,
    depth: +document.getElementById('depth').value,
    elo: elo === 'max' ? null : +elo,
  };
}

async function uploadImage(file) {
  setStatus('Detecting position from image…');
  setBusy(true, 'Detecting…');
  clearHistory();  // a fresh image is a new game
  const fd = new FormData();
  fd.append('image', file);
  let j;
  try {
    const r = await fetch('/api/detect', { method: 'POST', body: fd });
    j = await r.json();
  } catch (e) {
    setStatus('Network error: ' + e.message, true);
    setBusy(false);
    return;
  }
  if (!j.ok) {
    setStatus('Detection failed: ' + (j.error || 'unknown'), true);
    setBusy(false);
    return;
  }
  let detected = j.grid.map(row => row.slice());
  let conf = j.confidence;
  if (j.flipped_guess) {
    detected = rotate180(detected);
    conf = rotate180(conf);
    flipped = true;
  } else {
    flipped = false;
  }
  grid = detected;
  confidence = conf;
  document.getElementById('turn').value = 'w';
  // Castling rights default to all four; backend will strip whichever the
  // detected piece layout doesn't support.
  ['cr-K','cr-Q','cr-k','cr-q'].forEach(id => document.getElementById(id).checked = true);
  setStatus(flipped
    ? 'Detected (black perspective). White to move. Analysing…'
    : 'Detected. White to move. Analysing…');
  render();
  analyze();
}

async function analyze() {
  if (analyzeInFlight) return;
  analyzeInFlight = true;
  setBusy(true);
  try {
    const fen = fullFen();
    const r = await fetch('/api/analyze', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ fen, ...settingsBody() }),
    });
    const j = await r.json();
    if (!j.ok) {
      setStatus('Engine: ' + (j.error || 'failed'), true);
      return;
    }
    if (j.fen) loadFenSilently(j.fen);
    if (!j.moves.length) {
      setStatus('Game over — no moves to analyse.');
      renderMoves([]);
      return;
    }
    const top = j.moves[0];
    const eloLabel = j.elo === 'max' ? 'max ELO' : `ELO ${j.elo}`;
    let s = `Best: ${top.san} (${top.eval_text}) at depth ${top.depth} · ${eloLabel}`;
    if (j.notes && j.notes.length) s += ' · ' + j.notes.join(', ');
    setStatus(s);
    renderMoves(j.moves);
  } catch (e) {
    setStatus('Network error: ' + e.message, true);
  } finally {
    analyzeInFlight = false;
    setBusy(false);
  }
}

function loadFenSilently(fen) {
  // Update castling/turn from server-cleaned FEN without resetting the
  // board (grid is already correct for the move we just made).
  const parts = fen.trim().split(/\s+/);
  if (parts[1]) document.getElementById('turn').value = parts[1];
  const c = parts[2] || '-';
  document.getElementById('cr-K').checked = c.includes('K');
  document.getElementById('cr-Q').checked = c.includes('Q');
  document.getElementById('cr-k').checked = c.includes('k');
  document.getElementById('cr-q').checked = c.includes('q');
  if (parts[0]) grid = placementToGrid(parts[0]);
  renderFenInput();
}

async function fetchLegalMoves(fromSq) {
  try {
    const r = await fetch('/api/legal-moves', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ fen: fullFen(), from: fromSq }),
    });
    const j = await r.json();
    if (j.ok) { legalDests = j.destinations; render(); }
  } catch {}
}

let moveInFlight = false;

async function sendMove(fromSq, toSq) {
  if (moveInFlight) return;  // ignore racing clicks
  // Only request promotion when the piece is actually a pawn moving to the
  // last rank — sending it otherwise produces UCI like "e7e5q" which is
  // never a legal move.
  const fr = 8 - parseInt(fromSq[1], 10);
  const fc = fromSq.charCodeAt(0) - 97;
  const tr = 8 - parseInt(toSq[1], 10);
  const piece = grid[fr][fc];
  const isPromotion = piece && piece[1] === 'P' && (tr === 0 || tr === 7);
  const fenBefore = fullFen();
  const body = { fen: fenBefore, from: fromSq, to: toSq, ...settingsBody() };
  if (isPromotion) body.promotion = 'q';

  moveInFlight = true;
  setBusy(true, 'Thinking…');
  setStatus(`Playing ${fromSq}-${toSq}…`);
  try {
    const r = await fetch('/api/move', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!j.ok) {
      console.warn('move rejected', j);
      const legal = (j.legal_from_source || []).join(', ') || 'none';
      setStatus(`Illegal: ${j.error || 'rejected'} (legal from ${fromSq}: ${legal})`, true);
      render();
      return;
    }
    loadFenSilently(j.fen);
    pushHistory({
      fen_before: fenBefore,
      fen_after: j.fen,
      san: j.san,
      uci: fromSq + toSq + (isPromotion ? 'q' : ''),
    });
    let msg = `You played ${j.san}`;
    if (j.is_checkmate) msg += ' — checkmate!';
    else if (j.is_stalemate) msg += ' — stalemate.';
    else if (j.is_check) msg += ' — check.';
    if (j.moves && j.moves.length) {
      msg += ` · Reply: ${j.moves[0].san} (${j.moves[0].eval_text})`;
      renderMoves(j.moves);
    } else {
      renderMoves([]);
    }
    setStatus(msg);
    render();
  } catch (e) {
    setStatus('Network error: ' + e.message, true);
    render();
  } finally {
    moveInFlight = false;
    setBusy(false);
  }
}

function setStatus(msg, isErr) {
  const el = document.getElementById('status');
  el.textContent = msg;
  el.classList.toggle('error', !!isErr);
}

function setBusy(on, label) {
  const btn = document.getElementById('analyze-btn');
  btn.disabled = !!on;
  btn.textContent = on ? (label || 'Analysing…') : 'Analyse';
  document.body.classList.toggle('busy', !!on);
}

// ---------------------------------------------------------------------------
// Move history
// ---------------------------------------------------------------------------

function pushHistory(entry) {
  // Drop any "future" entries past historyIdx — a new branch starts here.
  history = history.slice(0, historyIdx + 1);
  history.push(entry);
  historyIdx = history.length - 1;
  renderHistory();
  updateUndoRedoButtons();
}

function clearHistory() {
  history = [];
  historyIdx = -1;
  renderHistory();
  updateUndoRedoButtons();
}

function renderHistory() {
  const ol = document.getElementById('history');
  if (!ol) return;
  ol.innerHTML = '';
  history.forEach((h, i) => {
    const li = document.createElement('li');
    const moveNum = Math.floor(i / 2) + 1;
    const isWhite = (i % 2) === 0;
    li.innerHTML = `<span class="num">${isWhite ? moveNum + '.' : ''}</span><span class="san">${escapeHtml(h.san)}</span>`;
    li.classList.toggle('current', i === historyIdx);
    li.addEventListener('click', () => jumpHistory(i));
    ol.appendChild(li);
  });
  ol.scrollTop = ol.scrollHeight;
}

function updateUndoRedoButtons() {
  const u = document.getElementById('undo-btn');
  const r = document.getElementById('redo-btn');
  if (u) u.disabled = historyIdx < 0;
  if (r) r.disabled = historyIdx >= history.length - 1;
}

function jumpHistory(idx) {
  if (idx < -1 || idx >= history.length) return;
  historyIdx = idx;
  const fen = idx < 0 ? (history[0]?.fen_before || STARTING_FEN + ' w KQkq - 0 1')
                      : history[idx].fen_after;
  loadFen(fen);
  renderHistory();
  updateUndoRedoButtons();
  analyze();
}

function undo() {
  if (historyIdx < 0) return;
  historyIdx -= 1;
  const fen = historyIdx < 0 ? history[0].fen_before : history[historyIdx].fen_after;
  loadFen(fen);
  renderHistory();
  updateUndoRedoButtons();
  setStatus('Undone. Re-analysing…');
  analyze();
}

function redo() {
  if (historyIdx >= history.length - 1) return;
  historyIdx += 1;
  loadFen(history[historyIdx].fen_after);
  renderHistory();
  updateUndoRedoButtons();
  setStatus('Redone. Re-analysing…');
  analyze();
}

function exportPGN() {
  if (!history.length) return '';
  const startFen = history[0].fen_before;
  const isStandardStart = startFen.startsWith('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq');
  const lines = [
    '[Event "Chess AI Assistant analysis"]',
    `[Date "${new Date().toISOString().slice(0, 10)}"]`,
    '[White "?"]',
    '[Black "?"]',
    '[Result "*"]',
  ];
  if (!isStandardStart) {
    lines.push('[SetUp "1"]');
    lines.push(`[FEN "${startFen}"]`);
  }
  lines.push('');
  // Build move list "1. e4 e5 2. Nf3 ..."
  let body = '';
  history.forEach((h, i) => {
    const moveNum = Math.floor(i / 2) + 1;
    const isWhite = (i % 2) === 0;
    if (isWhite) body += `${moveNum}. `;
    body += `${h.san} `;
  });
  body += '*';
  lines.push(body.trim());
  return lines.join('\n');
}

async function copyPGN() {
  const pgn = exportPGN();
  if (!pgn) {
    setStatus('No moves played yet to export.', true);
    return;
  }
  try {
    await navigator.clipboard.writeText(pgn);
    setStatus('PGN copied to clipboard (' + history.length + ' plies).');
  } catch {
    // Fallback: drop into the FEN field as a one-shot.
    const ta = document.createElement('textarea');
    ta.value = pgn; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove();
    setStatus('PGN copied (fallback path).');
  }
}

// ---------------------------------------------------------------------------
// Live screen-share capture
// ---------------------------------------------------------------------------
//
// Workflow:
//   1. User clicks "Live capture" → browser asks them to pick a screen / window / tab.
//   2. The chosen MediaStream gets piped to a hidden <video>.
//   3. A polling loop draws the video frame to a canvas, hashes it, and only
//      forwards it to /api/detect when:
//        - the hash differs from the previous frame (something changed), AND
//        - two consecutive frames have the same hash (the new frame is *settled*,
//          i.e. animations / cursor flicker have stopped).
//   4. If detection produces a position different from the current grid, we
//      update the board, push a synthetic history entry, and trigger /api/analyze.
//   5. Stops cleanly when the user clicks Stop OR when the browser ends the
//      sharing session.

let live = {
  stream: null,
  video: null,
  canvas: null,
  timer: null,
  prevHash: null,
  stableCount: 0,
  lastDetectedFen: null,
  busy: false,
  // ROI in normalised video coordinates [0..1] of the actual stream resolution.
  // null until the user has drawn a rectangle on the preview.
  roi: null,
  drag: null,  // { x0, y0, x1, y1 } in preview-canvas pixels during a drag
};

function liveStatus(text, klass) {
  const el = document.getElementById('live-status');
  if (!el) return;
  el.textContent = text;
  el.classList.remove('active', 'error');
  if (klass) el.classList.add(klass);
}

async function startLiveCapture() {
  if (live.stream) return;
  if (!navigator.mediaDevices?.getDisplayMedia) {
    setStatus('Live capture not supported in this browser.', true);
    return;
  }
  try {
    live.stream = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: 10, cursor: 'never' },
      audio: false,
    });
  } catch (e) {
    setStatus('Screen-share permission denied or cancelled.', true);
    return;
  }

  live.stream.getVideoTracks()[0].addEventListener('ended', stopLiveCapture);

  live.video = document.getElementById('live-video');
  live.video.srcObject = live.stream;
  await live.video.play().catch(() => {});

  document.getElementById('live-panel').classList.remove('hidden');
  document.getElementById('live-btn').disabled = true;
  liveStatus('Drag a rectangle around the chess board on the preview →', 'active');
  clearHistory();

  live.canvas = document.createElement('canvas');
  live.prevHash = null;
  live.stableCount = 0;
  live.lastDetectedFen = null;
  live.roi = null;
  attachRoiHandlers();
  drawRoiOverlay();
  // Start ticking only after ROI is set so we don't waste cycles detecting
  // the entire desktop.
}

function stopLiveCapture() {
  if (live.timer) { clearTimeout(live.timer); live.timer = null; }
  if (live.stream) {
    live.stream.getTracks().forEach(t => t.stop());
    live.stream = null;
  }
  if (live.video) live.video.srcObject = null;
  document.getElementById('live-panel').classList.add('hidden');
  document.getElementById('live-btn').disabled = false;
  liveStatus('Idle');
  live.roi = null;
}

// ---- ROI selection on the preview ----------------------------------------

function attachRoiHandlers() {
  const c = document.getElementById('live-roi');
  if (!c) return;
  c.onpointerdown = onRoiDown;
  c.onpointermove = onRoiMove;
  c.onpointerup = onRoiUp;
  c.onpointercancel = onRoiUp;
  c.onpointerleave = e => { if (live.drag) onRoiUp(e); };
}

function roiCanvasContext() {
  const canvas = document.getElementById('live-roi');
  const wrap = canvas.parentElement;
  // Match the canvas pixel size to its CSS size for crisp drawing.
  if (canvas.width !== wrap.clientWidth || canvas.height !== wrap.clientHeight) {
    canvas.width = wrap.clientWidth;
    canvas.height = wrap.clientHeight;
  }
  return canvas.getContext('2d');
}

function videoDisplayRect() {
  // The video uses object-fit:contain inside the wrap, so it's letterboxed.
  // Compute the actual display rectangle so we can map pointer coords to
  // normalised video coords.
  const v = live.video;
  const wrap = document.getElementById('live-roi').parentElement;
  const wrapW = wrap.clientWidth, wrapH = wrap.clientHeight;
  const vw = v.videoWidth || wrapW;
  const vh = v.videoHeight || wrapH;
  const scale = Math.min(wrapW / vw, wrapH / vh);
  const dw = vw * scale, dh = vh * scale;
  const dx = (wrapW - dw) / 2;
  const dy = (wrapH - dh) / 2;
  return { dx, dy, dw, dh, vw, vh };
}

function pointerToVideoNorm(ev) {
  const rect = ev.currentTarget.getBoundingClientRect();
  const px = ev.clientX - rect.left;
  const py = ev.clientY - rect.top;
  const { dx, dy, dw, dh } = videoDisplayRect();
  const nx = Math.max(0, Math.min(1, (px - dx) / dw));
  const ny = Math.max(0, Math.min(1, (py - dy) / dh));
  return { nx, ny, px, py };
}

function onRoiDown(ev) {
  ev.preventDefault();
  ev.currentTarget.setPointerCapture(ev.pointerId);
  const p = pointerToVideoNorm(ev);
  live.drag = { x0: p.nx, y0: p.ny, x1: p.nx, y1: p.ny };
  drawRoiOverlay();
}
function onRoiMove(ev) {
  if (!live.drag) return;
  const p = pointerToVideoNorm(ev);
  live.drag.x1 = p.nx;
  live.drag.y1 = p.ny;
  drawRoiOverlay();
}
function onRoiUp(ev) {
  if (!live.drag) return;
  const { x0, y0, x1, y1 } = live.drag;
  live.drag = null;
  const minX = Math.min(x0, x1), maxX = Math.max(x0, x1);
  const minY = Math.min(y0, y1), maxY = Math.max(y0, y1);
  // Reject too-tiny boxes (probably a click).
  if ((maxX - minX) * (maxY - minY) < 0.005) {
    drawRoiOverlay();
    return;
  }
  live.roi = { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
  drawRoiOverlay();
  document.getElementById('live-roi-hint').classList.add('set');
  document.getElementById('live-roi-hint').textContent = 'Region locked. Capturing…';
  liveStatus('Region locked. Watching for moves…', 'active');
  // Reset state so the next stable frame triggers a detection.
  live.prevHash = null;
  live.stableCount = 0;
  if (!live.timer) scheduleLiveTick();
}

function clearRoi() {
  live.roi = null;
  document.getElementById('live-roi-hint').classList.remove('set');
  document.getElementById('live-roi-hint').textContent = 'Drag a rectangle around the chess board.';
  drawRoiOverlay();
  if (live.timer) { clearTimeout(live.timer); live.timer = null; }
  liveStatus('Region cleared. Drag a new rectangle.', 'active');
}

function drawRoiOverlay() {
  const ctx = roiCanvasContext();
  if (!ctx) return;
  const c = ctx.canvas;
  ctx.clearRect(0, 0, c.width, c.height);
  const { dx, dy, dw, dh } = videoDisplayRect();
  let box = null;
  if (live.drag) {
    const { x0, y0, x1, y1 } = live.drag;
    box = {
      x: Math.min(x0, x1), y: Math.min(y0, y1),
      w: Math.abs(x1 - x0), h: Math.abs(y1 - y0),
    };
  } else if (live.roi) {
    box = live.roi;
  }
  if (!box) return;
  const px = dx + box.x * dw;
  const py = dy + box.y * dh;
  const pw = box.w * dw;
  const ph = box.h * dh;
  // Dim everything outside the box.
  ctx.fillStyle = 'rgba(0,0,0,0.4)';
  ctx.fillRect(0, 0, c.width, py);                 // top
  ctx.fillRect(0, py + ph, c.width, c.height);     // bottom
  ctx.fillRect(0, py, px, ph);                     // left
  ctx.fillRect(px + pw, py, c.width, ph);          // right
  // Box outline
  ctx.strokeStyle = live.drag ? '#ffd866' : '#6ce06c';
  ctx.lineWidth = 2;
  ctx.strokeRect(px, py, pw, ph);
}

function scheduleLiveTick() {
  if (!live.stream) return;
  const ms = +document.getElementById('live-interval').value || 1500;
  live.timer = setTimeout(liveTick, ms);
}

async function liveTick() {
  if (!live.stream) return;
  if (live.busy) { scheduleLiveTick(); return; }
  if (!live.roi) { scheduleLiveTick(); return; }

  const v = live.video;
  if (!v.videoWidth) { scheduleLiveTick(); return; }

  // Crop to ROI in source-video coordinates, then down-sample for hashing.
  const sx = live.roi.x * v.videoWidth;
  const sy = live.roi.y * v.videoHeight;
  const sw = live.roi.w * v.videoWidth;
  const sh = live.roi.h * v.videoHeight;
  const scale = 160 / Math.max(sw, sh);  // smaller hash thumb = faster
  const w = Math.max(64, Math.round(sw * scale));
  const h = Math.max(64, Math.round(sh * scale));
  live.canvas.width = w;
  live.canvas.height = h;
  const ctx = live.canvas.getContext('2d');
  ctx.drawImage(v, sx, sy, sw, sh, 0, 0, w, h);

  // Coarser hash (sample every 64 bytes) — still discriminating enough to
  // notice a piece move, but ~3× cheaper than the previous step.
  const data = ctx.getImageData(0, 0, w, h).data;
  let h1 = 0, h2 = 0;
  for (let i = 0; i < data.length; i += 64) {
    const g = (data[i] + data[i + 1] + data[i + 2]) | 0;
    h1 = (h1 + g) | 0;
    h2 = ((h2 << 1) ^ g) | 0;
  }
  const hash = `${h1},${h2}`;

  // Ultra-fast path: trigger detection on the FIRST tick after a change.
  // This gives ~poll-interval latency from the move on screen to detection
  // (default 0.2 s). The detector itself is idempotent so a stray animation
  // frame just produces the same FEN and gets skipped by the FEN-equality
  // check inside runLiveDetection.
  if (live.prevHash !== null && hash !== live.prevHash) {
    live.prevHash = hash;
    await runLiveDetection();
    scheduleLiveTick();
    return;
  }
  live.prevHash = hash;
  scheduleLiveTick();
}

async function runLiveDetection() {
  if (live.busy) return;
  live.busy = true;
  liveStatus('Detecting position…', 'active');
  try {
    // Capture the ROI at full source resolution for detection accuracy.
    const v = live.video;
    const sx = live.roi.x * v.videoWidth;
    const sy = live.roi.y * v.videoHeight;
    const sw = live.roi.w * v.videoWidth;
    const sh = live.roi.h * v.videoHeight;
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(sw);
    canvas.height = Math.round(sh);
    canvas.getContext('2d').drawImage(v, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(res => canvas.toBlob(res, 'image/png'));
    if (!blob) { liveStatus('Frame capture failed', 'error'); return; }

    const fd = new FormData();
    fd.append('image', blob, 'live.png');
    const r = await fetch('/api/detect', { method: 'POST', body: fd });
    const j = await r.json();
    if (!j.ok) {
      liveStatus('Detect: ' + (j.error || 'failed'), 'error');
      return;
    }

    let detected = j.grid.map(row => row.slice());
    if (j.flipped_guess) detected = rotate180(detected);
    const newFen = gridToPlacement(detected);
    const currentFen = gridToPlacement(grid);

    if (newFen === currentFen) {
      liveStatus(`Same position. Watching…`, 'active');
      return;
    }

    // Position changed. Try to figure out whose turn it is by counting which
    // side moved (a single-piece delta from current grid implies the other
    // side just played). Fall back to flipping the current side-to-move.
    const inferred = inferTurnFromDiff(grid, detected);
    grid = detected;
    flipped = !!j.flipped_guess;
    confidence = j.flipped_guess ? rotate180(j.confidence) : j.confidence;
    if (inferred) document.getElementById('turn').value = inferred;
    pushHistory({
      fen_before: currentFen + ' (live)',
      fen_after: fullFen(),
      san: '(detected)',
      uci: '----',
    });
    render();
    live.lastDetectedFen = newFen;
    liveStatus(`Position changed. Analysing as ${inferred === 'w' ? 'white' : 'black'} to move…`, 'active');
    await analyze();
    liveStatus('Watching for next move…', 'active');
  } catch (e) {
    liveStatus('Error: ' + e.message, 'error');
  } finally {
    live.busy = false;
  }
}

function inferTurnFromDiff(oldG, newG) {
  // Count which colour added/removed pieces. The side that *moved* is the one
  // whose pieces shifted (a piece left a square AND appeared on another); the
  // other side gets the move next.
  let whiteMoved = false, blackMoved = false;
  for (let r = 0; r < 8; r++) {
    for (let c = 0; c < 8; c++) {
      if (oldG[r][c] !== newG[r][c]) {
        const old = oldG[r][c];
        const nu = newG[r][c];
        if (old && old[0] === 'w') whiteMoved = true;
        if (old && old[0] === 'b') blackMoved = true;
        if (nu && nu[0] === 'w') whiteMoved = true;
        if (nu && nu[0] === 'b') blackMoved = true;
      }
    }
  }
  if (whiteMoved && !blackMoved) return 'b';
  if (blackMoved && !whiteMoved) return 'w';
  return null;
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

function init() {
  buildPalette();
  loadFen(STARTING_FEN + ' w KQkq - 0 1');
  updateUndoRedoButtons();

  document.getElementById('img-file').addEventListener('change', e => {
    const f = e.target.files[0];
    if (f) uploadImage(f);
    e.target.value = '';
  });
  document.getElementById('analyze-btn').addEventListener('click', analyze);
  document.getElementById('reset-btn').addEventListener('click', () => {
    grid = emptyGrid(); confidence = null; lastMoves = []; flipped = false;
    document.getElementById('turn').value = 'w';
    ['cr-K','cr-Q','cr-k','cr-q'].forEach(id => document.getElementById(id).checked = false);
    clearHistory();
    render(); renderMoves([]);
    setStatus('Empty board. Place pieces using the palette.');
  });
  document.getElementById('start-btn').addEventListener('click', () => {
    flipped = false;
    clearHistory();
    loadFen(STARTING_FEN + ' w KQkq - 0 1');
    setStatus('Starting position. White to move. Click Analyse.');
  });
  document.getElementById('flip-btn').addEventListener('click', () => {
    flipped = !flipped; render();
  });
  document.getElementById('undo-btn')?.addEventListener('click', undo);
  document.getElementById('redo-btn')?.addEventListener('click', redo);
  document.getElementById('pgn-btn')?.addEventListener('click', copyPGN);
  document.getElementById('live-btn')?.addEventListener('click', startLiveCapture);
  document.getElementById('live-stop-btn')?.addEventListener('click', stopLiveCapture);
  document.getElementById('live-roi-clear-btn')?.addEventListener('click', clearRoi);
  // Re-draw the ROI overlay when the window resizes (preview wrap can flex).
  window.addEventListener('resize', () => { if (live.stream) drawRoiOverlay(); });
  document.getElementById('fen-input').addEventListener('change', e => {
    clearHistory();
    loadFen(e.target.value);
  });
  ['turn','cr-K','cr-Q','cr-k','cr-q','elo','multipv','depth'].forEach(id =>
    document.getElementById(id).addEventListener('change', renderFenInput));

  // Keyboard shortcuts. Skip when the user is typing in an input/textarea.
  document.addEventListener('keydown', e => {
    const t = e.target;
    if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
    if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === 'z') {
      e.preventDefault(); undo(); return;
    }
    if ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === 'y' || (e.shiftKey && e.key.toLowerCase() === 'z'))) {
      e.preventDefault(); redo(); return;
    }
    if (e.key === 'Escape') {
      pickedSquare = null; legalDests = []; render(); return;
    }
    if (e.key === 'f' || e.key === 'F') {
      flipped = !flipped; render(); return;
    }
    if (e.key === 'a' || e.key === 'A') {
      analyze(); return;
    }
  });

  setStatus('Upload a board image, edit manually, or click Analyse. Shortcuts: F flip, A analyse, Ctrl+Z undo, Esc cancel.');
}

init();
