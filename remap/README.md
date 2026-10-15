# `remap/` — Protocol Artifact Index

Versioned evidence for the GMMK Pro (VID `320F`, PID `5092`) stock-firmware key
remapping work. Every hex capture, finding, and smoke-test output produced by
the phases in `PLAN.md` lands here **before** any shipping code consumes it.

All artifacts are recorded with UTC timestamps and the exact payload hex, per
PLAN.md HARD SAFETY RULE 3. This phase (P0) touched **no device**.

## Artifact index

| Artifact | Phase | Status | Description |
| --- | --- | --- | --- |
| `README.md` | P0 | ✅ present | This index. |
| `probe_cmd_space.py` | P1 | ⏳ placeholder | Command-space sweep script (read-only feature-report requests only). |
| `probe_results.jsonl` | P1 | ⏳ not yet captured | One JSON line per probed command: candidate, response hex, length, parse. |
| `probe_0x81_baseline.txt` | P1 | ⏳ not yet captured | Baseline cmd `129` (0x81) profile/layer response with current config. |
| `probe_<cmd>.txt` | P1 | ⏳ not yet captured | Per-command dumps that differ from the all-zero baseline. |
| `DISCOVERY.md` | P1 | ⏳ placeholder | Findings doc: responsive commands, state-dump hypotheses, keymap layout guesses, negative findings. |
| `capture_plan.md` | P1 | ⏳ placeholder | Future "capture by comparison" session plan (Windows CORE + USB sniff); never run automatically. |
| `KEYMAP_MAP.md` | P2 | ⏳ placeholder | GMMK Pro key-index → physical key label table (~82 keys). |
| `smoke_setkeymap.py` | P2 | ⏳ placeholder | Operator-run single-key smoke + revert script (prints full hex + PASS/FAIL). |
| `SMOKE.md` | P2 | ⏳ placeholder | Operator-pasted smoke test output. |

## Ground rules (from PLAN.md)

- Only FEATURE report ID **7** on the vendor hidraw interface; no DFU, no
  bootloader, no kernel-driver detach/attach cycles.
- Zero writes in Phase 1; writes from Phase 2 onward always require a read-back
  confirmation plus a recorded hex dump here.
- Relative paths only; everything under this directory.

## Local-patch / rebase note (PLAN.md risk 5)

This clone already diverges from upstream (PID 5092 support, backend
descriptor gate). Phase 0 adds one more local patch to
`src/open_glorious_core/backend.py`:

- `StockGmmkProBackend.__init__(hidraw=...)` — optional keyword accepting
  `Path | None`. Unset → unchanged auto-detect. Explicit `Path` → used
  directly. Explicit `None` → "no device" unit-test mode (enabled by the
  module-level `_AUTO_DETECT` sentinel distinguishing "unset" from `None`).

Any upstream rebase must preserve this keyword; the `tests/` suite depends on
it for device-free construction.
