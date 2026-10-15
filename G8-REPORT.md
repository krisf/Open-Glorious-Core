# G8 Run Report — Shadow-proofing fix round (keymap writers + /api/keystate)

Plan: `G8_SHADOW_PLAN.md` (repo root, not committed, G7-plan precedent).
All work is headless: zero device I/O in every test (`tests/conftest.py` keeps
`_send_feature_report` / `_exchange_feature_report` / `_detect_stock_interface`
hard-blocked; the new device models are pure in-memory doubles), no
`QApplication`, relative paths only, `remap/` scripts, fixtures and
`src/open_glorious_core/data/` untouched, no pushes, no installs.

## Live-device evidence (operator gate, 2026-10-15, real GMMK Pro WB 320F:5092)

G7's GUI gate FAILED at step 1/3:

1. `GET /api/keystate` → `ok:false` — *"Config mirror read failed: device
   response carries no parsable profile/layer."* The GUI showed **no
   remapping panel** on this board.
2. Raw probe: the GET (cmd 0x81) mirror returns a `00 00 00 00` head and
   **zero nonzero bytes in `[4:]`** — TRUE EVEN IMMEDIATELY after a successful
   cmd 0x01 bind (tested at 0 s / +1 s / +4 s: identical).
   `_parse_profile_layer_response` can never find valid selectors (all bytes
   `0x00`): the config-mirror shadow buffer **never populates** on this
   wireless board post-factory-reset.
3. `POST switch-profile (1,1)` → `ok:true` "read-back confirmed" — that
   confirmation is the fallback path (parse None → keep last known), not a
   real read.
4. G6 verdict stands: cmd 0x03 matrix writes DO land (PrtSc→F13 physically
   verified by keypress). **Only the read-back is dead.**

Consequence: the G7 strict active-slot guard made every keymap writer refuse
(`_read_config_mirror` raises) and every keystate load error out — on the very
board whose writes work.

## The fix (shipped semantics)

Blank mirror (`_parse_profile_layer_response` → None) is a first-class
shadow-only state, never a refusal:

- **backend.py** (`5e2a9f4`)
  - `_get_config_mirror()` — raw GET + device-presence + 1–3 selector
    validation, no interpretation.
  - `_read_config_mirror()` — strict read kept for the READ path
    (`read_keymap`); its "no parsable profile/layer" text IS the blank
    signature callers detect.
  - `_read_config_mirror_shadow_tolerant()` — the writers' pre-read guard:
    parsable pair → OLD refusal (exact text, refuse before anything is sent);
    blank mirror → target profile/layer taken as given, no refusal.
    `set_keymap_codes` / `set_keymap` / `reset_keymap` all use it.
  - `_write_keymap_matrix()` — send ONCE, ONE read-back: fully blank
    (parse-None) read-back → shadow (caveat message, no retry, no second
    write, no extra GET); blank all-zero table → shadow (G7 branch kept);
    byte-match → verified; any other difference → the exact G7 hard-error
    text, unchanged.
- **app.py** (`9002d68`) — `GET /api/keystate` degrades on the blank
  signature instead of erroring: `ok:true`, `keymap.source =
  "factory-defaults"`, `is_dirty_source: true`, slots from
  `backend.default_keymap_table()`, and the plan-pinned honest message:
  *"Config mirror is blank (shadow buffer never populates on this wireless
  board post-factory-reset); showing packaged factory defaults. Writes still
  land and physical key test is the only verification."*
  Non-blank failures (device gone, permission, …) still return `ok:false`.
  No UI change was needed: `static/app.js` keys the banner off
  `is_dirty_source` only.

## Commits (3)

| # | Commit | Deliverable |
|---|--------|-------------|
| 1 | `5e2a9f4` | backend shadow-tolerant guard + read-back; `FullyBlankDevice` (live-probe model: all-zero GET, header included) tests: all three writers SEND (exactly one `_send_feature_report` each) and return the caveat; parsable-pair refusal kept |
| 2 | `9002d68` | controller `/api/keystate` graceful degradation + honest message; tests: blank mirror → ok:true / factory-defaults / 120 slots / slot 0 = 0x29; device-gone → ok:false |
| 3 | (this commit) | `G8-REPORT.md` (this file) |

## Test results

- Command (repo root):
  `env PYTHONDONTWRITEBYTECODE=1 XDG_CACHE_HOME=scratch/cache PYTHONPYCACHEPREFIX=scratch/pycache PYTHONPATH=src ~/.local/share/open-glorious-core/venv/bin/python -m pytest -q`
- Result: **157 passed** in ~0.3 s (baseline 151 → +6: 4 backend shadow-tolerance,
  2 controller degradation).
- G7 tests pinning refusal semantics: the only refusal-on-blank behavior in
  the suite lives in `tests/test_smoke_setkeymap.py`, which pins the
  *operator tool* `remap/smoke_setkeymap.py` (selectors come FROM the mirror
  there by design, and `remap/` is out of scope) — it stays green untouched.
  All G7 backend/controller expectations (verified wordings, exact hard-error
  text, shadow banners, IO-lock) pass unchanged.

## Node check

- `/home/ubuntu/.hermes/tools/node-26.7.0-linux-x64/bin/node --check src/open_glorious_core/static/app.js` → **OK** (no UI changes this round, as expected).

## Operator gate rerun guidance

The G7 gate steps 1–6 are unchanged in shape; expected deltas on the WB board:
step 1 keystate now answers `ok:true` with the factory-defaults panel and the
blank-mirror message; writes answer the shadow caveat; the PHYSICAL KEY TEST
remains the only true verification of any write.
