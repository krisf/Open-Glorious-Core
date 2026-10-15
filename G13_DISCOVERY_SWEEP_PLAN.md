# G13 — Discovery sweep: probe the 12 unidentified matrix cells via F13-F24

## Operator-originated design (their idea, refined)

Operator: "could you not try a bold plan like wipe all assignments then
assign them what they should be then i keep using the keyboard and discover
whats [broken]". As stated that is a byte-identical rewrite of the current
good state — uninformative. The informative sweep writes DELIBERATELY WRONG
codes into the UNIDENTIFIED cells so ordinary typing reveals whether the
firmware binds real keys to them (the caps/slot-58 class). The operator's
normal usage IS the test loop — no dedicated verification paragraph needed.

## Probe design (one write, all facts pinned)

- Probe layout = CURRENT verified-good matrix (vendor factory + empirical
  caps overlay: codes[58]=0x39 types[58]=0x01) EXCEPT the 12 unidentified
  cells get unique codes F13..F24 (0x68..0x73):
    slots: 14, 55, 56, 57, 59, 60, 72, 92, 103, 104, 106, 107, 111
  — that is THIRTEEN cells (55..60 minus the verified 58 = 55,56,57,59,60
  = 5, plus 14, 72, 92, 103, 104, 106, 107, 111 = 8) = THIRTEEN probes
  (F13..F24 is only 12 codes — extend with two inert buddies 0x64
  (Non-US#) and 0x65 (Keyboard ƒ) if needed; DSH MUST assert the count
  matches the code list length and refuse to write on mismatch).
- Types for probed cells = 0x01 (normal key) so the firmware accepts them
  as regular typed codes.
- NOTHING ELSE in the matrix changes (letters stay true; operator types
  normally through the whole probe).
- SAFETY: F13..F24 emit NO desktop action; they DO appear verbatim in the
  G12 keydown logger (event.code F13..F24) even with NumLock/shift state
  irrelevant. A real keyboard's F21 etc. never fire accidentally.

## Flow (operator gate = one approval + passive typing)

1. Structural E2E FIRST (fake HID): replay probe write → assert exactly 13
   probed cells carry the right codes and the OTHER 107 bytes equal the
   verified-good vector; replay restore write → assert byte-identical to
   last-good. NO device I/O in DSH authoring.
2. Operator approval (agent-side, outside DSH): single live probe write
   (bind (1,1) + cmd-0x03). DO NOT dispatch DSH to write the device.
3. Operator types/drives the machine normally; the G12 event panel logs
   F13..F24 sightings with timestamp. Expected discovery outcomes:
   - sighting on key X → X's firmware cell = the probed slot → update
     EMPIRICAL map (KEYMAP_MAP.md) + GUI slot table in a later commit.
   - no sighting after a reasonable window → those 13 cells host NO
     physical keys on this firmware (or the operator hasn't pressed them —
     hops mitigate: list remaining unpressed candidates in the report).
4. Restore: single live revert write (verified-good recipe: 0x39@58+0xFF
   per vendor... NO — restore bytes = the last-good matrix EXACTLY: codes
   and types, i.e., vendor + caps overlay; the auto-revert insurance also
   covers it).
5. Report G13-REPORT.md: probe bytes, sighting table, updated empirical
   facts, restore confirmation, operator verdict verbatim.

## Constraints

- DSH authoring: zero device I/O, headless tests, G13 panel = a
  "DISCOVERY" section in the remap panel showing the probe slot↔code table
  and live event sightings (reads /api/keyevent feed; no extra backend
  state beyond the G12 keyevent log).
- At most 2 commits (panel + report).
- Final stdout ends with `G13_DONE tests=<n> commits=<sha1..2>`.
