# Smoke log — remap/smoke_setkeymap.py

## Run 1 — factory-init (2026-10-15T10:26Z), profile 1 layer 1

- Pre-flight: raw cmd-129 GET — virgin confirmed (entire 0x80–0xF7 table slice 0x00, header 00 00 00 00).
- Payload: cmd-1 full 256-byte factory image (capture `tests/fixtures/mirror_profile11.bin`,
  table = packaged `default_keymap.bin`, header (1,1), brightness 0x14). Built by
  `_build_keymap_write_payload`; dry-run-inspected before send (slice == factory, offsets correct).
- Interactive WRITE gate passed; SET sent **exactly once** (`_send_feature_report`).
- Post-read: `_read_config_mirror` → BackendError "device response carries no parsable profile/layer".
- Plain raw re-read: **mirror unchanged — all zeros, header 00 00 00 00, table all-zero.**

## Verdict

**FAIL — H1 write hypothesis REFUTED for the virgin/post-factory-reset channel state.**
The firmware accepted the feature-report exchange without error but ignored the cmd-1
full-config write. No retry was performed. No other writes were attempted.

## Interpretation (honest bounds)

- The write *reached* the vendor interface (no EPIPE) but had no observable effect.
- Most plausible cause: the configurator channel requires a session/handshake sequence
  before accepting config writes (what Glorious CORE does on connect — command family
  not yet discovered). Tonight's probe sweep (29 candidates, all byte-identical GET
  mirrors) did not include it.
- The read path (GET mirror) remains proven; the failure is write-path-only.

## Safety record

- Zero writes other than the single factory-image SET above.
- Device remained factory-default throughout; user's typing (NVRAM binds) unaffected.
- Per plan discipline: no blind retries; device state re-verified read-only after FAIL.
