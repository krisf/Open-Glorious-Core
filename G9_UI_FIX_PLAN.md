# G9 — UI polish fix round (operator eyeball findings)

G7+G8 shipped and the GUI write path is LIVE-PERFECT (operator gate passed).
Pure UI/polish defects remain from the operator's screenshot review of the
running panel. NO protocol/backend behavior changes. NO device writes needed.

## Findings from real screenshot (operator, verbatim evidence)

1. Keycap labels overflow and collide: rendered mashed strings
   "Minus Equal BackspDelete", "LBrackRBrackBackslPageUp", "SemicolonQuote",
   "Comma Period Slash Right Shift", "Grave1"; main labels mid-word cut
   ("Backsp", "LBrack", "Backsl"); secondary labels pervasive bare "…".
2. Remap-to search dead: query "num1" and query "1" show zero results; the
   dropdown list is completely EMPTY with an empty query too.
3. Five keycaps show raw nonsense names `#103 #104 #106 #107 #111` (secondary
   "–"). Data says these slots are `*(unassigned spare)*` positions
   (remap/KEYMAP_MAP.md lines 175-187) with factory code 0xFF — verified by
   operator against src/open_glorious_core/data/default_keymap.bin
   (103/104/106/107/111 = 0xFF). They are the physical GAPS of the bottom row
   (`LCtrl LWin LAlt ·· Space ·· RAlt Fn RCtrl · ← ↓ →` per map line 26) —
   same class as slots 14/72/92 (knob area / R-row artifacts). NO physical
   keycap exists there.
4. Profile cards repeat the profile name in title AND subtitle ("Profile 2"
   ×3 with "Layer N" subtitle) — redundant crowding.

## Fix spec

### FIX A — slot-physicality data (`src/open_glorious_core/app.py` controller)
- The slot cat alog in `/api/keystate` gains a per-slot boolean `is_keycap`.
  Derivation, single source of truth, no invented names:
  - define module-level `NON_KEYCAP_SLOTS = {14, 72, 92, 103, 104, 106, 107, 111}`
    — derived from remap/KEYMAP_MAP.md annotations (?14 knob area, ?72, ?92,
    and the five row-6 `··` spacer positions) with a comment citing the map
    lines and the 0xFF fixture evidence;
  - controller stamps `is_keycap = slot not in NON_KEYCAP_SLOTS` on every
    slot object it builds.
- UI (`static/app.js`): for `is_keycap === false` render an INVISIBLE spacer
  div (no border, no background, no label, not clickable/tabbable) so the
  15-column row alignment is preserved. The #NNN names must never render
  anywhere. Keycap count on the grid visibly drops by exactly these 8.

### FIX B — keycap visual fit (`static/styles.css`)
- Main label: single line, `white-space:nowrap; overflow:hidden;
  text-overflow:ellipsis`, font-size ~0.62rem, max-width 100%, full name on
  HTML `title` (native tooltip).
- Secondary label: ~0.55rem, same ellipsis treatment.
- Keycap: uniform min-width per 1u cell, consistent height (do NOT let long
  names push row height), padding tightened so 15 columns fit the panel width
  at the app's default window size.
- The editor drawer, cards and buttons already look right — leave them.

### FIX C — search (`static/app.js` + `src/open_glorious_core/keycodes.py`)
- REGISTRY gains per-entry `aliases` (bounded set, domain-derived strings,
  NOT new usage names): keypad keys get the obvious short aliases
  (`num1`,`numpad1`,`kp1`,`p1`,`1`? — NO: do not alias plain single digits; a
 ída "1" already prefix-matches the digits-row name "1 ..."), F-keys keep
  `f1`-style, arrows/media consume their obvious words; document each alias
  group in a comment. Every published alias string must lowercase+strip
  punctuation BEFORE matching — normalization must be: query becomes
  lowercase stripped of non-alphanumerics; each alias likewise; match =
  startswith on alias OR on normalized name.
- Search box behavior:
  - EMPTY query: populate the list with ALL assignable entries grouped under
    headings (Letters, Digits row, F-keys, Modifiers, Navigation, Numpad,
    Media, Vendor specials). The list must never be empty-on-open.
  - Query matches hex too: "0x59" or "59" find "Keypad 1 and End (0x59)".
  - Case-insensitive, includes the normalized aliases above.
  - Result item click → stages the assignment on the selected key: dirty dot,
    "N unsaved changes" counter, "Write N keys to keyboard" button label and
    enabled state all update immediately.
- Search input keeps focus while arrow-down/up moves selection, Enter stages
  (do not regress typing).

### FIX D — profile card labels (`static/index.html`/`app.js` render logic)
- Card title shows only "Profile 2"; subtitle only "Layer 3". No string
  repeats the other part. The ACTIVE tag/amber border stays on the active
  pair. (Card COUNT stays 3×3 — that is the stock GMMK Pro firmware layout;
  operator sanity-check confirms.)

### FIX E — small scrub
- The "Write N key(s) to keyboard" button is hidden until N > 0 (its label
  when visible: "Write N key to keyboard" / plural "N keys", correct grammar,
  singular never shows "(s)").
- Dark themed webkit scrollbars for the app scroll area AND the suggestion
  list (`::-webkit-scrollbar*` styles) so no native light scrollbars peek
  in dark panels.
- Everything else visual. NOT the banner text (operator-approved honesty).

## Constraints
- Same environment as before: venv
  `~/.local/share/open-glorious-core/venv/bin/python`, `PYTHONPATH=src`,
  headless tests, cache redirection env, node at
  `/home/ubuntu/.hermes/tools/node-26.7.0-linux-x64/bin/node` — run
  `node --check` on every touched .js.
- Tests: update `tests/test_controller_keymap.py` for the `is_keycap` flag
  (assert NON_KEYCAP_SLOTS are false, spot-check 100/105/109/112 are true) +
  any pure-logic tests extractable from app.js (if any pure JS helpers are
  factored, keep them importable-free of DOM).
- ZERO device I/O. No pip installs. No pushes. Relative paths only.
- At most 3 commits:
  a. `fix(ui): slot physicality + keycap fit + profile card labels`,
  b. `feat(ui): searchable remap dropdown — normalized aliases, hex search,
     full list on empty query, staged-assignment wiring`,
  c. `docs(g9): run report` (G9-REPORT.md).
- Final stdout ends with `G9_DONE tests=<n> commits=<sha1..3>`.
