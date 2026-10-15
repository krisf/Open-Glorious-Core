# GMMK Pro Remap Protocol — Discovery Log (Phase 1)

Device: GMMK PRO wireless, VID `320F` / PID `5092`, vendor hidraw node `/dev/hidraw3`
(signature `06 01 ff 09 01 a1 01 85 07`, interface selected by `_detect_stock_interface()`).

## Batch 1 — status: **STOPPED pre-baseline — ZERO device exchanges**

Run: 2026-10-15, ~01:03 UTC (DSH session `session-f2394626`).
Outcome: **0 of 29 candidate probes executed. No byte was ever sent to the keyboard by
this session.** The mandatory baseline (3× cmd-129, all sane) could not be attempted:
the vendor node is not reachable from the DSH session. Machine-readable abort record:
`probe_results.jsonl` line 1 (`stage=init_profile_sync`).

## Method (as specified for Batch 1, as implemented in `probe_cmd_space.py`)

- Sole device path: `StockGmmkProBackend._exchange_feature_report()` — `fcntl`
  `HIDIOCSFEATURE`/`HIDIOCGFEATURE` ioctl on the vendor hidraw node. No pyusb, no
  kernel-driver detach, no DFU (HARD SAFETY RULES 1–2).
- Request layout exactly like `_make_request(cmd)`: 256-byte report 7,
  `[0]=0x07, [1]=candidate, [2]=active profile, [3]=active layer`, zeros elsewhere.
- Baseline first: 3× cmd-129 exchanges ≥0.3 s apart, all must parse to a sane
  profile/layer; one saved to `probe_0x81_baseline.txt`.
- Pre-flight before **every** candidate: a cmd-129 exchange that must parse sane;
  on failure the sweep STOPS immediately and the stop point is recorded here.
- Each non-129 candidate sent **exactly once** (one SET + one GET), never retried.
  Persistent EPIPE ⇒ "not supported by stock firmware" (RULE 1), noted, not retried.
- Every exchange appended to `probe_results.jsonl` (cmd hex, full request/response
  hex, length, meaningful length, parsed profile/layer, UTC timestamp); every
  non-zero candidate response also dumped to `probe_0x0NN.txt`.
- Minimum 0.35 s between all device transactions.
- Script refuses all device IO without an explicit `--run`; `--dry-run` instantiates
  the backend in device-free unit mode (`hidraw=None`) only.

### Candidate list (Batch 1, ascending) — 29 candidates

`0x00 0x03 0x04 0x05 0x06 0x08 0x09 0x0a 0x0b 0x0c 0x0d 0x0e 0x0f 0x10 0x11 0x12
0x13 0x14 0x15 0x16 0x17 0x18 0x19 0x1a 0x1b 0x1c 0x1d 0x1e 0x1f`

**Discrepancy flagged for gate G1:** the batch instruction enumerated the set as
"0x00–0x1F minus known 1,2,7" (= the 29 bytes above) but parenthetically said "26
candidates". The two descriptions disagree by 3; the **explicit enumeration wins**,
so the planned batch is 29 probes (highest = `0x1F`; `0x20`, everything above `0x1F`,
and `0x81` excluded this batch). Operator may cap with `--limit` at gate time.

## Baseline observations

None captured — see blocker below. The 3× cmd-129 baseline never completed its first
exchange, so `probe_0x81_baseline.txt` was intentionally **not** created (no fabricated
evidence).

## Where the sweep stopped (exact point)

1. **01:03:23 UTC** — `probe_cmd_space.py --run --baseline-only` started.
2. `_detect_stock_interface()` succeeded from sysfs and selected `/dev/hidraw3`
   (UEVENT shows `HID_ID=0003:0000320F:00005092`, `HID_NAME=USB HID GMMKPRO-WB`;
   descriptor head `06 01 ff 09 01 a1 01 85 07` matches the gate).
3. Baseline exchange #1 (the sanctioned cmd-129 read inside `StockGmmkProBackend.__init__`
   → `_sync_active_profile_layer`) failed at `os.open('/dev/hidraw3')` with
   `FileNotFoundError: [Errno 2] No such file or directory` — i.e. **before any ioctl,
   before any USB traffic**. Sweep STOPPED immediately per batch rules.
4. No candidate command (including `0x00`) was ever transmitted. Nothing to revert.

## Root cause (environment, not device)

The DSH session's mount namespace shows the host `devtmpfs` (which holds `/dev/hidraw*`)
bind-mounted at `/dev` read-only, **shadowed by the sandbox's own tmpfs on `/dev`**:

```
11366 … 0:7  / /dev ro,nosuid,nodev master:2 - devtmpfs devtmpfs rw,size=…
11452 … 0:131 / /dev rw,nosuid,nodev,relatime - tmpfs tmpfs rw,mode=755,uid=1000,…
```

Evidence chain:
- `/dev` contains only 14 synthetic nodes (no `hidraw*` at all); `statx /dev/hidraw3`
  → ENOENT; filesystem-wide search finds no hidraw node anywhere in the visible tree.
- `os.open('/dev/hidraw3', O_RDWR)` → `[Errno 2]` (device never reached).
- `CapEff=0000000000000000`: unprivileged, cannot recreate the node; sandbox
  escalation to full device access was attempted once and **failed closed — "no
  approval channel is available"**. Per sandbox policy the denial was not retried by
  any other route (no mknod/namespace tricks attempted — deliberately).
- The keyboard itself is **alive and normally bound** on the rig (driver `hid-generic`,
  sysfs present, three GMMK interfaces `hidraw1/2/3` enumerated). This run neither
  disturbed nor interrogated it.

## Per-candidate notes (Batch 1)

| cmd | status | response |
| --- | --- | --- |
| 0x00 … 0x1F (all 29, list above) | **not probed** — sweep aborted at baseline | — |

No response data exists for any candidate. No `probe_0x0NN.txt` dumps exist.

## Hypotheses

(empty — no data to hypothesize from; this section stays empty until real responses exist)

## Runbook to resume (for the operator, or a DSH session with `/dev` visibility)

From the repo root, on the rig where `/dev/hidraw3` is accessible:

```bash
# 0) preview only (touches nothing):
~/.local/share/open-glorious-core/venv/bin/python remap/probe_cmd_space.py --dry-run

# 1) baseline gate — must print 3 sane lines and write probe_0x81_baseline.txt:
~/.local/share/open-glorious-core/venv/bin/python remap/probe_cmd_space.py --run --baseline-only

# 2) batch-1 sweep (operator may cap ≤20 per invocation per PLAN.md, e.g. --limit 20,
#    then --skip-probed for the remainder):
~/.local/share/open-glorious-core/venv/bin/python remap/probe_cmd_space.py --run --skip-probed
```

The script self-gates: pre-flight health check before every probe, exact-once sends,
0.35 s spacing, immediate recorded stop on any unresponsiveness. For G1, verify:
`probe_0x81_baseline.txt` sane (profile/layer 1–3 each), keyboard visually unchanged,
`read_state()` returns sane values, no `abort` lines beyond expected history in
`probe_results.jsonl`.
