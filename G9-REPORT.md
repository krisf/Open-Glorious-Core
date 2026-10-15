# G9 Run Report — operator-screenshot UI polish fix round

Plan: `G9_UI_FIX_PLAN.md` (repo root, not committed, G7/G8-plan precedent).
All work is headless: zero device I/O in every test (`tests/conftest.py` keeps
`_send_feature_report` / `_exchange_feature_report` / `_detect_stock_interface`
hard-blocked), no `QApplication`, relative paths only, `remap/` scripts,
fixtures and `src/open_glorious_core/data/` untouched, no pushes, no installs.
Python: `~/.local/share/open-glorious-core/venv/bin/python` with
`PYTHONPATH=src` and cache redirection
(`PYTHONDONTWRITEBYTECODE=1 XDG_CACHE_HOME=scratch/cache PYTHONPYCACHEPREFIX=scratch/pycache`).
Every touched `.js` checked with the pinned node
(`/home/ubuntu/.hermes/tools/node-26.7.0-linux-x64/bin/node --check`).

The plan's banner text ("Device buffer not initialized — showing factory
default", G8 honesty contract), the protocol/backend write path and
`KEYMAP_SLOT_LABELS` are unchanged; the protocol surface is byte-identical.

## Commits (plan cap: 3 — met)

1. `b18a465` — `fix(ui): slot physicality + keycap fit + profile card labels`
2. `82bdf16` — `feat(ui): searchable remap dropdown — normalized aliases,
   hex search, full list on empty query, staged-assignment wiring`
3. `(this)` — `docs(g9): run report`

## FIX A — slot physicality (`b18a465`)

The operator saw keycaps named `#103 #104 #106 #107 #111` (and `#14 #72 #92`):
the grid's `"#" + slot` label fallback fired for every rendered slot without a
`KEYMAP_SLOT_LABELS` entry. The five row-6 spares were always label-less —
`KEYMAP_MAP.md` line 26 row-6 anatomy (`LCtrl LWin LAlt ·· Space ·· RAlt Fn
RCtrl · ← ↓ →` — the `··` positions ARE slots 103/104/106/107/111) and its
lines 178–186 row-6 table (all five = `*(unassigned spare)*` `0xFF`, verified
against `data/default_keymap.bin`); 14/72/92 are the matrix artifacts of the
lines 32–42 notes table (knob area / ISO / phantom Menu).

- `app.py`: module-level `NON_KEYCAP_SLOTS = frozenset({14, 72, 92, 103, 104,
  106, 107, 111})` (comment cites the KEYMAP_MAP.md evidence + fixture bytes),
  and every `/api/keystate` slot object now stamps
  `"is_keycap": entry.slot not in NON_KEYCAP_SLOTS` — on the normal AND the
  G8 degraded path.
- `app.js` `renderKeymapGrid()`: `meta.is_keycap === false` renders an
  **invisible spacer div** (`.keycap-spacer`: `visibility:hidden`, no border,
  no background, no label, not a button → not clickable, not tabbable,
  `aria-hidden`), preserving the 15-column row alignment. The payload flag is
  the source of truth; a documented offline mirror (`KEYMAP_NON_KEYCAP`) covers
  the pre-payload paint. The `#NNN` fallback is gone — the label fallback is
  now the empty string, so raw slot numbers never render as keycap names
  anywhere (keycap count on the grid drops by exactly 8).
- `index.html` legend updated to the honest eight-slot text.

## FIX B — keycap fit (`b18a465`)

`.keycap`: fixed `height: 46px` (long names can no longer push row height),
`min-width: 0`, tightened `padding: 5px 6px`. `.keycap-label`: `0.62rem`,
single line (`white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
max-width: 100%`); `.keycap-code`: `0.55rem` with the same ellipsis. Full
names stay available via the native HTML `title` tooltip (set on the label
span and the button).

## FIX C — searchable remap dropdown (`82bdf16`)

The old `<select id="keymap-code-select" size="10">` + hidden-option filtering
rendered an EMPTY list on open (QtWebEngine ignores `<option hidden>` inside
`<optgroup>` in a multiple-size select; and with no query the code left the
list untouched) — the operator's "empty list, and `num1`/`1` found nothing".

