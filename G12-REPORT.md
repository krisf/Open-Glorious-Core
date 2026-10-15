# G12 Run Report — automated remap verification + self-healing revert

Plan: `G12_AUTOVERIFY_PLAN.md` (repo root, committed with this report,
G7–G11 precedent). All work is headless: zero device I/O in every test
(`tests/conftest.py` keeps `_send_feature_report` /
`_exchange_feature_report` / `_detect_stock_interface` hard-blocked, and an
autouse guard redirects the config dir via `$OGC_CONFIG_DIR` into the test
tmp_path — no test ever touches `~/.config`), no `QApplication`, relative
paths only, `remap/` scripts and `src/open_glorious_core/data/` untouched
(including `default_keymap.bin` — the overlay is display + write-time ONLY),
no pip installs, no pushes. Python:
`~/.local/share/open-glorious-core/venv/bin/python` with `PYTHONPATH=src`
and cache redirection (`PYTHONDONTWRITEBYTECODE=1 XDG_CACHE_HOME=scratch/cache
PYTHONPYCACHEPREFIX=scratch/pycache`). Every touched `.js` checked with node
`--check` (`static/app.js`): clean (also pinned as a test).
Suite: **263 passed** (baseline 227 + 36 new).

## Why (the physics constraint)

A remap's effect on this hardware is observable ONLY through a physical
keystroke: the WB5092 shadow buffer never populates (G8), so read-back can
verify nothing. G11 proved the failure mode end-to-end — a write to the
WRONG slot, or a factory vector with `0xFF` at the empirical CapsLock cell
(slot 58), leaves a key physically dead while every API answers `ok: true`.
G12 closes the loop the only way the hardware allows: arm a one-key test,
listen for the operator's keystroke, and if 60 s pass with no input, WRITE
THE LAST-GOOD CONFIG BACK.

## What changed

1. **Last-good snapshot store** — `src/open_glorious_core/lastgood.py`
   (new). One JSON file per (profile, layer):
   `~/.config/open-glorious-core/last-good-<p>-<l>.json` (overridable via
   `$OGC_CONFIG_DIR`), written atomically (tmp + `os.replace`). Every
   applied matrix is PERSISTED, but applied codes ride in `pending` — the
   revertable `codes` change ONLY on an operator-confirmed keystroke
   (`confirm()`), so an unconfirmed write can never become its own revert
   target. First touch seeds the factory vector flagged `seed: true,
   verified: false` — honestly, a seed has NEVER been confirmed.
2. **`POST /api/keyevent`** — one keydown forwarded as `{code, key}`;
   localhost-only, NO storage beyond the armed pending (events seen while
   nothing is armed are dropped, not logged, not stored). The page logger
   (`static/app.js`) forwards every keydown while the app has focus;
   transport failures are invisible to the UI (a lost event self-heals).
3. **Verify-armed matcher** — `POST /api/verify-remap {slot,
   expect:[codes…]}` arms a server-side 60 s timer; banner
   `press your remapped key once (60 s)`. The matcher maps the EXPECTED HID
   codes to KeyboardEvent.code variants through an EXPLICIT table
   (`KEY_EVENT_CODE_VARIANTS`): the NumLock class of the capslock incident
   is first-class — `0x59` matches BOTH `Numpad1` AND `End` (same for the
   whole keypad block, e.g. `0x5F` → Numpad7/Home), `0x68` → `F13`, `0x04`
   → `KeyA`. Codes the browser can never observe (Fn `0xA0`, the knob
   `0x02`, `0xFF`) get NO variants — never a false VERIFIED, the self-heal
   decides instead. A match: `VERIFIED — <key> now emits <name>`, timer
   cancelled, last-good promoted to the confirmed revert target.
