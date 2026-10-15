# GMMK Pro keymap slot map (slot index → physical key)

Source of truth: the **factory keymap captured from the device** —
`tests/fixtures/mirror_profile11.bin` (full 256-byte config mirror; first
`kind="baseline"` record of `remap/probe_results.jsonl`, captured by the operator
2026-10-15; all 38 live GETs byte-identical), table slice `0x80–0xF7` copied to
`remap/default_keymap.bin` and `src/open_glorious_core/data/default_keymap.bin`.
Decode method: `remap/DISCOVERY.md` ("OPERATOR RUN").

## Layout model

The table is 120 slots = **6 physical rows × 20 slots** (row `r` starts at slot
`(r-1)*20`). Each row uses its first 15 slots for the physical key column order
and leaves 5 trailing `0xFF` spare slots. `0xFF` = unassigned. Bytes are one USB
HID keyboard usage ID per key; a few are vendor-internal codes (see notes).

Row anatomy (columns 1–15):

| Row | Columns |
| --- | --- |
| 1 | Esc F1–F4 · F5–F8 · F9–F12 PrtSc **?14** (knob area) · spare |
| 2 | ` 1 2 3 4 5 6 7 8 9 0 - = Backspace **Del (R1)** · spares |
| 3 | Tab Q W E R T Y U I O P [ ] \ **PgUp (R2)** · spares |
| 4 | Caps A S D F G H J K L ; ' **?72** Enter **PgDn (R3)** · spares |
| 5 | LShift Z X C V B N M , . / RShift **?92** ↑ **End (R4)** · spares |
| 6 | LCtrl LWin LAlt ·· Space ·· RAlt **Fn** RCtrl · ← ↓ → · spares |

Nav cluster (Glorious guide "R1–R4 keycaps": Del / PgUp / PgDn / End) sits in
column 15 of rows 2–5; the arrow cluster + Fn sit at the right of row 6 (guide:
"3× 1u keys to the right of the spacebar for Alt, Fn, Ctrl").

## Notes on the three unlabeled raw bytes

| Slot | Raw | Interpretation |
| --- | --- | --- |
| 14 | `0x02` | Row-1 slot right of PrtSc, under the knob's keycap position. 82-key + knob accounting says there is **no keycap here** — likely the knob (push) or a phantom matrix position. `0x02` is not a standard HID usage — vendor-internal. **Do not remap without verifying physically.** |
| 72 | `0x32` | Between `'` and Enter; row 4 has no room for a keycap there in the ANSI layout (`0x32` = standard ISO `\|` position). ANSI-unpopulated matrix slot. **Leave alone.** |
| 92 | `0x64` | Between RShift and ↑; no Menu key on this unit. No keycap fits that position. **Leave alone.** |

85 slots hold non-`FF` bytes: 82 physical keys + these 3 unconfirmed positions
(`82 + knob = 83` marketing count; the 3 positions are matrix artifacts, not
keycaps — the row widths above confirm no caps fit).

## The 120 slots

### Row 1 — slots 0–19

| Slot | Raw | Key |
| --- | --- | --- |
| 0 | `0x29` | Esc |
| 1 | `0x3A` | F1 |
| 2 | `0x3B` | F2 |
| 3 | `0x3C` | F3 |
| 4 | `0x3D` | F4 |
| 5 | `0x3E` | F5 |
| 6 | `0x3F` | F6 |
| 7 | `0x40` | F7 |
| 8 | `0x41` | F8 |
| 9 | `0x42` | F9 |
| 10 | `0x43` | F10 |
| 11 | `0x44` | F11 |
| 12 | `0x45` | F12 |
| 13 | `0x46` | PrtSc |
| 14 | `0x02` | **RAW 0x02 — see notes** |
| 15 | `0xFF` | *(unassigned spare)* |
| 16 | `0xFF` | *(unassigned spare)* |
| 17 | `0xFF` | *(unassigned spare)* |
| 18 | `0xFF` | *(unassigned spare)* |
| 19 | `0xFF` | *(unassigned spare)* |

### Row 2 — slots 20–39