- `keycodes.py`: bounded, group-documented `CODE_ALIASES` + `code_aliases()`
  (import hygiene test unaffected): navigation abbreviations (`prtsc`, `ins`,
  `pgup`, `pgdn`, `del`, `esc`), keypad digit families `num1`/`numpad1`/`kp1`/
  `pad1`/`p1` (…digit 0 included) + operator words (`kpplus`, `kpminus`,
  `kpenter`, …), media (`volup`, `voldown`, `mute` words), modifiers
  (`ctrl`/`lctrl`, `win`/`super`/`meta`/`gui`/`cmd` family, `altgr`), system
  (`caps`, `numlock`, `menu`), editing (`return`, `spacebar`), vendor (`fn`),
  0xFF (`none`, `blank`, `clear`). NO plain single-digit aliases — the digits
  row names `1`–`0` already prefix-match those queries (plan rule, test-
  pinned). `code_to_name` remains the single naming source of truth.
- `app.py`: catalog entries in `/api/keystate.codes` carry `aliases`.
- `app.js`: DOM-free pure helpers between `[PURE-SEARCH-START/END]` markers —
  `normalizeKeyToken` (lowercase, strip non-alphanumerics), `keyQueryHex`
  (`0x59`, `59`, `ff` → hex; single bare digits stay text so `1` hits the
  digits row), `keyEntrySearchName` (strips the ` (0xNN)` suffix so `f10` no
  longer prefix-matches F1 — the exact false positive normalization
  introduces), `keyEntryMatches` (startswith on alias OR stripped name OR hex
  code; empty query matches ALL).
- The `<select>` is replaced by a real scrollable listbox
  (`#keymap-code-list`, `role="listbox"`): EMPTY query → the FULL grouped
  catalog under sticky human headings (Letters / Digits row / F-keys /
  Modifiers / Navigation / Numpad / Media / Punctuation / Editing / System /
  International / Vendor specials) — the list is never empty on open.
  Item click stages the target and refocuses the search; ArrowDown/ArrowUp
  move the selection **while the search input keeps focus** (no typing
  regression), Enter stages; hover tracks the cursor; the staged target
  immediately updates the dirty dot, the "N unsaved changes" counter and the
  write-button label/enabled state through the existing `setBufferCode`
  staging path (device write still only behind the write button).

## FIX D — profile card labels (verified, `b18a465`)

The shipped card renderer already emits exactly `<strong>Profile N</strong>` +
`<span>Layer M</span>` (git-proven since the panel shipped): title shows only
"Profile 2", subtitle only "Layer 3", no string repeats the other part, the
ACTIVE tag + amber border stay, the 3×3 card grid is untouched. No code change
was needed; re-verified in this round.

## FIX E — write button + scrollbars (`b18a465`, list styling `82bdf16`)

- "Write N key(s) to keyboard" is **hidden** while N = 0 (`is-hidden` class,
  initial HTML included) and reads "Write 1 key to keyboard" / "Write 9 keys
  to keyboard" once visible — the `(s)` is gone.
- Dark themed webkit scrollbars (10px, amber thumb, dark track,
  `scrollbar-color` fallback for non-webkit) applied app-wide: covers the app
  scroll area (`.workspace-shell`) and the suggestion list; the G8 banner TEXT
  is untouched ("operator-approved honesty").

## Verification (all headless)

- Full suite: **186 passed** (baseline 157 → +29:
  +1 controller `is_keycap` test covering normal + degraded paths,
  +4 `keycodes` alias invariants (assignable codes, normalized + bounded,
  keypad family, API shape), +24 node-executed pure-search tests in
  `tests/test_keymap_search_js.py` — the harness extracts the
  `[PURE-SEARCH]` section verbatim, asserts it is DOM-free, and runs it under
  the pinned node binary; alias/hex/normalization/empty-query cases all
  pinned).
- `node --check src/open_glorious_core/static/app.js` → clean (only `.js`
  touched, checked after each commit's edits).
- Zero device I/O: conftest autouse blocker active in every run; no test
  opens a hidraw.
- `tests/test_keymap_layout.py` untouched and green — backend
  `KEYMAP_SLOT_LABELS` unchanged (its `{14,72,92}` unlabeled-slot pin stays
  true: `is_keycap` lives in the controller/UI only).

## Constraints

3 commits exactly (plan cap); no pushes; no pip installs; relative paths only;
`remap/powercycle_watch.log` / `remap/smoke_results.jsonl` left untracked;
protocol/backend write behavior byte-identical.
