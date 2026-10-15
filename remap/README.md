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
| `probe_results.jsonl` | P1 | ✅ present (39 records) | 1 UTC abort record (DSH pre-baseline stop) + operator-run capture: 9 baseline + 29 probe exchanges; every response byte-identical (mirror behavior, ZERO side effects). |
| `probe_0x81_baseline.txt` | P1 | ✅ captured (operator run) | Baseline 129 response hex dump — the 256-byte config mirror (profile/layer 1,1). |
| `probe_<cmd>.txt` | P1 | ✅ captured (×29) | Per-candidate dumps; all 29 identical to the baseline mirror (no command-specific response found in 0x00-0x1F). |
| `DISCOVERY.md` | P1 | ✅ present (batch-1 results) | Findings doc: method, stop record, decode of the config mirror (keymap table at 0x80-0xF7), H1/H2 write hypotheses, resume runbook. |
| `capture_plan.md` | P1 | ⏳ placeholder | Future "capture by comparison" session plan (Windows CORE + USB sniff); never run automatically. |
| `KEYMAP_MAP.md` | P2 | ✅ present | All 120 keymap slots → physical key names, derived from the real factory dump; the 3 unconfirmed raw bytes (14/72/92) flagged DANGER. |
| `default_keymap.bin` | P2 | ✅ present | 120-byte factory keymap table captured from the device (reset source of truth; packaged copy in `src/open_glorious_core/data/default_keymap.bin`, asserted equal by tests). |
| `../tests/fixtures/mirror_profile11.bin` | P2 | ✅ present | Full 256-byte factory config-mirror fixture (first baseline record verbatim) — keymap tests run against the real dump, no device. |
| `smoke_setkeymap.py` | P2 | ✅ present (operator-pending) | Single-key smoke + `--revert` + `--factory-init` (virgin-buffer recovery after the official factory-reset hotkey: refuses unless the whole 0x80-0xF7 table reads 0x00, then one cmd-1 write of the factory capture image + read-back byte-compare + header (1,1) check), `--run`-gated with `WRITE` confirmation; prints full request/response hex + PASS/FAIL; single-key mode refuses slots 14/72/92 (N/A for `--factory-init` — payload is the factory image); dry-run verified. |
| `SMOKE.md` | P2 | ⏳ placeholder | Operator-pasted smoke test output (gate G3). |

**P1 batch-1 note (2026-10-15, 01:03 UTC):** sweep ran 0 device exchanges — the
vendor hidraw node is invisible inside the DSH session's `/dev` (devtmpfs shadowed
by sandbox tmpfs; escalation unavailable). Keyboard is untouched and alive. All
tooling is dry-run-verified; resume via the runbook in `DISCOVERY.md`.

**P2 code note (2026-10-15, 02:00 UTC, DSH):** keymap backend implemented on the
PROVEN mirror read path (`read_keymap`) + H1 write path (`set_keymap`/`reset_keymap`,
UNVERIFIED — single builder `_build_keymap_write_payload`, exact-once send, full
read-back byte-compare, BackendError without claiming partial writes). 46/46 tests
green against the real dump fixture. `smoke_setkeymap.py` awaits OPERATOR execution
(gate G3): dry-run payload verified, `--run` + physical confirmation required.

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
- G1 (Phase 1 batch 1) PASS 2026-10-15: sweep executed by OPERATOR (DSH sandbox has no
  /dev nodes — operator amendment in PLAN.md). Baseline ×12 healthy; all 29 candidates
  probed; every response byte-identical to pre-sweep baseline = ZERO side effects.
  Result: hidraw GET mirrors the config buffer; scancode table decoded at 0x80-0xF7;
  H1 write hypothesis (cmd 1 full-payload) pending device test. Commit cb2bebf.
- INCIDENT 2026-10-15 01:09 UTC (out of scope, infra): vLLM :8180 (DSH backend) went
  DOWN mid-plan (radiance-qfn exited 1 on 0.0.0.0:8180 bind collision after an earlier
  crash). Restart via swap.sh radiance in progress; DSH work resumed after recovery.
- G2 (Phase 2 coding) PASS 2026-10-15: DSH authored (commit cf0bc4d) after 3 aborted
  attempts (engine-port thrash — see PLAN.md incident log). Operator verification:
  independent pytest re-run 46/46 green (0.03s); fixture provenance byte-exact
  (tests/fixtures/mirror_profile11.bin == first baseline record of
  probe_results.jsonl; src/open_glorious_core/data/default_keymap.bin ==
  fixture[0x80:0xF8], 120 slots, Esc=0x29 F1-F12=Pscr head matches decode);
  read_keymap (PROVEN mirror path) + set_keymap/reset_keymap (H1 write, isolated in
  _build_keymap_write_payload, read-back verifier, BackendError on mismatch);
  remap/KEYMAP_MAP.md = 120-slot decode (6 rows x 20, 15 used + 5 spare, slots
  14/72/92 flagged unconfirmed); remap/smoke_setkeymap.py properly gated (refuses
  without --run/--dry-run, DANGER_SLOTS blocked, factory-byte --revert).
  REMAINING: H1 device smoke (operator-gated single-key remap + revert) — awaiting
  operator/user go.
