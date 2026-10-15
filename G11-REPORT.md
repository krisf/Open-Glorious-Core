# G11 Run Report — empirical CapsLock slot: the grid now binds Caps Lock to 58

Plan: `G11_CAPS_EMPIRICAL_PLAN.md` (repo root, committed with this report,
G7–G10 precedent). All work is headless: zero device I/O in every test
(`tests/conftest.py` keeps `_send_feature_report` /
`_exchange_feature_report` / `_detect_stock_interface` hard-blocked), no
`QApplication`, relative paths only, `remap/` scripts, fixtures and
`src/open_glorious_core/data/` untouched, no pip installs, no pushes.
Python: `~/.local/share/open-glorious-core/venv/bin/python` with
`PYTHONPATH=src` and cache redirection
(`PYTHONDONTWRITEBYTECODE=1 XDG_CACHE_HOME=scratch/cache PYTHONPYCACHEPREFIX=scratch/pycache`).
Every touched `.js` checked with node `--check` (`static/app.js`,
`static/keymap-slots.js`): clean. Suite: **227 passed**.

The protocol/backend write path and the G8 shadow honesty contract are
unchanged; this is the same class of fix as G10 — WHICH SLOT the grid's
"Caps Lock" cap targets — except G11 corrects G10's own correction: the
vendor table itself was the liar.

## The operator arc (verbatim evidence, G11_CAPS_EMPIRICAL_PLAN.md)

Board: GMMK Pro WIRELESS `320F:5092`, stock WB5092 firmware. Every write in
this arc LANDED (`ok: true`, one cmd 0x03 matrix) — the protocol was never
the problem; the slot table was.

1. G9 GUI: CapsLock cap bound to slot **58**; the operator wrote `0x59`
   (Keypad 1 and End). With NumLock off that key emits End/nothing — the key
   *looked* dead (and G10 read it that way).
2. A factory revert pushed the vendor `default_keymap.bin` vector — `0xFF`
   (unassigned) at 58 — and **physically killed CapsLock**.
3. The G10 build re-gridded the CapsLock cap to the vendor-table position,
   slot **60**. The operator's num1 assignment and every revert hit 60:
   no-ops on the real firmware. CapsLock stayed dead through every
   "restore".
4. Recovery: one exchange wrote the full 120-slot matrix (bind first) with
   `0x39` at **both 58 and 60**, types 0x01. The operator physically
   confirmed: **CapsLock works again**.

**Conclusion (empirical truth, operator-verified 2026-10-15): on THIS
firmware the CapsLock cell is slot 58.** The vendor `Buttoninfo_Default`
(`0xFF`@58, `0x39`@60) is wrong there — G10 pinned the grid to the vendor's
60, which is why every G10-era "restore" missed the real cell. Letters type
normally at their existing slots (operator typed all session), PrtSc=13 and
Escape=0 were previously physically verified, so the rest of the table
stands.

## What changed (the at-will-remap ready state)

- **Grid binds Caps Lock → slot 58.** `static/keymap-slots.js` (single
  source of truth, transcribed into the row tables) heads row 4 with `[58,
  61..74]`; `static/app.js` renders and writes from it, never a formula.
  Clicking the physical Caps Lock cap opens the drawer titled
  "Caps Lock · slot 58", with **Current** showing the live code (`0x39`
  since recovery) — the operator can now remap Caps Lock at will, and the
  write aims at the cell that physically *is* CapsLock.
- **Drawer honesty, refined.** G10 hid the editor on factory-`0xFF` slots.
  Slot 58's *vendor* factory byte is `0xFF` — under G10 that would have made
  the real CapsLock cap "unassigned, writes change no key" and un-remappable.
  `keycapSlotIsUnassigned(meta, slotHasKeycap)` now respects the
  firmware-confirmed keycap (`is_keycap` from `/api/keystate`, mirrored
  offline by the slot table): an empirical keycap always gets the editor;
  the honest unassigned note remains for keycap-less cells.
- **Hidden cells: 55, 56, 57, 59, 60.** No empirical identity — none is in a
  visual row, none is clickable, `is_keycap: false` on `/api/keystate` and
  in the grid agree (`NON_KEYCAP_SLOTS` = `{14, 55, 56, 57, 59, 60, 72, 92,
  103, 104, 106, 107, 111}`). **Slot 60 keeps its duplicate `0x39` on the
  device: do NOT touch it** — no empirical plan exists for it (documented
  duplicate-caps risk).
- **Backend labels move with the grid:** `KEYMAP_SLOT_LABELS[58] =
  "Caps Lock"`, slot 60 unlabeled — the triple (slot table / backend labels
  / `NON_KEYCAP_SLOTS`) is pinned consistent by tests.
- **Matrix self-test tells the empirical story:** header carries
  "CapsLock = slot 58 … the vendor table … is wrong"; scan row
  `slot  58  Caps Lock  code 0xNN`; the keycap-less block lists
  `55, 56, 57, 59` (dead-write cells) and `60` (vendor-table caps
  duplicate, hidden); the defect pin is now
  "slots 55-57, 59, 60 bound to a cap: NO". Byte-honest: a live `0x39`@58
  renders against the vendor byte `0xFF`; the header marks that pair as the
  empirical truth, not a deviation.
- **Docs:** `remap/KEYMAP_MAP.md` gained the **EMPIRICAL-FIRMWARE** note —
  WB5092 caps=58 operator-verified; vendor `Buttoninfo_Default` lacks this
  truth; trust the firmware; re-verify a row-4 head cell's physical identity
  before GUI-remapping it. Existing line-number references in code comments
  stay valid (note appended at the end of the map).

## Pins (all green, headless, zero device I/O)

- CapsLock cap → slot 58; `capName(58) == "Caps Lock"`; slot 60 in no
  visual row, `is_keycap` false, in `non_keycap_slots`;
  `FACTORY[58] == 0xFF` + `FACTORY[60] == 0x39` pinned as the vendor-bytes
  evidence (the documented exception to "clickable caps have real factory
  bytes").
- Kept intact: PrtSc 13, Esc 0, Space 105, Fn 109, arrows 112–114, 82
  unique clickable caps, no cap-slot collisions, spares absent, the G8/G9
  honesty and degraded-path contracts, drawer note/editor wiring (now
  2-arg, with the empirical-keycap override pinned).
- Self-test reports (live mirror, blank mirror, CLI) carry the G11 strings;
  deviation scan flips to the empirical cell (`0x59`@58 DEVIATES against
  factory `0xFF`).