| Slot | Raw | Key |
| --- | --- | --- |
| 20 | `0x35` | Grave |
| 21 | `0x1E` | 1 |
| 22 | `0x1F` | 2 |
| 23 | `0x20` | 3 |
| 24 | `0x21` | 4 |
| 25 | `0x22` | 5 |
| 26 | `0x23` | 6 |
| 27 | `0x24` | 7 |
| 28 | `0x25` | 8 |
| 29 | `0x26` | 9 |
| 30 | `0x27` | 0 |
| 31 | `0x2D` | Minus |
| 32 | `0x2E` | Equal |
| 33 | `0x2A` | Backspace |
| 34 | `0x4C` | Delete |
| 35 | `0xFF` | *(unassigned spare)* |
| 36 | `0xFF` | *(unassigned spare)* |
| 37 | `0xFF` | *(unassigned spare)* |
| 38 | `0xFF` | *(unassigned spare)* |
| 39 | `0xFF` | *(unassigned spare)* |

### Row 3 — slots 40–59

| Slot | Raw | Key |
| --- | --- | --- |
| 40 | `0x2B` | Tab |
| 41 | `0x14` | Q |
| 42 | `0x1A` | W |
| 43 | `0x08` | E |
| 44 | `0x15` | R |
| 45 | `0x17` | T |
| 46 | `0x1C` | Y |
| 47 | `0x18` | U |
| 48 | `0x0C` | I |
| 49 | `0x12` | O |
| 50 | `0x13` | P |
| 51 | `0x2F` | LBracket |
| 52 | `0x30` | RBracket |
| 53 | `0x31` | Backslash |
| 54 | `0x4B` | PageUp |
| 55 | `0xFF` | *(unassigned spare)* |
| 56 | `0xFF` | *(unassigned spare)* |
| 57 | `0xFF` | *(unassigned spare)* |
| 58 | `0xFF` | *(unassigned spare)* |
| 59 | `0xFF` | *(unassigned spare)* |

### Row 4 — slots 60–79

| Slot | Raw | Key |
| --- | --- | --- |
| 60 | `0x39` | Caps Lock |
| 61 | `0x04` | A |
| 62 | `0x16` | S |
| 63 | `0x07` | D |
| 64 | `0x09` | F |
| 65 | `0x0A` | G |
| 66 | `0x0B` | H |
| 67 | `0x0D` | J |
| 68 | `0x0E` | K |
| 69 | `0x0F` | L |
| 70 | `0x33` | Semicolon |
| 71 | `0x34` | Quote |
| 72 | `0x32` | **RAW 0x32 — see notes** |
| 73 | `0x28` | Enter |
| 74 | `0x4E` | PageDown |
| 75 | `0xFF` | *(unassigned spare)* |
| 76 | `0xFF` | *(unassigned spare)* |
| 77 | `0xFF` | *(unassigned spare)* |
| 78 | `0xFF` | *(unassigned spare)* |
| 79 | `0xFF` | *(unassigned spare)* |

### Row 5 — slots 80–99

| Slot | Raw | Key |
| --- | --- | --- |
| 80 | `0xE1` | Left Shift |
| 81 | `0x1D` | Z |
| 82 | `0x1B` | X |
| 83 | `0x06` | C |
| 84 | `0x19` | V |
| 85 | `0x05` | B |
| 86 | `0x11` | N |
| 87 | `0x10` | M |
| 88 | `0x36` | Comma |
| 89 | `0x37` | Period |
| 90 | `0x38` | Slash |
| 91 | `0xE5` | Right Shift |
| 92 | `0x64` | **RAW 0x64 — see notes** |
| 93 | `0x52` | Up |
| 94 | `0x4D` | End |
| 95 | `0xFF` | *(unassigned spare)* |
| 96 | `0xFF` | *(unassigned spare)* |
| 97 | `0xFF` | *(unassigned spare)* |
| 98 | `0xFF` | *(unassigned spare)* |
| 99 | `0xFF` | *(unassigned spare)* |

### Row 6 — slots 100–119

