# G7 — Key Remapping GUI (build plan for a whole-run execution)

Headless executor: complete every deliverable below, offline only. Print the
exact token `G7_DONE` as the last line of your final answer, preceded by a
one-line-per-deliverable checklist. Print `G7_HELP <n>` in any step whose only
sensible continuation needs operator (Hermes) action, plus a message; the
operator must be able to resume with zero re-discovery from your G7-REPORT.md.

## COMMIT DISCIPLINE

- You are allowed at most SIX logical commits (list below). NO scratch, cache,
  .pyc, planning files, or fixtures may be committed.
- Commit 1: backend — default-keymap helpers + shadow-aware matrix verification.
- Commit 2: keycodes framework. Commit 3: controller API. Commit 4: static UI.
- Commit 5: tests. Commit 6: run report + G7-REPORT.md.
- Never push to any remote.

## ENVIRONMENT FUNDS

- Interpreter: `~/.local/share/open-glorious-core/venv/bin/python`
  (pytest is installed). Run all tests from repo root:
  `env PYTHONDONTWRITEBYTECODE=1 XDG_CACHE_HOME=scratch/cache PYTHONPYCACHEPREFIX=scratch/pycache PYTHONPATH=src <venv python> -m pytest -q`.
- Current suite: 106 passing. You MUST leave ≥ 106 + all your new tests green.
- Node available at `/home/ubuntu/.hermes/tools/node-26.7.0-linux-x64/bin/node`
  — run `node --check` on every edited .js file.
- No network needed or allowed beyond what the repo already vendors. No pip
  installs, no package manager commands, no sudo.
- Backend endpoint architecture docs: `remap/PROTOCOL_DECODE.md`.

## HARD RULES

- **DEVICE**: zero device I/O. You have no /dev/hidraw. Never call any
  function that touches a device. All device verification is the operator's
  post-run gate — spec it, don't fake it.
- Relative repo paths only; no absolute-path writes.
- No PySide6/Qt instantiation in tests (headless CI) — importing
  `open_glorious_core.app` is fine (PySide6 imports headless), but never create
  QApplication/QWebEngineView in test code.
- Do not modify `remap/` scripts, PROTOCOL_DECODE.md runtime semantics, or the
  hotspot plumbing decisions below without an explicit note in G7-REPORT.md.
- Do not modify tests/fixtures, source-of-truth .bin data, or any file under
  `src/open_glorious_core/data/`.

## FEATURE SPEC — "Key Remapping" GUI panel

This repo ships a PySide6 desktop shell (`src/open_glorious_core/app.py`) that
serves `src/open_glorious_core/static/` over localhost HTTP inside
QWebEngineView. The lighting panel (`/api/state`, `/api/action`) already works.
You are adding a first-class key-remapping panel to the same web UI. Backend
wire goodness (write path) is PROVEN live on this operator's board
(commit 7484c86, `remap/PROTOCOL_DECODE.md` G6 verdict): cmd 0x03 matrix write
+ cmd 0x01 bind physically land. GET mirror semantics are special:
- The mirror exposes the ACTIVE profile/layer only
  (`_read_config_mirror` enforces this; response carries profile/layer).
- After factory reset the mirror codes table reads **all zero** forever even
  though writes land — treat all-zero codes table as "device shadow buffer
  not initialized; GUI displays/exposes packaged factory defaults instead"
  (`Backend.default_keymap_table()` in Commit 1). The UI must label this
  source honestly (banner: `Device buffer not initialized — showing factory default`).
- Physical verification (user presses keys) is the only true read-back. Never
  claim a GUI false-verified state.

### Deliverable 1 (Commit 1): backend surface polish
- `StockGmmkProBackend.default_keymap_table()` — thin public wrapper around the
  existing `(_read_default_data internal path)`, returns the 120-byte factory
  codes table bytes. Reuse the exact internal helper body style.
