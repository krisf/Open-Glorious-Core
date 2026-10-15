# G12 — Automated remap verification + self-healing revert (no operator interpretation)

## Physics constraint (state it honestly in the report)

A remap's effect is observable ONLY through a physical keystroke (that press is
what a remap means). What CAN be fully automated:
- catching the bug class BEFORE any device write (structural E2E);
- reading the verdict (OS/keyboard-event code matching, not the operator's
  eyes);
- discovering failure and REPAIRING it (auto-revert to last-good matrix).

Operator's stated intent: "test key remapping without me having to be in the
loop so this doesn't get [broken] with another key remap". The capslock
incident replay under this design: write lands wrong → no familiar keypress
within 60 s → app auto-reverts to last-good → operator sees "no input seen,
auto-reverted" and their keyboard is untouched. Zero interpretation, zero
repair, one optional keystroke.

## Fix spec

1. Last-good insurance: on every successful `apply-keymap`, persist the
   per-current-(profile,layer) code matrix to
   `~/.config/open-glorious-core/last-good-<profile>-<layer>.json`
   (created only AFTER an operator-verified state exists; seed = packaged
   factory vector but flagged `verified: false` until the operator confirms
   a post-write press). Reading this file must be atomic (tmp+rename).
2. `/api/keyevent` endpoint + JS keydown logger in static/app.js: while the
   app window is focused, keydown events are POSTed with
   `{code: event.code, key: event.key}`. No storage beyond the armed
   verification pending, no third parties (localhost only).
3. Verify-armed state: after a successful write the app shows the verify
   banner "press your remapped key once (60 s)"; POST `/api/verify-remap`
   `{slot, expect:[codes...]}` starts a 60 s timer server-side. Matcher
   maps expected HID codes to KeyboardEvent.code variants (explicit list,
   incl. NumLock-dependent KP variants: 0x59 → Numpad1 AND End; F13 → F13;
   letters → Key<A> etc.). On match: banner "VERIFIED — <key> now emits
   <name>", clear armed state. On timeout: backend writes the last-good
   matrix back (single cmd-0x03 + cmd-0x01, same proven path), banner
   "no input seen — auto-reverted to last good config", log line to the
   app log.
4. The armed timer must NOT auto-write if `verified: false` seed state
   (first-ever run): only after an operator has confirmed any press for the
   profile pair (the seed is only "unchanged factory" — safe to revert to
   but flag honestly in the banner).
5. Structural E2E (the bug-class killer): conftest fake-HID double gain's a
   `capture` list; new tests replay via the controller the FULL GUI flow
   (keystate → pick cap → set target code → apply) and assert the exact
   byte lands at the exact slot in the captured report. Pin the capslock
   incident shape: "Caps Lock cap with target Keypad 1" MUST produce
   codes[58]=0x59 in the captured report (NOT 60, no types drift). The
   revert apply MUST assert the GUI's "Reset to factory" applies the
   EMPIRICAL overlay (factory bytes with codes[58]=0x39, types[58]=0x01) —
   regression-pinning the recovery recipe so a factory reset can never
   physically kill caps again.
6. Display overlay for the empirical caps cell: `/api/keystate` and the
   remap drawer must show "Slot 58 · Caps Lock (0x39) · normal" for slot 58
   (G11's DOM shows the vendor-factory text "Unassigned (0xFF) · unused" —
   misleading). Cite the EMPIRICAL-FIRMWARE note as the source; do not
   rewrite default_keymap.bin (the overlay is display + write-overlay only).
7. Docs: G12-REPORT.md + a paragraph in `references/remap-protocol.md`
   (skill) describing the self-heal contract and the one-keystroke physics
   caveat.
8. Zero device I/O during authoring; the auto-revert path is exercised in
   tests through the fake HID only. Operator gate afterwards exercises it
   live ONCE (a harmless F13 target, then manual revert).
9. Tests headless, no pip installs, no pushes, relative paths only. At most
   3 commits:
   a. `feat(verify): last-good snapshot + /api/keyevent + auto-revert-on-no-input`,
   b. `test(e2e): full GUI remap flow replay against fake HID (pins caps→58, factory-reset keeps caps)`,
   c. `docs(g12): run report`.
10. Environment: venv python, PYTHONPATH=src, cache redirection env, node
    --check every touched .js.
11. Final stdout ends with `G12_DONE tests=<n> commits=<sha1..3>`.
