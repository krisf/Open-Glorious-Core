# G14 Run Report — keymap panel scale: full-width grid, overlay drawer, clamped keycaps

Plan: `G14_GRID_SCALE_PLAN.md` (repo root, committed with this report,
G7–G12 precedent). All work is headless: zero device I/O in every test
(`tests/conftest.py` keeps `_send_feature_report` /
`_exchange_feature_report` / `_detect_stock_interface` hard-blocked), no
`QApplication`, relative paths only, `remap/` scripts, fixtures and
`src/open_glorious_core/data/` untouched, no pip installs, no pushes.
Python: `~/.local/share/open-glorious-core/venv/bin/python` with
`PYTHONPATH=src` and cache redirection
(`PYTHONDONTWRITEBYTECODE=1 XDG_CACHE_HOME=scratch/cache PYTHONPYCACHEPREFIX=scratch/pycache`).
Every touched `.js` checked with node `--check` (`static/app.js`,
`static/keymap-slots.js`): clean. Suite: **263 passed** (the untouched G12
baseline — all green, no test edits needed).

No backend change. The keycap data-slot semantics (G10/G11 table binding,
the `[PURE-DRAWER]` block, `keycapSlotIsUnassigned`, the G12 keydown
logger) are untouched — this is CSS/markup plus two small front-end hooks.

## Operator feedback pinned by the plan (screenshot 2026-10-15 11-32-12)

Content measured ~1450 px inside the maximized window while the grid
rendered ~903 px left-aligned: the permanent `minmax(0,1fr) 300px` rail
reserved ~20% of every row for a drawer that was hidden, caps stayed at a
static 46 px/0.62 rem and truncated to "Pr…"/"Mi…", the app booted
scrolled mid-page, and the legend wrapped at grid width.

## Fixes (spec items 1–5)

1. **Grid owns the width; drawer is an overlay.** `.keymap-body` is a
   single column (`grid-template-columns: minmax(0, 1fr)`). At ≥960 px the
   editor becomes `position: absolute; top: 0; right: 0; width: 330px`
   inside the (now `position: relative`) keymap-body, with panel border,
   `box-shadow`, `z-index: 6` and `max-height: 100%; overflow-y: auto`
   (internal scroll; it reserves ZERO layout width). It anchors to
   keymap-body — below the panel header — so it never covers the
   dirty-counter / reload / self-test buttons. Visibility keeps the exact
   G9 state (the `is-hidden` toggle in `renderKeymapEditor`: a selected,
   editable cap). Dismiss: the new head close button
   (`#keymap-editor-close` → `closeKeymapDrawer()`) and **Escape**
   (document keydown; deselects, drawer hides). Below 960 px the drawer
   stays the normal stacked block (G9 simple path, untouched).
2. **Clamped keycap scaling.** `:root { --keycap-h: clamp(46px, 3.6vw,
   62px) }`; `.keycap` height/min-height and `.keycap-spacer` min-height
   follow the var, so every row scales together. Label font
   `clamp(0.7rem, 0.95vw, 0.95rem)`, code line `clamp(0.6rem, 0.8vw,
   0.8rem)`. The `repeat(15, minmax(0,1fr))` template stays. Row 1's
   equal right edge needs no markup change and none was made: the slot
   table's row 1 is 15 cells whose last cell (slot 14, matrix artifact,
   `keys: [.., null]`) already renders as the invisible
   `.keycap-spacer` trailing cell — every row ends at the same 15th-column
   right edge (G9 FIX A spacers, kept verbatim, now var-sized).
3. **Scroll-to-top on boot.** `scrollAppToTop()` pins `window.scrollTo(0,
   0)` (per spec) **and** `.workspace-shell.scrollTop = 0` — under the
   fixed `overflow: hidden` `.app-shell`, that element is the app's real
   scroll box, so restoring mid-page is exactly what clipped the swatch
   row. Runs at boot, on `requestAnimationFrame` (after layout, before the
   user sees the restored position), and on `window load`.
4. **Legend full width.** `.keymap-legend` sits in keymap-body's single
   column, so it now spans the full keymap width; comment pins the reason
   (it no longer wraps at a 300 px-rail grid width).
5. **Device-card subtitle.** `.device-card span` gets `white-space:
   normal; overflow-wrap: anywhere` — the "hidraw • backend" separators
   stay readable via natural wrap; no other change.

## Front-end hooks added (app.js, 31 lines)

* `km.editorClose` element + `closeKeymapDrawer()` (deselect + repaint),
  wired to the close button and to a document `keydown` Escape listener.
* `scrollAppToTop()` boot wiring (see item 3).

Existing static pins verified against the new code, not worked around:
`renderKeymapEditor` body pins, `runMatrixSelfTest(` count, the G12
keydown-logger split-pin (the Escape listener registers on the same
event string BEFORE the logger; the split remainder still contains
`postKeyEvent(`), and the `[PURE-DRAWER]` node-eval block (byte-identical).

## Files touched

* `src/open_glorious_core/static/styles.css` (+79/−8)
* `src/open_glorious_core/static/index.html` (+2, close button)
* `src/open_glorious_core/static/app.js` (+31)

## Verification

* `node --check static/app.js` / `static/keymap-slots.js`: clean.
* Full suite: **263 passed** (`python -m pytest -q`, venv recipe above).
* CSS brace balance + no remaining `minmax(0, 1fr) 300px` rule: checked.
* Visual verdict is the operator gate (screenshot), per plan item 6;
  shipped markup is headless-safe.

## Commits

1. `feat(ui): keymap grid full-width + drawer overlay + scaled keycaps`
2. `docs(g14): scale-fix report`