- Shadow-aware matrix verify: `_write_keymap_matrix` currently raises on any
  mirror mismatch. Keep that behavior WHEN the mirror read-back table contains
  nonzero content and mismatches. If the read-back codes table is
  **entirely zero** (0x00 × 120, the shadow-only case), do NOT raise:
  return a flag that lets `set_keymap_codes`/`set_keymap`/`reset_keymap`
  return a success message worded:
  `Wrote ... profile P layer L (cmd 3 KeyMatrix); device mirror is blank so read-back could not verify — physical key test is the only confirmation.`
  Keep the exact current hard-error text for the true mismatch branch.
- All tests for this live in `tests/test_backend_keymap_shadow.py`.

### Deliverable 2 (Commit 2): `src/open_glorious_core/keycodes.py`
- `HID_CODE_NAMES: dict[int, str]` for every byte 0x04–0xDF that maps to a
  common USB HID keyboard Usage ID usable as a remap target, plus:
  - vendor specials observed in the factory table: 0xA0 Fn and any others in
    `remap/KEYMAP_MAP.md` non-standard codes section (document each!).
  - media/comsumer keys commonly used as remap targets on this board (mute,
    play/pause, volume, brightness up/down, sleep)*only if* the types byte
    0x03 row supports them (KEYMAP_MAP.md / KEYMAP types file: type 0x03 exists
    for the knob/mute). Media helper names live here with a `TARGET_TYPE`
    marker so the UI can filter.
- `HID_MODIFIER_CODES: dict[int, str]` for 0xE0–0xE7 (Left/Right Ctrl, Shift,
  Alt, GUI).
- Public helper `code_to_name(code: int, *, fall_behaviour: Literal["byte","error"]) -> str`
  returning e.g. `"A (0x04)"` or `"Unassigned (0xFF)"` for 0xFF,
  `"Reserved 0xXX"` for unmapped bytes (fall_behaviour="byte"),
  raise `KeyError` if fall_behaviour="error" and the byte is not merely
  reserved but structurally invalid (>0xFF). Tests in
  `tests/test_keycodes.py`:
  - every byte present in `src/open_glorious_core/data/default_keymap.bin` has
    a non-"Reserved" name (assert this — the fixture is the ground truth and
    must never render as "Reserved" in the GUI).
  - `KEYMAP_SLOT_LABELS` import from backend still resolves (no circular).
- Derivation source — do not hallucinate: read `remap/KEYMAP_MAP.md` slot
  annotations and the fixture bytes for the ~60 codes actually used; only add
  extra names for standard HID usage IDs documented by USB-HID usage tables
  that are UPSTREAM (never single-letter guessing). If in doubt add "TODO
  operator verify" in a comment, not in the shipped map.

### Deliverable 3 (Commit 3): controller API (`src/open_glorious_core/app.py`)
Extend `OpenGloriousCoreWebController` (backend untouched at import level —
still `create_backend()` singleton) with:

- `GET /api/keystate` — same pattern as get_state, returns
  `{ok, backend, message, devices, keymap: {profile, layer, source, is_dirty_source,
  slots: [{slot, code, code_name, code_type_label, physical, is_unassigned, factory_code}],
  can_switch: [...], active_profile, active_layer}}`
  where:
  - codes come from `backend.read_keymap()` then shadow-resolved per spec;
  - `factory_code` from `default_keymap_table()`;
  - `code_type_label` — from KEYMAP types table (normal / modifier /
    consumer / unused); the types table is the packaged default for UI
    display purposes only (never written unless the types file says so);
  - `is_dirty_source` = True when source is the all-zero shadow case.
- `POST /api/action` new actions in `run_action`:
  - `apply-keymap` value `{codes: [120 bytes], }` — write ONLY the ACTIVE
    profile/layer mirror. Build `codes` as a full 120-byte table from the
    GUI buffer, call `backend.set_keymap_codes(profile, layer, codes)`;
    response message from backend goes to `message`.
  - `reset-keymap` value `{}` — `backend.reset_keymap(profile, layer)`.
  - `switch-profile` value `{profile, layer}` — `backend.change_profile(...)`.
  All follow the existing run_action pattern (BackendError → ok:false + message)
  and become threads synchronized with a single shared lock to serialize HID
  access actions (`threading.Lock` held across each backend call, including
  the existing RGB ones — the smallest change that removes interleaved
  ioctl risk).
