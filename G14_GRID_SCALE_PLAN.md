# G14 — Keymap panel scale: grid spans full width, drawer becomes an overlay

## Operator feedback (screenshot 2026-10-15 11-32-12, paraphrased facts)

- "fix the scaling width of the key map, you have unused space" — the panel
  content measures ~1450 px inside the maximized window; the grid renders
  ~903 px left-aligned, leaving a dead zone to its right.
- Root causes pinned in the CSS:
  1. `.keymap-body` (min-width:960px media) = `grid-template-columns:
     minmax(0,1fr) 300px` — a permanent 300 px right rail for the remap
     editor even while NOTHING is selected. ~20% of the row is always
     reserved.
  2. `.keycap` height is a static 46px (G9 FIX B) with 0.7-rem labels —
     caps stay small even when columns widen; labels truncate to
     ellipses ("Pr…", "Mi…") at useful widths.
  3. The app restores scroll mid-page on launch (swatch row clipped by the
     title bar; scrollbar shows the viewport starts scrolled).
  4. `.keymap-legend` footnote sits inside the grid column, so it wraps at
     grid width — should use the panel width.

## Fix spec

1. `.keymap-body` = single column (`grid-template-columns: minmax(0,1fr)`);
   the remap editor drawer becomes a right-edge OVERLAY: fixed/absolute
   panel at the right side of the keymap panel (width ~330px, above the
   grid, panel z-index, border + shadow, non-blocking scroll), visible ONLY
   when a cap is   selected (same state G9 used), dismissible with Escape
   and a close button; it must never reserve layout width. Keep the G9
   mobile/simple path: at <960px the drawer is a normal block.
2. Scaling: keycap dimensions from CSS vars:
   `--keycap-h: clamp(46px, 3.6vw, 62px)`, label font
   `clamp(0.7rem, 0.95vw, 0.95rem)`, code line `clamp(0.6rem, 0.8vw,
   0.8rem)`. The 15-column `repeat(15, minmax(0,1fr))` template stays
   (rows already span full width once the rail is gone). Row 1 keeps its
   natural 14-cell width (physical anatomy, ends one column short — NOT a
   defect; keep column alignment by letting row 1 cells span the same
   15-col template with an invisible trailing cell so EVERY row ends at the
   same right edge).
3. `window.scrollTo(0, 0)` on app boot (after layout, before user paint).
4. `.keymap-legend` spans the full keymap-body width.
5. Device-card subtitle: separators stay readable — minor: allow natural
   wrap, no other changes.
6. NO backend changes, NO device I/O, keycap data-slot semantics untouched
   (G10/G11 bindings stay). Tests: existing 227 suites must stay green;
   add node-level DOM checks? Not required — the operator gate (screenshot)
   is the visual verdict; ship headless-safe markup changes only.
7. At most 2 commits:
   a. `feat(ui): keymap grid full-width + drawer overlay + scaled keycaps`,
   b. `docs(g14): scale-fix report`.
8. node --check static/app.js. Final stdout ends with
   `G14_DONE tests=<n> commits=<sha1..2>`.
