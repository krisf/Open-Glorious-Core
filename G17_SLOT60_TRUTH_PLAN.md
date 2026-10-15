# G17 — Slot-60 truth: GUI reverts the G11 empirical error

## Operator-proven verdict (G13 sweep, 10-15 14:4x)

Sweep write via G16 GUI path (bind-first): F13 (0x68) on slot 58,
F14 (0x69) on slot 60, all other cells factory. Hidraw capture of the
operator's caps press emitted `00 00 69` = F14 — twice (press and
release-shape present for 60, nothing from 58). Factory restore returned
caps (`Ss yes its normal again` — operator-verified).

VERDICT: on this WB5092 wireless firmware the REAL CapsLock cell is
SLOT 60 (vendor Buttoninfo_Default was right). Slot 58 is a dead matrix
spare. The G11 "empirical" conclusion (slot 58 = caps) is RETRACTED —
it was a misattribution: the morning recovery wrote 0x39 to BOTH slots
and caps returned via slot 60; every later GUI remap failed because the
GUI aimed the remap at dead slot 58. The types-overlay finding (G15)
may still matter for genuinely dead vendor-typed cells but is NOT the
primary bug; keep it, it is harmless and E2E-proven.

## Fix spec

1. GUI grid: Caps Lock keycap targets slot 60 (data-slot 60), normal
   click/edit/write path. Slot 58 is hidden as a non-keycap spare (join
   the hidden set; the G12 slot-58 display overlay is REMOVED).
2. remap/KEYMAP_MAP.md: retract the EMPIRICAL-FIRMWARE note (slot 58 =
   caps) — replace with the sweep verdict: slot 60 = caps (G13 sweep
   F14 capture, operator-verified 10-15), slot 58 = dead spare. Remove
   any leftover claim that vendor Buttoninfo_Default is wrong.
3. Tests: un-pin every test asserting caps cap == slot 58 (G11/G12
   pins); re-pin caps cap == slot 60; hidden-set assertions gain 58;
   the G12 reset-preserves-caps test now asserts codes[60]==0x39 with
   the plain vendor factory types (no special overlay at 58).
4. Keep G15 types overlay and G16 bind-first behaviors as-is.
5. G17-REPORT.md: the sweep evidence chain (probe write -> F14 capture
   -> factory restore), retraction of G11, operator re-gate: GUI remap
   caps -> Numpad 1 must be retried by the operator and VERIFIED by the
   app listener (it should finally work end-to-end).
6. Zero device I/O in authoring; headless tests; node --check touched
   js. At most 2 commits:
   a. `fix(keymap): caps cell is slot 60 (G13 sweep F14 capture retracts G11 empirical claim)`
   b. `docs(g17): slot-60 truth report`
7. Final stdout ends with `G17_DONE tests=<n> commits=<sha1..2>`.
