# G16 — Bind-first keymap writes + drift-proof GUI

## Operator-confirmed chain (10-15, 13:00–14:20)

- 13:07 GUI write (caps→Numpad 1, slot 58, code 0x59) produced NO CHANGE.
- 14:20 multi-interface hidraw capture: operator typed zz hello — all
  keystrokes visible on hidraw1; CapsLock presses (twice) emitted
  NOTHING on any of hidraw1/2/3. Caps cell is dead in the active
  profile the operator is actually typing on.
- RGB liveness probe (effect 2 → 7) DID visibly blink — the channel
  works; cosmetic effects are GLOBAL but keymap writes are
  profile-addressed. RGB cannot prove a profile-addressed write.
- Prior states that fixed caps (both times) used BIND-FIRST:
  cmd 0x01 (profile 1, layer 1) then cmd 0x03 matrix. The GUI's
  apply-keymap (all of G7–G15) writes the matrix WITHOUT binding first.
- Board truth: GET mirror is blank on this wireless board (no readback),
  so the GUI's backend profile/layer attributes are a SOFTWARE COUNTER
  that can drift from the real active profile whenever the operator uses
  FN+CTRL+↑ or the board re-pairs/sleeps. A GUI write that goes to
  profile 1 while the operator types on profile 2 is indistinguishable
  from no write — this is exactly the observed NO CHANGE.

## Fix spec (one decisive change + safety)

1. apply-keymap and reset-keymap: call backend.change_profile(profile,
   layer) IMMEDIATELY BEFORE every matrix write (bind-first, mirrors
   the proven recovery recipes). Log a single line per write:
   `apply-keymap: bind (p,l) -> matrix 120 slots`. No blind retries,
   still exactly one cmd 0x03 per write.
2. The bind itself is safe/reversible (it only selects the target slot
   for the subsequent write and visibly switches the board's active
   profile — which the operator can also do with FN+CTRL+↑).
3. UI: after any successful apply-keymap/reset-keymap, the drawer
   banner MUST state: `Wrote profile <p> layer <l> — if your key still
   misbehaves, the board is on a different profile: cycle profiles with
   FN+CTRL+↑ (or press Write again; every write now targets this
   exact profile).`
4. E2E (fake HID): bind-first ordering pinned — captured command
   sequence must show cmd 0x01 (p,l) immediately before cmd 0x03
   (p,l) for both apply-keymap and reset-keymap; still one 0x03 per
   action; the G15 types overlay must still ride the same matrix.
5. G16-REPORT.md: operator evidence chain (13:07 NO CHANGE, 14:20
   dead-cell capture, RGB-global vs keymap-profile-addressed argument,
   bind-first recipe authority), operator re-gate instructions.
6. Zero device I/O in authoring; headless tests; node --check touched js.
   At most 2 commits:
   a. `fix(keymap): bind-first matrix writes (profile drift kills GUI remaps on wireless board)`
   b. `docs(g16): bind-first report`
7. Final stdout ends with `G16_DONE tests=<n> commits=<sha1..2>`.
