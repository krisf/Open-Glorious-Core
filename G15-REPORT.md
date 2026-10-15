# G15 Run Report — effective types overlay: GUI writes now activate empirical cells

Plan: `G15_TYPES_OVERLAY_PLAN.md` (repo root, committed with this report,
G7–G14 precedent). All work is headless: zero device I/O in every test
(`tests/conftest.py` keeps `_send_feature_report` /
`_exchange_feature_report` / `_detect_stock_interface` hard-blocked, and an
autouse guard redirects the config dir via `$OGC_CONFIG_DIR` into the test
tmp_path — no test ever touches `~/.config`), no `QApplication`, relative
paths only, `remap/` scripts, fixtures and `src/open_glorious_core/data/`
untouched (both `default_keymap.bin` and `default_types.bin` stay the vendor
bytes — the overlay is write-time + display-time ONLY), no pip installs, no
pushes. Python: `~/.local/share/open-glorious-core/venv/bin/python` with
`PYTHONPATH=src` and cache redirection
(`PYTHONDONTWRITEBYTECODE=1 XDG_CACHE_HOME=scratch/cache
PYTHONPYCACHEPREFIX=scratch/pycache`). Every touched `.js` checked with node
`--check` (`static/app.js`): clean (pinned as a test via `tests/test_verify_js.py`).
Suite: **264 passed** (baseline 263 + 1 new controller overlay test, plus
extended pins in the E2E and verify suites).

## Root cause (root cause #2 — the one that killed the caps key)

G11 fixed the slot (58, not 60). The board died anyway, because there is a
second firmware truth the GUI write path ignored:

**The WB5092 firmware honors the TYPE byte as cell activation. A cell typed
`0xFF` ignores its code byte entirely.**

`apply-keymap` in the controller called `backend.set_keymap_codes(profile,
layer, codes)` with `types=None`, and the backend substitutes the packaged
vendor table (`data/default_types.bin`) — which types the empirical CapsLock
cell 58 as `0xFF` ("unassigned"). The GUI therefore wrote
`codes[58]=0x54, types[58]=0xFF`: a live code byte stuffed into a cell the
firmware refuses to activate. `ok: true` on the wire, a dead key on the
board, and a factory reset (`codes[58]=0xFF`) as the only "working" state.

## Evidence chain (from the plan; device artifacts cited, not touched)

1. **Last-good pending bytes** (`~/.config/open-glorious-core/lastgood-1-1.json`):
   the G12 store captured the pre-apply vector; the applied payload's diff is
   exactly ONE changed cell — `codes[58]=0x54` (caps → NumpadDivide), every
   other byte identical to the factory table. The GUI did exactly what the
   operator asked.
2. **Verify log timestamps** (`logs/verify_20260729_125147.log`): armed
   `12:51:58`, timed out `12:52:58`, zero keystrokes; auto-revert did not
   fire (a seed state is never auto-written — G12 rule 2), so the board kept
   the dead vector. The timeout was also UNFOCUSED input: the operator typed
   into other windows and nothing reached the app (see banner fix, item 4).
3. **`types[58]=0xFF` is the vendor default** (`src/open_glorious_core/data/
   default_types.bin` byte 58): every write that pairs `codes` with the
   packaged table ships `0xFF` onto the empirical caps cell — the silent
   no-op, on every GUI apply, on every board slot the vendor left unassigned.
4. **The recovery-write contrast** (G11, `remap/KEYMAP_MAP.md`
   "WB5092 FIRMWARE EMPIRICAL MAP"): the identical code byte lands the moment
   the type byte is explicit — `codes[58]=0x39, types[58]=0x01` restored the
   caps key. Same slot, same code mechanism; the only variable was the type.
   Vendor-table self-consistency explains why nothing else ever broke: in the
   factory file every non-`0xFF` code sits on a protected type
   (`0x01`/`0x02`/`0x03`), and all 35 type-`0xFF` slots are code `0xFF` —
   the vendor table only breaks the moment a GUI assignment enters a
   type-`0xFF` cell.

## What changed (plan items 1–4)

* **`build_effective_types(codes, default_types)`** (`app.py`, pure function):
  start from the packaged vendor table, then for every slot — protected types
  (`0x01`/`0x02`/`0x03`, i.e. normal keys, the vendor modifier set, the knob's
  consumer typing) are NEVER touched; an unassigned slot (code `0xFF`) keeps
  the vendor byte; any assigned cell with a vendor `0xFF` type becomes `0x01`
  (normal key); a cell assigned a modifier code (`0xE0`–`0xE7`) becomes
  `0x02`, matching the vendor modifier typing.
