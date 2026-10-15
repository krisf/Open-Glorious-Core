# G10 Run Report — board-matrix slot alignment (operator catch: GUI edit killed real CapsLock)

Plan: `G10_UI_TARGET_PLAN.md` (repo root, not committed, G7/G8/G9-plan
precedent). All work is headless: zero device I/O in every test
(`tests/conftest.py` keeps `_send_feature_report` /
`_exchange_feature_report` / `_detect_stock_interface` hard-blocked), no
`QApplication`, relative paths only, `remap/` scripts, fixtures and
`src/open_glorious_core/data/` untouched, no pip installs, no pushes.
Python: `~/.local/share/open-glorious-core/venv/bin/python` with
`PYTHONPATH=src` and cache redirection
(`PYTHONDONTWRITEBYTECODE=1 XDG_CACHE_HOME=scratch/cache PYTHONPYCACHEPREFIX=scratch/pycache`).
Every touched `.js` checked with the pinned node
(`/home/ubuntu/.hermes/tools/node-26.7.0-linux-x64/bin/node --check`):
`static/app.js` and the new `static/keymap-slots.js`, clean.

The protocol/backend write path, `KEYMAP_SLOT_LABELS` and the G8 shadow
honesty contract are unchanged; the protocol surface is byte-identical. The
defect the plan names is purely WHICH SLOT the grid's "Caps Lock" cap
targets — fixed in the UI + the `is_keycap` advertisement, not in the wire.

## Operator evidence (verbatim, G10_UI_TARGET_PLAN.md lines 5-7)

> "i tried editing capslock key to change it to numpad 1 and i click write
> and now the capslock key just doesnt work at all, no original caplock or
> num 1"

- Operator clicked the key the GUI labeled "Caps Lock" — that grid cell wrote
  slot **58** with 0x59 (Keypad 1 and End). The write landed (`ok: true`, one
  cmd 0x03 matrix) and the physical CapsLock went dead with no numpad-1
  anywhere: slots 55-59 are the head of the row-4 USB block, factory **0xFF**
  spares with NO physical keycap (remap/KEYMAP_MAP.md lines 115-119
  "unassigned spare" ×5; `data/default_keymap.bin[55..59]` all 0xFF; the
  dead-write is itself the physical proof — a landed write that changes
  nothing can only have hit a position with no key).
- The REAL CapsLock is slot **60** (0x39): KEYMAP_MAP.md line 125 AND
  `default_keymap.bin[60] == 57`.

### Factory-revert touch-test verdict (received-verbatim)

Recorded exactly as received (G10_UI_TARGET_PLAN.md lines 16-19): the REAL
CapsLock is slot 60 (0x39), and the "user's touch-test after my factory
revert (full 120-slot default, list-form apply-keymap) is the physical
verdict on restore." No physical-restore claim beyond the operator's own
words is asserted anywhere in this round.

## Commits (plan cap: 3 — met)

1. `32d1a12` — `fix(ui): board-matrix slot alignment — real CapsLock at slot 60, head-of-block 0xFF spares excluded`
2. `61387e8` — `feat(ui): matrix self-test aid + unassigned-slot drawer note`
3. `(this)` — `docs(g10): run report`

## Root cause + FIX 1 — table-driven grid binding (`32d1a12`)

Every committed revision of `renderKeymapGrid()` derived the slot from
stride math (`slot = row * KEYMAP_ROW_SLOTS + column`, stride 20). The
plan's proven facts (cap wrote 58, CapsLock is 60) show the running GUI's
per-row assignment drifted from the map — exactly the failure mode arithmetic
invites: a silent per-row offset is invisible in code. The repair class is
removal of the formula:

- New `static/keymap-slots.js`: the six KEYMAP_MAP.md row tables transcribed
  as per-row slot LITERALS (row 1 `0..14`, row 2 `20..34`, row 3 `40..54`,
  **row 4 `60..74` — starts at the real CapsLock slot 60, not 55**, row 5
  `80..94`, row 6 `100..114` with the five `··` spacers as `null`), each
  slot paired with its key name (`null` = no keycap). The table is a
  strict-JSON block between `[KEYMAP-SLOT-TABLE]` markers, DOM-free, exported
  for node, with pure helpers (`slotForCapLabel`, `clickableSlots`,
  `allVisualSlots`, `capName`, `isNonKeycap`).
- `app.js` `renderKeymapGrid()` iterates the table; `slot` is a table cell,
  never a formula. The cap labeled "Caps Lock" binds slot 60 by
  construction; slots 55-59 are in NO visual row, so no cap can bind to them
  at all. Non-keycap visual slots (14/72/92, 103/104/106/107/111) stay
  invisible `.keycap-spacer` divs (G9 FIX A contract: payload `is_keycap`
  wins, the table mirrors offline). If the table fails to load the panel
  fails CLOSED with a problem banner — no arithmetic fallback exists.
- `index.html` loads `keymap-slots.js` before `app.js`; legend rewritten to
  the honest 13-slot text + table-driven-binding statement.
- Honest note on reproduction: the live build that produced the 58-write
  could not be mechanically reconstructed from committed code (the committed
  formula maps row 4 col 0 to 60 on paper). Per the plan's rule the file
  wins for POSITION claims; the new binding is immune to stride drift
  regardless of how 58 was reached, and the operator incident is recorded
  verbatim above.

## FIX 3 — backend `NON_KEYCAP_SLOTS` extended (`32d1a12`)