4. **Auto-revert on no input** — the timeout writes the last-good matrix
   back through the PROVEN G11 recovery path (cmd-0x01 bind first, then
   exactly ONE cmd-0x03 full-matrix write — `_io_lock` acquired per call,
   the verify lock never nested inside it); banner
   `no input seen — auto-reverted to last good config`; the outcome lands
   in the app log (`verify-timeout: … auto-reverted …`). Bind failures are
   tolerated (the WB mirror can never verify a bind — G8): the healing
   matrix write proceeds. A write failure reports
   `no input seen — AUTO-REVERT FAILED: …` — never silently.
   **Item 4 honesty:** in seed state (`verified: false` — only the factory
   table was ever stored, no operator has confirmed a press) the timer
   NEVER auto-writes; the banner says so: `no input seen — NOT
   auto-reverted: this profile/layer has no operator-confirmed config yet
   (the last-good seed is only the unchanged factory table)`. Every keystate
   payload carries `verify` (armed/state/banner/log) so the drawer banner
   (`keymap-verify-banner`) survives reloads; the JS arms after a
   SUCCESSFUL write using the first dirty slot + target snapshotted BEFORE
   the post-write refresh resets the baseline, and refetches keystate just
   after the server-side timer expires so the verdict lands without a
   reload.
5. **E2E replay (fake HID, capture list)** — `tests/conftest.py` gained a
   shared `FakeHIDDevice` double with a `capture` list (GET mirror all-zero,
   the live WB5092 state). `tests/test_e2e_gui_remap_flow.py` replays the
   FULL GUI flow through the controller + the REAL `StockGmmkProBackend`:
   keystate → Caps Lock cap (resolved to slot **58** via the shipped slot
   table) → target `0x59` → apply → the captured cmd-0x03 report carries
   `codes[58] == 0x59` (NOT 60 — slot 60 keeps its vendor byte) and the
   types array byte-identical to `data/default_types.bin` (NO types drift)
   → arm → one `End` keydown → VERIFIED → seed-state timeout writes
   NOTHING. "Reset to factory" applies the EMPIRICAL OVERLAY:
   `codes[58] == 0x39`, `types[58] == 0x01` — a factory reset now KEEPS the
   caps keycap alive (the G11 incident is structurally impossible).
6. **Display overlay** — the keystate factory-defaults degradation now
   shows `Slot 58 · Caps Lock (0x39) · normal` (banner, drawer tooltip and
   editor all read from the overlaid bytes; the slot dict carries
   `overlay: "empirical-caps (remap/KEYMAP_MAP.md EMPIRICAL-FIRMWARE)"`).
   Display + write-overlay ONLY — `default_keymap.bin` stays the raw vendor
   bytes, and on a REAL mirror (incl. a genuine `0xFF` at 58) the device
   bytes display untouched: the overlay fires only on the
   factory-defaults degradation path.

## Design decisions

- **`pending` vs `codes` in last-good**: the plan asked to persist every
  apply AND to auto-write only a *verified* config. Those collide for the
  first remap of a fresh pair, so the file separates them: `codes` is the
  revertable matrix (changes only on confirm), `pending` is the
  last-applied insurance. Auto-write ⇔ `verified: true`.
- **Wire fact pinned by the E2E**: the cmd-0x03 selector bytes at [2]/[3]
  carry the RAW 1-based profile/layer (1,1 for profile 1 layer 1) —
  consistent with the existing device doubles; the report asserts it.
- **Timer architecture**: `threading.Timer` daemon threads carry the armed
  object as a token — a stale timer that fires after a cancel/rearm is a
  no-op (`self._verify_armed is armed`); the 60 s default is injectable
  (`_verify_timeout`), so the tests exercise the real threads in
  milliseconds.

## Honest limitations

- The keydown logger only hears keys while the web page has focus; an
  operator who alt-tabs away mid-test gets the honest timeout path, not a
  fake verdict.
- On this board the revert write itself cannot be verified by read-back
  (shadow buffer) — it rides the SAME proven path every write uses; the
  physical keystroke remains the only ground truth, which is exactly what
  the banner tells the operator.
- `VERIFIED` means "the key now emits the requested code" — it is the
  server's matcher verdict, and the front end contains no verdict logic
  (pinned by `tests/test_verify_js.py`).