* **`apply-keymap`** passes the effective types explicitly as the 4th
  argument of `set_keymap_codes` — the wire now carries
  `codes[58]=0x54` riding `types[58]=0x01` (E2E-pinned).
* **reset-keymap**: `backend.reset_keymap` already applies the empirical
  overlay (`codes[58]=0x39, types[58]=0x01`) — a factory reset can never
  write `0xFF` onto the empirically alive caps cell. E2E keeps the pin, now
  explicitly against `0xFF`, plus the knob/modifier survival pins.
* **`/api/keystate`** reports the EFFECTIVE types for every source (slot 58
  with its overlay code `0x39` shows `normal`, not the vendor `0xFF`), and
  each slot gains `type_name` (the label the UI shows) and the boolean
  `cell_active`. The grid tooltip and drawer show the effective label plus a
  "cell active" flag; an honest `0xFF` device read of an unassigned cell
  still renders "unused" — no lying about real bytes.
* **Deliberate superset (recorded):** the G12 auto-revert write
  (`_auto_revert_write`) had the identical defect — restoring a last-good
  GUI vector with vendor types would RE-KILL the assigned cells it exists to
  heal. The overlay is applied there too; `test_auto_revert_*` pins the
  effective types bytes on the revert write. The plan names the GUI apply
  path; this is the same bug class on a GUI write path, and leaving it fixed
  everywhere else would recreate the incident on the next timeout.
* **Banner (item 4):** `VERIFY_ARM_BANNER` is now
  `"Click this app window, then press the key. press your remapped key once (60 s)"`
  — the focus sentence prepended, the G12 core sentence kept byte-for-byte
  so every existing substring pin keeps its teeth.

`backend.py`, `keymap_layout.py`, `key_event_logger.py` and all
`data/*.bin` bytes are untouched.

## Tests (plan item 3; fake HID, d0933d3 suite extended)

* `test_e2e_gui_remap_flow.py` full flow retargeted to the operator's write
  (caps → `NumpadDivide` `0x54`, keystroke `NumpadDivide`): the captured
  cmd-0x03 report pins `codes[58]==0x54` RIDING `types[58]==0x01` (and
  `!= 0xFF`), the knob cell keeps `0x03`, all seven modifier slots keep
  `0x02`, the full types array equals `build_effective_types(codes,
  DEFAULT_TYPES)` — no byte drift; keystate pins the effective-type fields
  (`type_name`/`cell_active`).
* Reset flow: `types[58]==0x01` (not `0xFF`) with `codes[58]==0x39`, every
  other slot verbatim factory, knob + modifier typings survive.
* `test_controller_keymap.py`: new
  `test_apply_keymap_effective_types_overlay` (caps→0x54 → `0x01`; spare cell
  given `0xE0` → `0x02`; knob cell REASSIGNED → `0x03` protected; un-assign →
  type returns to `0xFF`); the full-table apply pin now pins the 5-tuple with
  `TYPES` verbatim (factory vector is self-consistent → overlay is a no-op —
  never blind byte-drift); keystate pins effective types = packaged table
  with slot 58 forced `0x01` on the degraded-factory display path.
* `test_verify_flow.py`: prepended banner pinned (exact + substring),
  `test_auto_revert_restores_and_confirms` pins the revert write's effective
  types (`TYPES` with `[58]=0x01`).

264 passed, 0 failed, fully headless.

## Operator re-gate (device — required before this ships)

1. Plug the board in, start the app, open the keymap panel.
2. Click the Caps Lock cap (slot 58), set the target to NumpadDivide, Apply.
3. When the banner arms, **click this app window first**, then press the
   caps key ONCE within 60 s. Expect:
   `VERIFIED — / now emits Keypad / (0x54)`.
4. If the banner times out, the app writes the last-good vector back — the
   caps key keeps working (it cannot be typed into an inactive cell: the
   restore carries the effective types too).
5. Factory reset the profile in the GUI, then confirm: caps types Caps Lock
   (slot 58 rides `0x39`+type `0x01`), the volume knob scrolls, all
   modifiers work. Slot 60 stays untouched (hidden vendor cell).
6. Only after all six checks pass, treat G15 as operator-verified.

## Commits

1. `fix(keymap): effective types overlay on apply/reset (GUI writes activate empirical cells)`
2. `docs(g15): types-overlay report`