`app.py`: `NON_KEYCAP_SLOTS = frozenset({14, 55, 56, 57, 58, 59, 72, 92,
103, 104, 106, 107, 111})` — `{55,56,57,58,59}` added to G9's eight. The
comment cites KEYMAP_MAP.md lines 115-119 (five `*(unassigned spare)*`
entries), `default_keymap.bin[55..59] == 0xFF` with slot 60 = 0x39, and the
user's physical dead-write test. `/api/keystate` no longer advertises any of
the 13 as keycaps (`is_keycap: false`, normal AND degraded path), so the UI
can never present a write target with no physical key.

Plan wording note (bytes win for CONTENT, per plan line 38): 14/72/92 are
excluded as keycap-less exactly as G9 pinned, but their FACTORY bytes are
matrix artifacts (0x02/0x32/0x64), NOT 0xFF; the truly-0xFF exclusions are
55-59 and 103/104/106/107/111. The tests pin both facts separately.

## FIX 2 — regression tests (headless, plan list fully pinned)

`tests/test_board_matrix_slots.py` (19 tests, no node): parses the shipped
JSON table and pins it against a hand transcription of KEYMAP_MAP.md 46-194 —
row structure incl. row 4 = `[60..74]`; "Caps Lock" → 60 with 58 bound to NO
cap; 13 → PrtSc, 105 → Space, 109 → Fn, 112/113/114 → Left/Down/Right,
100/101/102/108/110 → LCtrl/LWin/LAlt/RAlt/RCtrl; 90 unique visual slots
(no two caps share a slot); clickable set == 82 == visual ∖ NON_KEYCAP,
disjoint from G9's 8 ∪ {55..59}, every clickable slot factory-byte ≠ 0xFF,
row-6 spacers = 0xFF, artifacts 14/72/92 ≠ 0xFF; table names == backend
`KEYMAP_SLOT_LABELS` (slot→name bijection); `app.NON_KEYCAP_SLOTS` == the
13-set == table's `non_keycap_slots`.

`tests/test_keymap_slots_js.py` (15 tests, pinned node): the module loads
DOM-free; `slotForCapLabel("Caps Lock") === 60`; `allVisualSlots()` excludes
58; triple-confirmed caps resolved in-node; `clickableSlots()` = 82 unique,
none in the 13; `index.html` loads the table before `app.js`; `app.js`
contains the table binding and NOT the `row * KEYMAP_ROW_SLOTS` formula (the
defect class is pinned absent).

`tests/test_controller_keymap.py`: the G9 `is_keycap` pin grown to the
13-set (+55-59 bytes 0xFF, +slot 60 keycap 0x39, +degraded-path 58/60).

## FIX 4 — unassigned-slot drawer note (`61387e8`)

`index.html` + `app.js` + `styles.css`: a selected slot whose FACTORY code
is 0xFF gets "Unassigned matrix slot — no physical key (factory 0xFF).
Writes here change no key (remap/KEYMAP_MAP.md)." **instead of** the normal
editor (facts/remap-search/actions hidden). The predicate
`keycapSlotIsUnassigned` is pure (in the `[PURE-DRAWER]` marker section,
node-tested). After FIX 1 no such slot is selectable from the grid — this is
the plan's second line of defense for any future payload that advertises a
0xFF-factory slot as a keycap.

## FIX 5 — matrix self-test aid, DEFAULT OFF (`61387e8`)

"Run matrix self-test" button + `--self-test-matrix` CLI flag. Fires ONLY on
an explicit operator click/flag — never on page load, never on any other
action (`runMatrixSelfTest` has exactly one call site: the click wiring,
pinned by test). Read-only by construction: the handler audits the config
mirror via `_backend_io(read_keymap)` (IO lock) — the same GET traffic as
opening the panel — and records ZERO writer calls (pinned against
FakeBackend: `set_keymap_codes`/`reset_keymap`/`change_profile` absent).
Report: per-slot `as mapped` / `DEVIATES (factory 0xNN)` / `UNASSIGNED (0xFF)
— keycap dead`, the 13 keycap-less slots listed, verdict `Caps bound: 82;
slot 58 bound to a cap: NO`, and the honesty line that codes are not presses
— physical aliveness is confirmed only by pressing keys. Degrades honestly:
G8 blank-mirror signature and all-zero shadow mirror audit the packaged
factory defaults with an explicit NOTE; non-blank failures stay `ok:false`;
no device → `ok:false` + install hint. CLI prints the audit and exits before
server/Qt startup (pinned). The report table is loaded from the SAME
`keymap-slots.js` JSON block (`load_slot_table`) — no second Python
transcription, the drift class that produced this incident.

## Verification

- Full suite: **227 passed** (baseline 186 + 41 new G10 tests), 0 failures,
  0 skips, ~1.1 s, zero device I/O (conftest blocker untouched, no test
  monkeypatches it).
- `node --check` clean on both touched `.js` files.
- Grep proof: no `KEYMAP_ROWS` / `KEYMAP_ROW_SLOTS` /
  `row * KEYMAP_ROW_SLOTS` remain in `app.js`.
- Self-test report smoke: factory table → all `as mapped`; slot 60 mutated
  to 0x59 → `DEVIATES (factory 0x39)`; to 0xFF → `UNASSIGNED … keycap
  dead`; verdict `slot 58 bound to a cap: NO`.

## Constraints kept

Plan not committed; `remap/` scripts + fixtures untouched
(`remap/powercycle_watch.log`, `remap/smoke_results.jsonl` remain untracked
per G9 precedent); `src/open_glorious_core/data/` untouched (read-only
fixture reads in tests only); `tests/fixtures/` untouched; no pip installs;
no pushes; relative paths throughout; `pyproject` `static/*` package-data
already ships `keymap-slots.js` — no packaging change.
