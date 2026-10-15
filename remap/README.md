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
| `probe_cmd_space.py` | P1 | ✅ present (batch-1 ready) | Command-space sweep script (read-only feature-report requests via `_exchange_feature_report` only; refuses device IO without `--run`; pre-flight 129 health check; exact-once sends; ≥0.35 s spacing; dry-run verified). |
| `probe_results.jsonl` | P1 | ⛔ blocked (0 exchanges) | Contains only the UTC abort record proving the sweep stopped pre-baseline (`stage=init_profile_sync`); no candidate was ever sent. |
| `probe_0x81_baseline.txt` | P1 | ⛔ not yet captured | Baseline blocked: vendor hidraw node not visible inside the DSH session's `/dev` (see `DISCOVERY.md` root cause). |
| `probe_<cmd>.txt` | P1 | ⛔ not yet captured | Per-command dumps that differ from the all-zero baseline. |
| `DISCOVERY.md` | P1 | ✅ present (batch-1 STOP record) | Findings doc: method, exact stop point, 29-vs-26 candidate-count flag for G1, resume runbook. Hypotheses intentionally empty. |
| `capture_plan.md` | P1 | ⏳ placeholder | Future "capture by comparison" session plan (Windows CORE + USB sniff); never run automatically. |
| `KEYMAP_MAP.md` | P2 | ⏳ placeholder | GMMK Pro key-index → physical key label table (~82 keys). |
| `smoke_setkeymap.py` | P2 | ⏳ placeholder | Operator-run single-key smoke + revert script (prints full hex + PASS/FAIL). |
| `SMOKE.md` | P2 | ⏳ placeholder | Operator-pasted smoke test output. |

**P1 batch-1 note (2026-10-15, 01:03 UTC):** sweep ran 0 device exchanges — the
vendor hidraw node is invisible inside the DSH session's `/dev` (devtmpfs shadowed
by sandbox tmpfs; escalation unavailable). Keyboard is untouched and alive. All
tooling is dry-run-verified; resume via the runbook in `DISCOVERY.md`.

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

## Operator gate log (Hermes)
- G0 (Phase 0) PASS 2026-10-14: 28/28 tests independently re-run green (0.02s); commit 9a36433;
  hidraw sentinel design verified in code (_AUTO_DETECT default = legacy auto-detect;
  hidraw=None = device-free unit mode). P0 accepted.
