# G16 Run Report — bind-first matrix writes: GUI writes now target the profile they name

Plan: `G16_BIND_FIRST_PLAN.md` (repo root, committed with this report,
G7–G15 precedent). All work is headless: zero device I/O in every test
(`tests/conftest.py` keeps `_send_feature_report` /
`_exchange_feature_report` / `_detect_stock_interface` hard-blocked, and an
autouse guard redirects the config dir via `$OGC_CONFIG_DIR` into the test
tmp_path), no `QApplication`, relative paths only, `remap/` scripts and
`src/open_glorious_core/data/` untouched, no pip installs, no pushes.
Python: `~/.local/share/open-glorious-core/venv/bin/python` with
`PYTHONPATH=src` and cache redirection (`PYTHONDONTWRITEBYTECODE=1
XDG_CACHE_HOME=scratch/cache PYTHONPYCACHEPREFIX=scratch/pycache`).
The touched `.js` (`static/app.js`) is node `--check` clean (pinned by
`tests/test_verify_js.py`). Suite: **267 passed** (baseline 264 + 2 new E2E
wire tests + 1 new JS structural pin, plus extended pins inside the existing
E2E apply/reset tests).

## Root cause (root cause #3 — the one that made a good write invisible)

G11 fixed the slot (58). G15 fixed the type byte (activation). The operator's
13:07 write had both right — and still produced **NO CHANGE**, because of a
third firmware/board truth the write path ignored:

**Keymap writes are profile-addressed, and on this wireless board the GUI's
profile/layer is a SOFTWARE COUNTER with no readback. A matrix write aimed at
a stale profile is physically indistinguishable from no write at all.**

The GET mirror on the live WB (320F:5092) is blank (G8/G12: the shadow buffer
never populates), so `StockGmmkProBackend.profile/layer` can only ever be
what `change_profile` last *believed*. Every FN+CTRL+↑ press, every
re-pair, every sleep can move the board's real active profile away from that
number. From 13:07 on, `apply-keymap` sent cmd 0x03 (1,1) — into a profile
the operator was not typing on.

## Operator evidence chain (device artifacts cited, not touched)

1. **13:07 GUI write, NO CHANGE.** The last-good store shows the applied
   vector was exactly the requested remap (caps→Numpad 1, slot 58, code
   0x59) — the GUI sent what was asked; the board ignored it.
2. **14:20 multi-interface hidraw capture.** Operator typed `zz hello`:
   every keystroke visible on hidraw1. The two CapsLock presses emitted
   NOTHING on hidraw1/2/3. The caps cell is dead **in the active profile
   the operator is actually typing on** — the profile the GUI's write never
   targeted.
3. **RGB liveness probe.** Effect 2 → 7 visibly blinked. The channel works;
   the write channel is not dead. Decisive asymmetry: **cosmetic effects
   are GLOBAL, keymap writes are profile-addressed** — a blinking RGB
   proves the wire, never the profile. RGB can never prove a
   profile-addressed write landed.
4. **Bind-first recipe authority.** Both historical states that fixed caps
   (G11 recovery, the 14:20 recovery) used the same recipe: cmd 0x01
   (profile 1, layer 1) FIRST, then cmd 0x03 matrix. Every GUI apply in
   G7–G15 sent the bare 0x03. The recipe is the empirical proof; the GUI
   now mirrors it verbatim.

## The fix (one decisive change + safety)

1. **Bind-first writes** (`app.py`). New
   `OpenGloriousCoreWebController._bind_first_matrix_write(label, profile,
   layer, write)`: one log line per write
   (`apply-keymap: bind (p,l) -> matrix 120 slots`),
   `backend.change_profile(profile, layer)` (cmd 0x01) IMMEDIATELY before
   the single matrix write (cmd 0x03). `apply-keymap` and `reset-keymap`
   both route through it inside one `_backend_io` lock, so bind+write are
   atomic against other requests. Exactly one cmd 0x03 per action, no blind
   retries.
2. **Recovery-recipe safety, mirrored 1:1 from `_auto_revert_write`.** The
   bind is safe/reversible: it only selects the write target and visibly
   switches the board's active profile — the operator can do the same with
   FN+CTRL+↑. A blank mirror can never CONFIRM a bind (G8), so a failed
   bind-verification is logged (`... bind unverified (...) — continuing
   with the matrix write`) and the healing write proceeds. The bind can
   therefore never turn a would-be-successful write into an error.
3. **Drift-proof drawer banner.** After every successful
   apply-keymap/reset-keymap the server attaches `keymap_write_banner`
   (`KEYMAP_BIND_WRITE_BANNER`, app.py) and the UI paints it into
   `keymap-banner`: *"Wrote profile p layer l — if your key still
   misbehaves, the board is on a different profile: cycle profiles with
   FN+CTRL+↑ (or press Write again; every write now targets this exact
   profile)."* The UI only paints the server's sentence — pinned by
   `tests/test_verify_js.py` (client contains no `FN+CTRL` text), same
   server-judges rule as G12.
4. **E2E wire pins** (`tests/test_e2e_gui_remap_flow.py`). `_cmd_seq(device)`
   reads the captured reports in send order. Pinned for BOTH actions:
   `[(0x01, p, l), (0x03, p, l)]` — cmd 0x01 immediately before cmd 0x03,
   same pair, exactly one 0x03, the per-write log line present, and the
   banner sentence verbatim (em dash, ↑ included). New drift test: counter
   at (2,2) → bind (2,2) then matrix (2,2), selectors follow the bind, the
   G15 types overlay (`types[58]=0x01`) still rides the SAME matrix, and
   the banner names 2/2. New tolerance test: a bind the blank mirror
   cannot confirm is logged, never fatal — the single 0x03 goes out
   immediately after.

## What this does NOT change

- The G15 effective-types overlay, the G12 empirical caps overlay,
  last-good/auto-revert, verify matcher: untouched, still pinned.
- The auto-revert path already bound first (`_auto_revert_write`,
  cmd 0x01 then 0x03) — the recipe this commit promotes to every write.
- `switch-profile` (explicit user action): unchanged.
- QmkHidBackend: no stock keymap actions exist there; behavior unchanged.

## Files touched

- `src/open_glorious_core/app.py` — `KEYMAP_BIND_WRITE_BANNER`,
  `_bind_first_matrix_write`, both write actions routed through it,
  `keymap_write_banner` on the success payload.
- `src/open_glorious_core/static/app.js` — `paintKeymapWriteBanner` + its
  two call sites (after `fetchKeystate()`, which resets the banner).
- `tests/test_e2e_gui_remap_flow.py` — ordering/log/banner pins for apply
  and reset; drift + unverified-bind tests.
- `tests/test_verify_js.py` — drawer-banner paint pins; server-owns-the-
  sentence pin.

## Operator re-gate (what to test on the board)

1. Rebuild/relaunch the app. Open the keymap drawer.
2. Press **FN+CTRL+↑** once so the board is on a profile of your choice
   (the app does not know which — on this wireless board it cannot).
3. Remap a key (e.g. caps → Numpad 1), press **Write**.
4. Read the drawer banner: it names the exact `profile p layer l` the
   write targeted. **If your key still misbehaves, the banner is the
   instruction: cycle profiles with FN+CTRL+↑ until the remapped key
   behaves — the write is on one of them.** (Or press Write again while
   the app shows the profile you want; every write now targets that exact
   profile, bind-first, the same recipe that fixed caps both times.)
5. Verify flow unchanged: the app arms for your press, auto-revert stays
   armed. Report which profile the banner named and what the key did.
