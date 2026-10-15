# G8 — Shadow-proofing fix round (operator gate findings)

G7 built the GUI (commits 87fee82..6a1ce5d). Operator gate FAILED at step 1/3
with live-device evidence from the real GMMK Pro WB (320F:5092) on 2026-10-15:

- `GET /api/keystate` → `ok:false` "Config mirror read failed: device response
  carries no parsable profile/layer." — the GUI shows NO remapping panel on
  this board.
- Raw probe: GET (cmd 0x81) returns `00 00 00 00` head, **zero nonzero bytes
  in [4:]** — and this is TRUE EVEN IMMEDIATELY after a successful cmd 0x01
  bind (tested at 0 s / +1 s / +4 s: identical). `_parse_profile_layer_response`
  can never find valid selectors (all bytes 0x00). The config-mirror shadow
  buffer NEVER populates on this wireless board post-factory-reset.
- `POST switch-profile (1,1)` via the API → `ok:true` "read-back confirmed" —
  confirmation there is the fallback path (parse None → keep last known), not
  a real read.
- G6 verdict stands: cmd 0x03 matrix writes DO land (PrtSc→F13 was physically
  verified by keypress). Only the READ-BACK is dead.

## Fix spec (smallest correct change set)

1. `backend.py` — `set_keymap_codes`, `set_keymap`, `reset_keymap`, and
   `_write_keymap_matrix`: make the shadow case first-class.
   - Replace the strict `_read_config_mirror(profile, layer)` active-slot
     guard pre-read in these three writers with a shadow-tolerant variant:
     read the mirror once; if `_parse_profile_layer_response` yields the
     expected (profile, layer) pair, enforce the OLD behavior (refuse when
     the device reports a different active pair). If the mirror is blank
     (parse → None), DO NOT refuse: treat as the shadow-only state, target
     profile/layer as given, and skip any read-back comparison (already the
     G7 behavior in `_write_keymap_matrix` via the all-zero table check —
     keep the exact message text pinned by tests).
   - `_write_keymap_matrix`: keep the G7 shadow branch, but in the shadow
     case do not waste the GET after the write either — send the matrix
     ONCE, attempt one read-back, and if the read-back is blank/unparsable,
     that IS the shadow path (honest caveat message, no retry, no second
     write). If the read-back is parsable and mismatches → the exact
     hard-error text stays.
2. `app.py` — `/api/keystate` must degrade gracefully instead of erroring:
   - Try `backend.read_keymap(profile, layer)`; on `BackendError` when the
     mirror is blank (detect via the parse-None signature: reuse
     `backend._read_config_mirror` + `_parse_profile_layer_response` or catch
     the "no parsable profile/layer" BackendError message), serve
     `keymap.source = "factory-defaults"`, `is_dirty_source: true`, slots
     built from `backend.default_keymap_table()`, and keep
     `ok: true` with an honest `message`:
     `Config mirror is blank (shadow buffer never populates on this wireless board post-factory-reset); showing packaged factory defaults. Writes still land and physical key test is the only verification.`
   - Non-blank failures (device gone etc.) still return ok:false.
3. Tests:
   - `tests/test_backend_keymap_shadow.py`: add cases where
     `_exchange_feature_report` (or the fixture double) returns all-zero
     buffers: `set_keymap_codes` must SEND (not refuse) and return the
     caveat message; `reset_keymap` likewise. Assert exactly one
     `_send_feature_report` call per operation.
   - `tests/test_controller_keymap.py`: keystate on blank mirror →
     ok:true, source factory-defaults, is_dirty_source true, 120 slots,
     slot 0 = 0x29; keystate on device-gone → ok:false.
   - Update any G7 tests that pinned the refusal semantics — they described
     the pre-fix behavior; the new semantics are: blank mirror = target as
     given + send once + honest caveat.
4. Tests MUST stay headless (conftest device double only), no QApplication.
5. At most 3 commits:
   a. backend shadow-tolerant guard/read-back;
   b. controller keystate graceful degradation + honest message;
   c. G8-REPORT.md documenting the live-device findings above and the fix.
6. Environment: same venv (`~/.local/share/open-glorious-core/venv/bin/python`),
   `PYTHONPATH=src`, cache redirection
   (`XDG_CACHE_HOME=scratch/cache PYTHONPYCACHEPREFIX=scratch/pycache
   PYTHONDONTWRITEBYTECODE=1`), pytest from repo root, node at
   `/home/ubuntu/.hermes/tools/node-26.7.0-linux-x64/bin/node` (`node --check
   src/open_glorious_core/static/app.js` must stay clean — no UI changes
   expected in this round).
7. Final stdout ends with `G8_DONE tests=<n> commits=<sha1..3>`.
8. HARD RULES unchanged: zero device I/O, no pip installs, no pushes,
   relative paths only, do not touch remap/ scripts, fixtures, or
   src/open_glorious_core/data/.