| Slot | Raw | Key |
| --- | --- | --- |
| 100 | `0xE0` | Left Ctrl |
| 101 | `0xE3` | Left Win |
| 102 | `0xE2` | Left Alt |
| 103 | `0xFF` | *(unassigned spare)* |
| 104 | `0xFF` | *(unassigned spare)* |
| 105 | `0x2C` | Space |
| 106 | `0xFF` | *(unassigned spare)* |
| 107 | `0xFF` | *(unassigned spare)* |
| 108 | `0xE6` | Right Alt |
| 109 | `0xA0` | Fn |
| 110 | `0xE4` | Right Ctrl |
| 111 | `0xFF` | *(unassigned spare)* |
| 112 | `0x50` | Left |
| 113 | `0x51` | Down |
| 114 | `0x4F` | Right |
| 115 | `0xFF` | *(unassigned spare)* |
| 116 | `0xFF` | *(unassigned spare)* |
| 117 | `0xFF` | *(unassigned spare)* |
| 118 | `0xFF` | *(unassigned spare)* |
| 119 | `0xFF` | *(unassigned spare)* |

## Cross-checks (all pass against the dump)

- `slot 0 = 0x29 Esc`, `slot 105 = 0x2C Space`, nav `4C/4B/4E/4D` at
  slots 34/54/74/94, arrows `50/51/4F` at 112/113/114, Fn `0xA0` at 109.
- Modifiers: `E0/E3/E2` (LCtrl/LWin/LAlt) at 100-102, `E1` LShift at 80,
  `E5` RShift at 91, `E6/E4` (RAlt/RCtrl) at 108/110.
- 82 labeled slots, none labeled in a slot the dump reports as `0xFF` — the
  tables above ARE the firmware truth (see the SWEEP-VERDICT note below; the
  G11 note that claimed a slot-58 Caps Lock overriding the dump is retracted).
- Automated in `tests/test_keymap_layout.py` (fixture provenance + spot checks).

## SWEEP-VERDICT note (G13 sweep, G17 — retracts the G11 "EMPIRICAL-FIRMWARE" note)

The tables above transcribe the vendor `Buttoninfo_Default` factory dump, and
on the operator's GMMK Pro Wireless (`320F:5092`, stock WB5092 firmware) that
dump is **correct** about CapsLock: **the physical CapsLock cell is slot 60**,
exactly where the dump puts `0x39` (type `0x01`).

The G11 note ("the physical CapsLock cell is slot 58, not 60 — trust the
firmware, not `Buttoninfo_Default`", operator-reported 2026-10-15 morning) is
**RETRACTED as a misattribution**. The morning "writes to 60 did nothing"
observation is explained by the recovery itself: the recovery recipe wrote
`0x39` to BOTH the vendor cell 60 and the then-suspected cell 58, and the
board answered via 60 — the cell that was live all along.

The retraction evidence is the G13 targeted cell sweep (same board, same
firmware, profile 1 layer 1):

1. GUI bind-first write of `F13 (0x68)` → slot 58 and `F14 (0x69)` → slot 60
   (both cells factory-0xFF-type, activated via the G15 effective types).
2. Caps Lock pressed: the OS hidraw key listener captured `00 00 69` twice —
   F14, slot 60 — and **nothing** from 58 (see `remap/` sweep captures,
   operator-verified 2026-10-15).
3. Factory restore (plain vendor factory table) afterwards: operator —
   "yes its normal again": CapsLock works from the untouched vendor bytes.

Consequences encoded in code and tests (G17):

- Slot 58 is a **dead matrix spare**: it joins the row-4 head cells 55, 56,
  57, 59 as factory-`0xFF`, keycap-less, hidden from the grid
  (`non_keycap_slots` / `NON_KEYCAP_SLOTS`).
- Slot 60 is the Caps Lock keycap: it heads row 4 of the GUI grid
  (`keymap-slots.js`), is labeled in `backend.KEYMAP_SLOT_LABELS`, and the
  factory-default display shows it as `Caps Lock (0x39) · normal` from the
  raw vendor bytes — no display overlay, no write overlay.
- The G12 "empirical caps overlay" (write + display patch at slot 58) is
  removed: a factory reset is the PLAIN vendor factory table, which is
  caps-complete at slot 60.
- Operator re-gate: the caps→Numpad 1 GUI remap retry must be run once more
  and VERIFIED by the app's key-event listener (arm → press → verdict) before
  any further row-4 head-cell experiments. Slot table of record:
  `src/open_glorious_core/static/keymap-slots.js` (row 4 heads at 60); pinned
  by `tests/test_board_matrix_slots.py` / `tests/test_keymap_slots_js.py`.
