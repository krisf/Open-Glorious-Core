# G7 Run Report — Key Remapping GUI (headless build)

Date: 2026-08-03 (session build). Plan: `G7_GUI_PLAN.md` (repo root, not committed).
All work is headless: zero device I/O in every test (`tests/conftest.py` keeps
`_send_feature_report` / `_exchange_feature_report` / `_detect_stock_interface`
hard-blocked), no `QApplication`/`QWebEngineView` in tests, relative paths only,
`remap/` scripts and `src/open_glorious_core/data/` untouched.

## What changed (six commits)

| # | Commit | Deliverable |
|---|--------|-------------|
| 1 | `87fee82` | `backend.py`: public `default_keymap_table()`; `KEYMAP_BLANK_TABLE`; shadow-aware `_write_keymap_matrix()` — an all-zero mirror read-back is "cannot verify" (G6: writes land) and yields honest `Wrote … (cmd 3 KeyMatrix); device mirror is blank so read-back could not verify — physical key test is the only confirmation.` messages from `set_keymap_codes` / `set_keymap` / `reset_keymap`; the true-mismatch branch keeps its exact prior hard-error text |
| 2 | `b1cda04` | New `keycodes.py`: upstream USB HID page-0x07 names 0x04–0x99 (0x9A–0xDF stay reserved), `HID_MODIFIER_CODES` 0xE0–0xE7, vendor specials `0xA0` Fn / `0x02` Knob-Mute (both documented in `remap/KEYMAP_MAP.md`), `TARGET_TYPE` consumer markers for media, groups, `code_to_name(code, fall_behaviour)` — zero project imports (no cycles) |
| 3 | `2d67305` | `app.py`: `GET /api/keystate` (active-pair mirror read, shadow-resolved to factory defaults, per-slot catalog, `can_switch` 3×3 with `switch-profile first` hints, code catalog for the dropdown); POST actions `apply-keymap` (120×int validation), `reset-keymap`, `switch-profile` (1–3 validated); controller-wide `threading.Lock` serializing **every** HID-touching backend call, RGB actions included |
| 4 | `698efd9` | Static UI: "Key Remapping" panel — 6×15 keycap grid (cols 16–20 spares never rendered; no-keycap slots 14/72/92 rendered as non-editable holes), editor drawer (current/factory/type, searchable grouped dropdown, Revert, Unassign), `N unsaved changes` counter + dirty dots, `Write N key(s) to keyboard` (disabled at 0), `Reset active profile to factory` with confirm, profile/layer 3×3 cards, shadow banner `Device buffer not initialized — showing factory default`; plain CSS/JS, no build step |
| 5 | `a849b01` | Tests: `tests/test_keycodes.py` (17), `tests/test_backend_keymap_shadow.py` (14), `tests/test_controller_keymap.py` (14) |
| 6 | (this commit) | `G7-REPORT.md` (this file) |

## Test results

- Command (repo root):
  `env PYTHONDONTWRITEBYTECODE=1 XDG_CACHE_HOME=scratch/cache PYTHONPYCACHEPREFIX=scratch/pycache PYTHONPATH=src ~/.local/share/open-glorious-core/venv/bin/python -m pytest -q`
- Result: **151 passed** in ~0.4 s (baseline 106 → +45 new; gate ≥ 110 ✔).
- New coverage: shadow vs. true-mirror verification (exact message texts pinned),
  `default_keymap_table()` == shipped `data/default_keymap.bin`, every factory
  byte names non-`Reserved`, `code_to_name` contracts (`A (0x04)`,
  `Unassigned (0xFF)`, `Reserved 0xA1`, `KeyError` for >0xFF in **both** modes),
  `/api/keystate` device + shadow shapes, action routing/validation,
  IO-lock serialization under 6 concurrent threads + non-reentrancy contract.

## Node checks

- `/home/ubuntu/.hermes/tools/node-26.7.0-linux-x64/bin/node --check src/open_glorious_core/static/app.js` → **OK** (only .js file touched; `index.html`/`styles.css` carry the panel).

## Shadow semantics (shipped behavior)

After a factory reset the GET config mirror's codes table reads `0x00 × 120`
forever while cmd 0x03 writes physically land (G6 verdict,
`remap/PROTOCOL_DECODE.md`). The app therefore: never displays the blank
table (shows packaged factory defaults, `source: "factory-default"`,
`is_dirty_source: true`, banner shown), never treats a blank read-back as a
write failure, and words every shadow write `Wrote … physical key test is the
only confirmation.` A non-blank mismatching read-back still raises the exact
hard error. Consumer-page media keys beyond keyboard-page Mute/Vol± are
documented in `keycodes.py` as `TODO operator verify` and are NOT shipped in
the map.

## OPERATOR GATE (live device — Hermes, after this build)

Preconditions: GMMK Pro connected, udev rule loaded, `open-glorious-core-app`
running (PySide6 window; server binds `127.0.0.1:0`, port printed on stdout as
`… serving UI at http://127.0.0.1:<port>`). Use `PORT` from that line.

Allowlist — ONLY these HTTP calls, in this order:

1. `GET http://127.0.0.1:$PORT/api/keystate` → expect `ok:true`; if
   `keymap.is_dirty_source` is true, the GUI must show the banner
   `Device buffer not initialized — showing factory default` and the grid must
   show factory legends (Esc/F1/… ), not blanks.
2. In the UI (equivalent to `POST /api/action {"action":"switch-profile","value":{"profile":1,"layer":1}}`):
   ensure the active card is Profile 1 / Layer 1 (device power-cycled to
   factory state per G6 procedure → active pair is (1,1)).
3. UI: click Esc keycap → Remap to **F13** (0x68) → counter reads
   `1 unsaved changes` → press `Write 1 key(s) to keyboard`
   (= `POST /api/action {"action":"apply-keymap","value":{"codes":[…120 ints…]}}`,
   slot 0 = 104).
4. **PHYSICAL KEY TEST (the only true verification):** press Esc on the
   keyboard → the OS must receive F13 (`xev` / `weasyprint`-free check:
   `sudo evtest` or a text editor + remap tester). Record result.
5. UI: `Reset active profile to factory` (confirm) → press Esc → must type Esc
   again. (= `POST /api/action {"action":"reset-keymap"}`)
6. UI: switch to Profile 2 (card) → keystate refetches for (2,1);
   switch back to (1,1). (= `POST /api/action {"action":"switch-profile","value":{"profile":2,"layer":1}}` etc.)

Any HTTP call outside this allowlist, any device I/O outside the app, any
`remap/` script run: NOT part of the gate. Report gate results (esp. step 4)
back to the repo; a failing step 4 changes the G6 "writes land" verdict and
must be filed as an operator finding, not patched around.
