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
| `DISCOVERY.md` | P1→G3 | ✅ present (operator runs 1-3) | Findings doc: method, config-mirror decode (codes table at 0x80-0xF7), H1/H2 hypotheses, **H1 REFUTED on device (run 3)**, the virgin post-factory-reset buffer + the `0x07` report-ID echo framing (run 2), resume runbook. |
| `capture_plan.md` | P1 | ⏳ placeholder | Future "capture by comparison" session plan (Windows CORE + USB sniff); never run automatically. |
| `KEYMAP_MAP.md` | P2 | ✅ present | All 120 keymap slots → physical key names, derived from the real factory dump; the 3 unconfirmed raw bytes (14/72/92) flagged DANGER. |
| `default_keymap.bin` | P2 | ✅ present | 120-byte factory keymap table captured from the device (reset source of truth; packaged copy in `src/open_glorious_core/data/default_keymap.bin`, asserted equal by tests). |
| `PROTOCOL_DECODE.md` | G4 | ✅ present | **The write protocol, decoded from the vendor's own source** (Glorious CORE 2.1.22 app.asar): command table — cmd 0x01 ChangeProfile (4 selector bytes), cmd 0x03 KeyMatrix = the keymap WRITE (types `[8..127]`, codes `[128..247]`), cmd 0x08 polling-rate init, GET 129 = config mirror; `Buttoninfo_Default` = 240 B (types + codes) with its type histogram. |
| `../src/open_glorious_core/data/default_types.bin` | G4 | ✅ present | 120-byte vendor `Buttoninfo_Default` **types** array (0x01×77 normal, 0x02×7 modifiers, 0x03×1 consumer Mute, 0xFF×35 unused) — the `[8..127]` half of every cmd-0x03 write; packaged, length + histogram asserted by tests. |
| `../tests/fixtures/mirror_profile11.bin` | P2 | ✅ present | Full 256-byte factory config-mirror fixture (first baseline record verbatim) — keymap tests run against the real dump, no device. |
| `smoke_setkeymap.py` | G5 | ✅ present (operator-pending) | **G5 rewrite on the decoded protocol** (the H1 single-key/`--revert` modes are gone): `--matrix-write` (read mirror → take profile/layer FROM THE MIRROR → one cmd-0x03 write of that table, optionally patching ONE `--key` slot → GET → full-table byte-compare + selector check), `--revert-matrix` (packaged factory codes + types), `--factory-init` (virgin-buffer recovery: refuses unless the located table is all 0x00, then writes the factory defaults as cmd 0x03). `--run`-gated behind typing `WRITE`; exact-once send; prints full request/response hex + PASS/FAIL; refuses slots 14/72/92 for `--key` (N/A for the whole-image modes); table located with `locate_codes_table()` (echo 0x81 / payload 0x80); dry-run verified, no device IO by default. |
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

**G5 code note (2026-10-15, 11:30 UTC, DSH):** the WRITE path is re-implemented on
the G4 decode, replacing the refuted H1 builder (H1 = cmd 1 full-config payload,
refuted on device in run 3 — the firmware ignores cmd-1 bytes past [3]):
`set_keymap_codes(profile, layer, codes, types=None)` builds the cmd-0x03 KeyMatrix
report (`[0]=0x07 [1]=0x03 [2]=profile [3]=layer`, types at `[8..127]`, codes at
`[128..247]`, types defaulting to the packaged `default_types.bin`), sends it
EXACTLY ONCE behind the active-slot guard, then re-reads the mirror and byte-compares
the whole codes table. `change_profile(profile, layer)` is the new cmd-0x01
ChangeProfile (four selector bytes; verification = the mirror's selectors moved);
the old cmd-1 "profile update" full-config write is gone and
`_prepare_profile_update_buffer` is now `_prepare_change_profile_buffer`.
`locate_codes_table(response)` (module-level) resolves the codes table through the
`0x07` report-ID echo (0x81) and the payload-only capture shape (0x80) via
factory-anchor scan (`29 3A 3B 3C`) + echo-aware slice, and every mirror parse —
read_keymap, verifiers, reset, the smoke tool — uses it; `_parse_profile_layer_response`
gained the payload-only selector pair for the same reason. 106 tests green, device-free.
**Remaining on-device step (operator, gate G5):** `--dry-run` then `--run --matrix-write`
(identity write first, then the one-key probe on slot 13 → 0x68) + `--revert-matrix`.

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

## G3 gate — FAIL (honest), 2026-10-15

- factory-init tooling accepted: commit b5344f2, 66/66 tests green independently,
  guard + payload + exact-once send verified by operator code review.
- Live result: write reached the vendor interface, produced NO state change.
  H1 refuted for the virgin/post-factory-reset channel state.
- Feature status: READ path proven; SET/reset paths code-complete but unverified
  on-device pending write-path handshake discovery. Documented in remap/SMOKE.md.