- Write helper the static UI calls via POST /api/action — no separate POST path.
- Controller-level tests in `tests/test_controller_keymap.py`:
  stub backend with a recording FakeBackend (no device), then check
  frame construction, shadow-seeding behavior, error propagation, and lock
  serialization is *not* observable deadlock.

### Deliverable 4 (Commit 4): static UI — remapping panel
- Read `src/open_glorious_core/static/app.js` (474 lines) + `index.html` (140
  lines) + `styles.css` **first** and extend IN THE EXISTING VISUAL PATTERN.
  Where the lighting controls currently live, ADD a "Key Remapping" section
  (same header style, spacing, controls, `.card` / panel patterns used for
  lighting).
- Grid anatomy from `remap/KEYMAP_MAP.md` layout model: 6 rows × 15 columns
  (slots 0–89 keycap order); skip slots 14/72/92 (no keycap); columns 15–19 of
  each row are unassigned spares (never render in the grid but keep them in
  the buffer). Cap render labels: use `KEYMAP_SLOT_LABELS` via
  `/api/keystate` payload (`physical` name for each slot) — this field MUST
  come from slot labels not from opposite derivation.
- Each keycap is a button: click → focus an editor drawer (right side or below
  the grid):
  - current assignment (code_name from /api/keystate),
  - factory assignment (`factory_code`),
  - search-as-you-type dropdown of assignable names (grouped: alpha /
    numeric / F-keys / modifiers / nav / media / vendor),
  - `Revert this key` (back to factory_code),
  - `Unassign (0xFF)`.
- State model: the frontend edits an in-memory 120-entry buffer cloned from
  the seeded GET state; track `dirty` per slot; header counter `N unsaved
  changes`; the buffer is fully reset (refetch) after successful write or
  reset. Write button label: `Write N key(s) to keyboard` disabled at N=0.
  Write does ONE POST (/api/action apply-keymap) and shows the backend
  `message` in the existing status line (its wording already carries the
  mirror caveat — don't paraphrase it away).
- Reset button: `Reset active profile to factory` (confirm() with the wording
  from backend reset message path).
- Profile/layer cards (prof 1..3, layer 1..3): the ACTIVE one is highlighted,
  others disabled with tooltip `switch-profile first` (exact wording from
  controller `can_switch` hint); when profile/layer is NOT active, all write
  buttons disabled (server enforces; UI mirrors).
- Shadow banner when `is_dirty_source`: precisely
  `Device buffer not initialized — showing factory default`.
  Keep it honest: write still goes to the device when the user explicitly
  clicks Write keys (correct per 7484c86).
- Visual polish bar (must pass operator eyeball): consistent dark theme, no
  FOUC, keyboard grid uses monospace keycap labels with hover lift +
  selected ring + dirty dot badge, media-query for min width 960px. Do not
  introduce any build step (plain CSS/JS — the existing style).
- All strings are inline (no i18n).

### Deliverable 5 (Commit 5) + (Commit 6): run report
- G7-REPORT.md in repo root: what changed, test counts passing, node-check
  results, and an explicit OPERATOR GATE section with exact numbered live
  steps for the operator gate (run whoami-free instructions with the exact
  allowlist of HTTP calls, e.g. "POST /api/action apply-keymap with N keys").
  Note commit hashes inline as they are created.

## SELF-EXIT GATE

Only exit when:
- `python -m pytest -q` ≥ 110 green (106 prior + new; run from repo root env
  above), and
- `node --check` passes on every JS file you touched, and
- all six commits exist, and
- the exact final token is `G7_DONE`, then a checklist:
  `G7_DONE tests=<n> commits=<sha 1..6>`
