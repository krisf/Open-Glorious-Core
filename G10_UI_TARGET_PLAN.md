# G10 — Board-matrix key alignment (operator catch: GUI edit killed real CapsLock)

## What happened (live operator evidence, user verbatim)

> "i tried editing capslock key to change it to numpad 1 and i click write
> and now the capslock key just doesnt work at all, no original caplock or
> num 1"

- Operator clicked the key the GUI labeled "Caps Lock" — that grid cell wrote
  slot **58** with 0x59 (Keypad 1 and End). Write landed (`ok: true`, one
  cmd 0x03 matrix) but the physical CapsLock died and no numpad-1 appeared:
  slots 55-59 are 0xFF matrix spares with NO physical keycap (proven:
  remap/KEYMAP_MAP.md lines 115-119 "unassigned spare", factory bytes
  default_keymap.bin[55..59] all 0xFF, and the user's physical test — the
  landed write did nothing at all).
- The REAL CapsLock is slot **60** (0x39): KEYMAP_MAP.md line 125 AND
  default_keymap.bin[60] = 57. User's touch-test after my factory revert
  (full 120-slot default, list-form apply-keymap) is the physical verdict on
  restore — G10-REPORT.md must record it as received-verbatim.
- G9's search fix works (operator: "the search now works"); G8 shadow
  honesty works; the defect is purely WHICH SLOT the grid's "Caps Lock" cap
  targets — a UI layout/assignment bug in static/app.js (+ possibly
  index.html), NOT a protocol bug (cmd 0x03 wrote exactly what the UI told
  it to).

## PROVEN slot facts the fix MUST be built from (no invented coordinates)

- Real keys, byte+map+physical triple-confirmed: slot 13 = PrtSc (user
  physically verified G6 demo), slot 60 = Caps Lock, slot 105 = Space,
  slot 109 = Fn, slot 112/113/114 = Left/Down/Right, slots 100/101/102/108/110
  = LCtrl/LWin/LAlt/RAlt/RCtrl.
- 0xFF spares with NO physical cap: slots 14, 72 (?72 artifact), 92 (?92),
  103/104/106/107/111 (bottom-row gaps) AND 55/56/57/58/59 (head of the row-4
  block, before Caps) — the last five are the class the G9 NON_KEYCAP_SLOTS
  list missed.
- KEYMAP_MAP.md is the single source of truth for visual-order slot mapping;
  default_keymap.bin is the byte cross-check. If they ever disagree, the file
  wins for POSITION claims and the bytes win for CONTENT claims — but a
  disagreeing pair is itself a bug to surface, not paper over.

## Fix spec

1. Grid slot assignment (`static/app.js`): rebuild each visual row's
   slot mapping from the KEYMAP_MAP.md row tables (skip leading 0xFF spares
   within each USB block — row 4 starts at slot 60, not 55), and drive the
   per-cap slot numbers from that table. Any cap whose slot is 0xFF or not
   in the map's visual-row tables must not render as a clickable keycap.
2. Regression tests (headless, pure logic — factor the slot-table into a
   small module or JSON the test can import if app.js keeps it in JS):
   - the cap labeled "Caps Lock" resolves to slot 60 (and slot 58 is not
     bound to any cap);
   - slot 13 → PrtSc, 105 → Space, 109 → Fn, 112-114 → arrows;
   - the clickable-cap slot set is exactly the non-0xFF visual slots from
     the map — the union of the previous NON_KEYCAP_SLOTS and
     {55,56,57,58,59} must all be absent;
   - no two caps share a slot.
3. Backend (`app.py`): extend `NON_KEYCAP_SLOTS` with {55,56,57,58,59} so
   `/api/keystate` stops advertising them as keycaps at all (comment must
   cite KEYMAP_MAP.md lines 115-119 + the user's physical dead-write test).
4. UX hardening (small): in the remap drawer, when the selected slot's
   factory code is 0xFF, show the same "unassigned matrix slot — no physical
   key" note the spares get instead of a normal editor.
5. Self-test aid, DEFAULT OFF (explicit operator click only):
   `Run matrix self-test` button + `--self-test-matrix` CLI flag: scans and
   prints which GUI-labeled keys are physically alive per the map; NEVER
   auto-writes anything.
6. Tests headless (conftest device double only), no pip installs, no pushes,
   relative paths only. Do NOT touch remap/ scripts, fixtures, or
   src/open_glorious_core/data/.
7. At most 3 commits:
   a. `fix(ui): board-matrix slot alignment — real CapsLock at slot 60, head-of-block 0xFF spares excluded`,
   b. `feat(ui): matrix self-test aid + unassigned-slot drawer note`,
   c. `docs(g10): run report` (G10-REPORT.md, includes the operator
      verbatim evidence above and the factory-revert touch-test verdict).
8. Environment: venv `~/.local/share/open-glorious-core/venv/bin/python`,
   `PYTHONPATH=src`, cache redirection env, node
   `/home/ubuntu/.hermes/tools/node-26.7.0-linux-x64/bin/node` — `node
   --check` every touched .js.
9. Final stdout ends with `G10_DONE tests=<n> commits=<sha1..3>`.
