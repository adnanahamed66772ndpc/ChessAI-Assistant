# Chess AI Assistant — Help / সব kichu ki ki kore

Sob feature ekhane explain kora ache. Bengali + English mix.

---

## ⚠️  Disclaimer — please read

**Ei software shudhu study, analysis, ar nijer game review er jonno** —
real-time live game e use kora **cheating** ar prottek major chess platform
(chess.com, lichess.org, worldchess.com, chess24, FIDE Online Arena, OTB
tournaments) er **rules-violation**.

Wrong use korle: account ban, rating loss, title forfeit, tournament
disqualification, ba legal action — ei sob er **dayitto tomar nijer**. Author
or contributors **kono dayee nay** misuse er kono consequence er jonno.

**OK use:**
- Tomar nijer purano games review kora
- Tactics / opening / endgame practice
- Chess software banano ba training
- Puzzle / position research, teaching

**Not OK:**
- Live online ba OTB game-er somoy move suggestion neoa
- Bot account jeta automatic play kore
- Student ke rated game er somoy real-time coach kora

Use korar agei niye nijer responsibility bujhe nao.

---

## 1. Position section (board er position set kora)

### FEN
**FEN** = Forsyth-Edwards Notation — chess position er ekta text string.

Example:
```
8/b3k3/4N3/pKP2ppp/P2N1P2/6P1/6b1/8 w - - 0 1
```

6 ta part space diye separate kora:

| Part | Example | Mane |
|------|---------|------|
| 1. Pieces | `8/b3k3/...` | Board er 8 ranks (top→bottom). Number = empty squares; letter = piece (uppercase=white, lowercase=black). |
| 2. Turn | `w` | `w`=white to move, `b`=black to move |
| 3. Castling | `-` ba `KQkq` | Kon side castle korte pare. `-` mane keu na. |
| 4. En passant | `-` ba e.g. `e3` | En passant capture target square (ekta pawn double-step korar por) |
| 5. Halfmove clock | `0` | 50-move rule counter |
| 6. Fullmove | `1` | Move number |

Apni FEN box e text type kore Enter korle position load hobe.

### Side to move
**Kar pala** — White ba Black. Ata FEN er 2nd part. Apni manually toggle korte paren — engine sei perspective theke best move dibe.

### Castling rights (4 ta checkbox)
| Box | Mane |
|-----|------|
| White O-O | White short castle korte parbe (king-side) |
| White O-O-O | White long castle korte parbe (queen-side) |
| Black O-O | Black short castle korte parbe |
| Black O-O-O | Black long castle korte parbe |

Backend automatically check kore — jodi rook/king moved hoye thake, castling rights strip kore dey, even if checkbox checked.

---

## 2. Engine settings

### Engine strength (ELO)
Stockfish 17.1 er strength control:

| Option | ELO | Ki rokom player |
|--------|-----|------------------|
| Maximum | ~3500+ | Stockfish full power, super GM theke o beshi strong |
| 3190 | 3190 | Stockfish er max limited setting |
| 3000 | 3000 | Top-tier super GM (Carlsen, Caruana level) |
| 2800 | 2800 | Top GM |
| 2500 | 2500 | Average GM |
| 2200 | 2200 | Master / IM |
| 1900 | 1900 | Strong club player / expert |
| 1600 | 1600 | Average club player |
| 1320 | 1320 | Beginner |

**Maximum** chai sob che bhalo move er jonno. Apni nije khelte chaile niche niye ashte paren (eg. 1900 e khelle realistic match).

### Top moves (1, 3, 5, 7)
Engine koto ta candidate moves dekhabe. **1** mane shudhu best move; **5** mane top 5 best moves with eval. Beshi top moves = engine ektu slow hobe.

### Depth (14, 18, 22, 26)
Engine koto move ahead think korbe.
- **14**: fast, tactical jinish dekhe — ~1s
- **18**: balanced — 2-3s
- **22**: strong (default) — 5-10s
- **26**: deep, slow but very accurate — 15-30s

Beshi depth = beshi accurate, kintu beshi time. **22 default best for most cases**.

---

## 3. Buttons (top of page)

### Upload board image
File chooser open kore — apni chess board image (screenshot ba photo) upload kore paren. App automatic position detect kore, side-to-move = white set kore, then auto-analyze kore best move dey.

### Empty
Sob piece remove kore khali board kore. Manual position banaytey palette use korun.

### Start
Standard chess starting position load kore.

### Undo (↶) / Redo (↷)
- **Undo (Ctrl+Z)**: shes move ta cancel kore, board agheror state e fire jay, automatic re-analyze kore.
- **Redo (Ctrl+Y / Ctrl+Shift+Z)**: undone move abar apply kore.

Move history e jokhon click korben, oikhane jump kore jabe.

### Copy PGN
Ekhon porjonto khela move-gulo PGN (Portable Game Notation) format e clipboard e copy kore. Apni eta lichess/chess.com/PGN-viewer e paste korte paren.

### Flip board (niche)
Board 180° rotate kore — white-perspective theke black-perspective ba ulta. Internal position change hoy na, only visual.

### Analyse (big blue button)
Current position e engine call kore top moves calculate kore. Image upload korar por o move korar por automatic chole — kintu apni manually trigger korte paren (e.g., depth/ELO change korar por).

---

## 4. Engine analysis output

### Eval bar (top)
```
[========+5.70========]
```
Position er evaluation (white perspective):
- **+5.70** = white 5.70 pawns ahead (material + position)
- **-1.20** = black ahead
- **0.00** = equal
- **M5** = checkmate in 5 moves (positive = current side wins, negative = current side loses)

Bar fill: white side beshi value hole left side beshi fill, black side hole right side.

