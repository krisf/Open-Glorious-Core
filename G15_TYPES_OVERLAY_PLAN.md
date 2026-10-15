# G15 — Types overlay: GUI-assigned cells must not ride vendor 0xFF type bytes

## Operator-confirmed root cause #2 (the one G11 could not see)

Operator GUI write: codes[58]=0x54 (NumpadDivide), single changed cell
(last-good-1-1.json pending). Physical result: "capslock continues to
function like capslock". Verify log: armed at 12:51:58, timed out
12:52:58 (no input reached the app; user typed in other windows), NOT
auto-reverted (seed unverified — correct per G12 item 4).

Cause: controller `apply-keymap` calls `set_keymap_codes(profile, layer,
bytes(raw))` with types=None → backend substitutes packaged vendor
`data/default_types.bin` → `types[58] = 0xFF` (vendor "unassigned"). The
firmware honors the TYPE byte as cell activation: a 0xFF-typed cell
ignores its code byte entirely (evidence: the operator's manual recovery
write that restored caps set types[58]=0x01 explicitly and landed; the
GUI write with implicit 0xFF did nothing). G11's slot-58 rebinding was
necessary but not sufficient: the GUI write path also needs the types
overlay.

## Fix spec

1. `apply-keymap` (app.py): build effective types = packaged default
   types, then:
   - any slot whose effective code is non-0xFF but whose default type is
     0xFF → force type 0x01 (normal key);
   - any slot assigned a modifier code (0xE0–0xE7) → force type 0x02
     (matches vendor modifier typing);
   - never touch slots whose default type is 0x01/0x02/0x03 (protects the
     knob's 0x03 consumer typing and the modifier set).
   Pass the effective types explicitly to `set_keymap_codes`. Same
   treatment in `reset-keymap`'s factory overlay path (the empirical
   overlay: codes[58]=0x39, types[58]=0x01 — factory reset must never
   write 0xFF types onto the empirically-alive caps cell).
2. `/api/keystate`: extend the display overlay to report the EFFECTIVE
   types (slot 58 shows type normal, not 0xFF); add `type_name` or a
   boolean the UI can show ("cell active").
3. E2E (fake HID, extend d0933d3 suite):
   - GUI flow caps→NumpadDivide produces captured types[58]=0x01 AND
     codes[58]=0x54;
   - GUI flow reset-to-factory produces types[58]=0x01 (not 0xFF) and
     codes[58]=0x39;
   - knob slot keeps 0x03; modifiers keep 0x02 after a normal apply.
4. Verify banner copy: prepend "Click this app window, then press the
   key." (the 12:51 timeout was unfocused input; operator guidance beat).
5. `G15-REPORT.md`: root cause, evidence chain (last-good pending bytes,
   verify log timestamps, types[58]=0xFF vendor default, recovery-write
   contrast), operator re-gate instructions.
6. Zero device I/O in authoring; headless tests; node --check touched js.
   At most 2 commits:
   a. `fix(keymap): effective types overlay on apply/reset (GUI writes activate empirical cells)`,
   b. `docs(g15): types-overlay report`.
7. Final stdout ends with `G15_DONE tests=<n> commits=<sha1..2>`.