- **Skill reference paragraph NOT applied:** plan item 7 also asks for a
  G12 paragraph in `~/.hermes/skills/glorious-gmmk-linux/references/
  remap-protocol.md`. That path is outside this workspace; the file sandbox
  denies writes there and the escalation requires an approval channel that
  is not available in this environment (fails closed). The full paragraph
  text is preserved here as the ready-to-paste block at the bottom of this
  report — apply it in a session with approval, then delete this note.

## Tests (36 new, all device-free)

- `tests/test_lastgood.py` (12): atomic tmp+replace (os.replace spy),
  seed flags, apply→pending without touching `codes`, confirm promotion,
  corrupt/truncated JSON → None, validation, `$OGC_CONFIG_DIR` precedence.
- `tests/test_verify_flow.py` (16): variant-table pins (End/Numpad1, F13,
  KeyA, empty for unobservable codes), arm seeding + banner, arm
  validation, match verdicts + confirm promotion, non-matching keydown
  dropped, seed-state timeout ZERO writes, verified-state timeout exact
  call sequence `[change_profile(1,1), set_keymap_codes(1,1,bytes)]` (one
  write), bind-failure tolerated, write-failure banner, re-arm cancels the
  old timer, keystate embedding, full HTTP wiring on a real
  `ThreadingHTTPServer` (200/400/404).
- `tests/test_e2e_gui_remap_flow.py` (3): the flow above + overlay reset +
  seed-no-write on the wired stack.
- `tests/test_verify_js.py` (5): `node --check` gate, keydown-logger pins,
  arm-after-apply ordering pins, banner element/paint wiring, "server is
  the only judge" source pins.
- Six pre-existing tests updated for the deliberate overlay change
  (`reset_keymap` payload + keystate factory-defaults display at slot 58).

## Operator gate (next, on the real board — one exercise)

Remap Caps Lock → `0x68` (F13, a harmless target nothing else uses), press
the key once within 60 s → expect `VERIFIED — <key> now emits F13 (0x68)`;
then repeat and press NOTHING → expect the auto-revert banner and CapsLock
working again afterwards (manual F13→Caps restore via the drawer if the
target was left standing). Zero-risk exercise of the whole loop, exactly
once.

## Commits

1. `feat(verify): last-good snapshot + /api/keyevent + auto-revert-on-no-input`
2. `test(e2e): full GUI remap flow replay against fake HID (pins caps→58, factory-reset keeps caps)`
3. `docs(g12): run report`

## Ready-to-paste skill paragraph (target: `references/remap-protocol.md`,
## after the G10/G11 line, in the skill `glorious-gmmk-linux`)

> \- G12 landed: automated remap verification + self-healing revert. `POST
> /api/verify-remap {slot, expect:[hid codes]}` arms a 60 s server-side
> matcher fed by `POST /api/keyevent` (page keydown logger, `{code,key}`, no
> storage beyond the armed pending); explicit HID→KeyboardEvent.code variant
> table (0x59 → Numpad1 AND End — the NumLock class; unobservable codes like
> 0xA0/0x02 get NO variants → never a false VERIFIED). Every applied matrix
> persists to `~/.config/open-glorious-core/last-good-<profile>-<layer>.json`
> (atomic tmp+replace; applied codes ride in `pending`, become the revertable
> `codes` ONLY on a confirmed keystroke; seed = factory vector flagged
> `verified:false`). No input in 60 s → auto-revert via the proven path (bind
> 0x01 + ONE full-matrix 0x03 write) and the banner says so; a seed state is
> NEVER auto-written (honest banner). `reset_keymap` and the keystate
> factory-defaults display apply the EMPIRICAL overlay (codes[58]=0x39,
> types[58]=0x01) — a factory reset now KEEPS caps alive;
> `default_keymap.bin` itself stays the raw vendor bytes (overlay is display
> + write-time only). Lesson: a remap verdict = a physical keystroke within
> the armed window, nothing else.
