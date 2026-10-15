# G17 Run Report — slot-60 truth: the GUI reverts the G11 empirical error

Plan: `G17_SLOT60_TRUTH_PLAN.md` (repo root, committed with this report,
G7–G16 precedent). All work is headless: zero device I/O in every test
(`tests/conftest.py` keeps `_send_feature_report` /
`_exchange_feature_report` / `_detect_stock_interface` hard-blocked, and an
autouse guard redirects the config dir via `$OGC_CONFIG_DIR` into the test
tmp_path), no `QApplication`, relative paths only,
`src/open_glorious_core/data/` untouched (the packaged vendor dumps stay
byte-for-byte vendor), no pip installs, no pushes.
Python: `~/.local/share/open-glorious-core/venv/bin/python` with
`PYTHONPATH=src` and cache redirection. The touched `.js`
(`static/app.js`, `static/keymap-slots.js`) are node `--check` clean.
Suite: **267 passed** — a re-pin round, not an additions round: every G11
"caps = slot 58" pin is reversed to the sweep truth, and the G12 overlay
pins became plain-vendor-table pins.

## Verdict (the one sentence)

**On this GMMK Pro Wireless (320F:5092, stock WB5092 firmware) the real
CapsLock cell is SLOT 60 — exactly where the vendor `Buttoninfo_Default`
factory dump puts `0x39` with type `0x01`.** The G11 "empirical" conclusion
(caps = slot 58) is RETRACTED: it was a misattribution, and it is the reason
every GUI remap of Caps Lock "did nothing" from G11 onward — the GUI aimed
the write at dead matrix spare 58.

## Sweep evidence chain (G13 sweep, 10-15 14:4x, operator-run on the live board)

1. **Probe write (G16 GUI path, bind-first).** Via the app: `F13 (0x68)`
   written to slot 58, `F14 (0x69)` to slot 60, every other cell factory.
   Both cells were factory-0xFF-type spares, activated by the G15 effective
   types overlay — the write itself provably lands.
2. **Capture.** The operator pressed Caps Lock. The OS hidraw key listener
   emitted `00 00 69` — F14, the code written to **slot 60** — twice (the
   press and its release shape), and **nothing** from slot 58. The physical
   CapsLock switch matrix-scans at 60.
3. **Factory restore.** A plain factory reset afterwards: operator —
   "Ss yes its normal again". CapsLock lives again from the untouched
   vendor bytes; the factory vector is caps-complete **as shipped**.

Why G11 was wrong: the morning recovery wrote `0x39` to BOTH slots 58 and 60
and caps returned — via 60, the cell that was live all along. "Writes to 60
did nothing" was never tested in isolation; the write that "restored" caps
into 58 restored it into 60 at the same time.

## The fix (retractions in code, one cell of truth)

1. **`backend.py`** — the G12 `apply_empirical_overlay` /
   `EMPIRICAL_CAPS_*` block is DELETED. `reset_keymap` now writes the
   PLAIN packaged vendor codes + types, byte for byte (the vector is
   caps-complete at slot 60). `KEYMAP_SLOT_LABELS`: Caps Lock label moves
   58 → 60.
2. **`app.py`** — the G12 item-6 display overlay and the per-slot
   `"overlay"` payload field are DELETED (factory-default degradation
   renders slot 60 as `Caps Lock (0x39) · normal` on the raw vendor bytes).
   `NON_KEYCAP_SLOTS` gains 58, drops 60. Matrix self-test report lines
   state the sweep verdict (`CapsLock = slot 60`, `slots 55-59 bound to a
   cap: NO`).
3. **`static/keymap-slots.js` + `static/app.js` + `index.html`** — row 4
   heads at slot **60**; the hidden head cells are 55–59 (dead spare 58
   included); legend + comments carry the SWEEP-VERDICT wording. The
   drawer's factory-0xFF "unassigned" predicate keeps its keycap override
   (harmless general rule), now needed for no caps cell at all.
4. **`remap/KEYMAP_MAP.md`** — the "EMPIRICAL-FIRMWARE" section is replaced
   by "SWEEP-VERDICT": vendor dump is CORRECT about CapsLock; slot 58 is a
   dead matrix spare; the sweep chain above is recorded.
5. **KEPT as-is (plan item 4):** the G15 effective-types overlay (assigned
   0xFF-type cells activate as 0x01 — real, E2E-pinned, and now exercised
   on genuine spares in the tests) and the G16 bind-first write path (the
   sweep itself ran through it).

## Files

- `src/open_glorious_core/backend.py` — overlay deleted, label 58→60,
  plain-table `reset_keymap`.
- `src/open_glorious_core/app.py` — display overlay + `overlay` field
  deleted, `NON_KEYCAP_SLOTS` ±, self-test wording.
- `src/open_glorious_core/static/keymap-slots.js` — row 4 slots head at 60,
  `non_keycap_slots` 58 in / 60 out.
- `src/open_glorious_core/static/app.js`, `index.html` — mirrors + legend.
- `remap/KEYMAP_MAP.md` — SWEEP-VERDICT note.
- `tests/test_keymap_layout.py`, `test_backend_keymap_shadow.py`,
  `test_board_matrix_slots.py`, `test_controller_keymap.py`,
  `test_e2e_gui_remap_flow.py`, `test_keymap_slots_js.py` — un-pinned the
  G11/G12 slot-58 claims, re-pinned: caps cap → slot 60, reset = plain
  vendor table (`codes[60]==0x39`, `codes[58]==0xFF`), hidden set includes
  58, G15 overlay pins moved onto a genuine 0xFF spare (slot 15/16).

## Operator re-gate (what to test on the board)

1. Rebuild/relaunch the app, open the keymap drawer: Caps Lock must render
   at slot 60 as `Caps Lock (0x39) · normal` (no banner needed — the
   factory bytes now tell the truth).
2. Remap **Caps Lock → Numpad 1**, press Write, then **verify with the
   app's listener** (arm → press → verdict). This is the exact G11-era
   remap that failed under the slot-58 misattribution; it should now work
   end-to-end on the first try.
3. Report the verdict. Only after this passes are any further row-4
   head-cell (55–59) experiments allowed — they are dead spares and must
   stay hidden.