### Move list (numbered)
Each move show kore:
| Field | Mane |
|-------|------|
| `1.` | Rank — 1st best, 2nd best, etc. |
| `fxg5` | SAN (Standard Algebraic Notation) er move |
| `+5.70` | Evaluation jodi ei move khela hoy |
| `fxg5 Bb8 Nf4 h4 ...` | **PV** (Principal Variation) — engine je sequence think korche |
| `W 100% D 0% L 0%` | **WDL** — Win/Draw/Loss probability if this move is played |

Click any move → board e oi move er arrow dekhabe (orange = primary, blue = alternative).

**Apnar example te:**
```
1. fxg5 +5.70
fxg5 Bb8 Nf4 h4 Nxf5+ Kd7 Nxh4 Bc6+
W 100% D 0% L 0%
```
Mane: **fxg5** play korle white 5.70 paw ahead. Engine ei sequence (`fxg5 → Bb8 → Nf4 → ...`) play korbe. Win probability 100% — basically winning position.

---

## 5. Move history (right panel)

Apni je move-gulo khelechen, sob ekhane sequential dekhabe:
```
1. e4 e5 2. Nf3 Nc6 3. Bb5 ...
```

**Click any move** → board oi position e fire jabe, automatic re-analyze hobe. Eshe apni different lines try korte paren.

**Manual edit** korle (palette diye piece bosanole) move history clear hoye jay (text editor er moto)).

---

## 6. Drag-and-drop / Click-to-move

Piece move karor 2 ta way:

### Drag (sob theke easy)
1. Piece er upor mouse press hold korun
2. Destination square e drag korun
3. Drop korun

Drag korar somoy:
- Source square er piece transparent hoye jabe (jate apni dekhte paren ki move korchen)
- Legal destinations e gray dot dekhabe
- Capture squares e red border dekhabe

### Click-to-move
1. Piece e click korun → green border ashbe (picked up)
2. Destination e click korun → move hobe
3. Cancel korte chaile shei piece e abar click korun ba **Esc** press korun

### Right-click
Square clear kore (eraser).

### Palette mode
Bottom palette theke ekta piece select korle "place mode" e jay. Tarpor je square e click korben, oikhane oi piece bose. Eraser button select korle erase mode.

Same piece abar click korle deselect hoy (move mode e fire jay).

---

## 7. Keyboard shortcuts

| Key | Kaaj |
|-----|------|
| **Ctrl + Z** | Undo |
| **Ctrl + Y** ba **Ctrl + Shift + Z** | Redo |
| **F** | Flip board |
| **A** | Analyse |
| **Esc** | Picked-up piece cancel korun |

(Input field e thakle shortcuts disabled — typo te accidentally undo na hoy.)

---

## 8. Visual indicators (board e ki ki ranger boxes)

| Color | Mane |
|-------|------|
| 🟧 Orange arrow | Best move |
| 🟦 Blue arrow | Alternative top move |
| 🟨 Yellow inset | "from" square (last move) |
| 🟧 Light orange | "to" square (last move) |
| 🟩 Green border | Picked-up piece |
| ⚫ Gray dot | Legal move destination (empty square) |
| 🟥 Red border | Legal capture |
| 🔴 Red dot (corner) | Detection low-confidence — manually check ei piece thik ki na |

---

## 9. Apnar specific example explain

Position:
```
8/b3k3/4N3/pKP2ppp/P2N1P2/6P1/6b1/8 w - - 0 1
```

Etar mane:
- White: King on b5, Knights on e6 and d4, Pawns on a4, c5, f4, g3
- Black: King on e7, Bishops on a7 and g2, Pawns on a5, f5, g5, h5
- White to move
- No castling rights anymore (kings already moved)

Apni **g5 played** → engine reply **fxg5** dilo (+5.70 eval = white winning by ~5.7 pawns).

Top move er PV: `fxg5 Bb8 Nf4 h4 Nxf5+ Kd7 Nxh4 Bc6+`

Mane:
1. White: fxg5 (pawn capture)
2. Black: Bb8 (best defense)
3. White: Nf4 (knight repositioning)
4. Black: h4 (push pawn)
5. White: Nxf5+ (knight captures pawn with check)
6. Black: Kd7 (king moves)
7. White: Nxh4 (knight captures pawn)
8. Black: Bc6+ (bishop check)

WDL **W 100% D 0% L 0%** = engine thinks white winning hocche guaranteed.

---

## 10. Tips & Tricks

- **Image upload after a real game**: position detect korle pieces wrong thakte pare (red dot wala). Palette diye fix kore Analyse press korun.
- **Apni khelchen as Black?** Side to move dropdown 'b' korun, then drag your move. App auto-flip korbe to white perspective.
- **Engine training mode**: ELO 1500-2000 set kore khelle realistic opponent feel hobe.
- **Long PGN export**: many moves er por Copy PGN press korun, paste korun lichess.org/paste e — instantly review hobe.
- **Multiple positions analyze**: ekta image upload korun → analyze → another image upload → previous discard hobe (history clear).

---

## File structure (developers er jonno)

```
CHESS-AISST/
├── main.py                   # Flask routes
├── app/
│   ├── engine.py             # Stockfish wrapper (lock, timeout, restart)
│   └── detector.py           # Image → FEN (Sobel projection + templates)
├── extract_templates.py      # Tool: image → piece templates
├── stockfish/stockfish.exe   # Stockfish 17.1 binary
├── piece_templates/          # merida + lichess + chesscom subdirs
├── static/
│   ├── app.js                # Frontend SPA
│   ├── style.css
│   └── pieces/               # 12 PNG piece images
├── templates/index.html
└── venv/                     # Python virtual env
```

Run via: `run.bat` (double-click)
