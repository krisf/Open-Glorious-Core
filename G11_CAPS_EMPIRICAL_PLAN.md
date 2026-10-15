# G11 — Empirical CapsLock slot (operator-confirmed fix for at-will remapping)

## Empirical truth (would rather have this once than the vendor table twice)

- Board: GMMK Pro WIRELESS `320F:5092`, stock firmware.
- Recovery write (single exchange, full 120-slot matrix to profile 1 layer 1,
  bind first) put code `0x39` at slots 58 AND 60 with types 0x01; operator
  physically confirmed CapsLock restored.
- Operator's failed arc before it (all writes landed — no protocol problem):
  1. GUI grid had CapsLock cap at slot 58; operator set `0x59` (Keypad 1 and
     End): with the board's NumLock off that emits End/nothing → "dead".
  2. Assistant's factory revert pushed the vendor `default_keymap.bin`
     vector: `0xFF` (unassigned) at 58 → **physically killed CapsLock**.
  3. G10 build re-gridded CapsLock cap to slot 60 (vendor-table position);
     operator's num1 assignment and revert both hit 60 → no-ops on the real
     firmware; caps stayed dead through every "restore".
- CONCLUSION: on THIS firmware, CapsLock cell = **slot 58**. Letters verified
  working at our existing slots (operator typed normally all session),
  PrtSc=13 and Escape=0 previously physically verified. Slot 60 currently
  carries `0x39` (vendor-table content, was never empirically identified as
  a physical key; harmless duplicate-caps risk noted in docs — do NOT touch
  it without an operator-confirmed plan).

## Fix spec (at-will caps remapping)

1. Grid (`static/app.js` + slot table): Caps Lock keycap binds slot **58**
   (empirical). Update the shared slot table so the remap drawer reads
   "Slot 58 · Caps Lock (0x39)" for the physical CapsLock cap.
2. Non-keycap advertising: keep 60, 55, 56, 57, 59 hidden (no empirical
   identity for them); /api/keystate and the grid agree.
3. Regression tests: pin CapsLock→58; keep existing pins (PrtSc 13, Esc 0,
   Space 105, Fn 109, arrows 112-114, no-cap-slot-collisions, spares absent).
4. Docs: `remap/KEYMAP_MAP.md` gains a short "EMPIRICAL-FIRMWARE" note:
   WB5092 firmware caps=58 (operator-verified 10-15 vendor-table rewrite
   killed caps; recovery 0x39@58 restored); vendor Buttoninfo_Default lacks
   this truth — trust the firmware, and re-verify any row-4 head cell before
   GUI-remapping it.
5. `G11-REPORT.md`: operator arc above + the at-will-remap ready state.
6. No device I/O. Tests headless. At most 2 commits.
7. Final stdout ends with `G11_DONE tests=<n> commits=<sha1..2>`.
