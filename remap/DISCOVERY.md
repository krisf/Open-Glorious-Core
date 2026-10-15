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

## OPERATOR RUN — Batch 1 sweep executed by Hermes (2026-10-15 ~01:05 UTC)

Per the operator amendment: DSH wrote the tooling, Hermes ran it on the rig where
/dev/hidraw3 lives. Executed: `--run --baseline-only`, `--run --limit 20`,
`--run --skip-probed` (total: baseline ×12 healthy, all 29 candidates probed).

### Result: ZERO side effects + the config mirror found

- Every probe = one SET(cmd c, profile 1, layer 1) + one GET. **All 29 GET responses
  are byte-identical to the pre-sweep baseline** (sha256-verified across all records).
  Interpretation: for this device, hidraw GET on report 7 does NOT execute the
  candidate command — it MIRRORS the active profile's full config buffer. The sweep
  is therefore read-side-inert by construction (SET goes nowhere without a valid
  command, GET ignores the request entirely).
- The mirror payload (256 B) decodes cleanly against the known live state:
  - `[0]=01 [1]=01` → active profile/layer (matches cmd-129 parse: 1,1)
  - `[6]=0x14 [7]=0x14` → brightness on the 0–200 scale: 0x14=20 ≙ the 10 % white
    brightness the user set on 10-14 (new value AND something at [7] track it)
  - `0x26`=…`=01`, `0x1F/0x21/0x27`=ff sentinel bytes (RGB state region)
  - **0x80–0xF7: a 1-byte-per-key USB HID scancode table in physical key order.**
    Verified row-by-row against the GMMK Pro 75 % layout: `29`=Esc, `3a–45`=F1–F12,
    `35`=grave, `1e–27`=1…0, `2b`=Tab, `14…13`=QWERTYUIOP, `04…0f`=ASDFGHJKL,
    `1d…10`=ZXCVBNM, `e1/e0/e3/e2/e4/e5/e6`=modifiers, `2c`=Space, `50/51/4f`=arrows,
    `4b/4e/4d`=PgUp/PgDn/nav, `4c`=Del, `28`=Enter, `2a`=Bksp. `ff` = unassigned slot.
  - Trailing `0xF8…=00` tail = spare/extension space.

### Hypotheses (first real ones — data exists now)

- **H1 (STRONG): the keymap table in the mirror (0x80–0xF7) is the live onboard
  keymap, and the remap WRITE path is the existing profile-update command (cmd 1)
  with a FULL 256-byte payload** — i.e. cmd 1 = "write config", where zero bytes in
  the payload mean "keep current" (this is consistent with the shipped RGB setters:
  they send cmd 1 with mostly-zero payloads and the device keeps everything except
  what they set). CORE's remap UI would simply ship a payload whose 0x80–0xF7 bytes
  differ. Testable WITHOUT any behavior change: send cmd 1 carrying the current
  mirror bytes with ONE keymap byte changed to its own current value (no-op write);
  verify the mirror still reads identical. Then the real micro-write + revert.
- **H2 (fallback): a dedicated remap command exists in 0x20–0x7E** (not yet swept).
  H1 gets tested first because it requires no new command and no dangerous probing.
- The mirror semantics ALSO give P2 its read-back verifier for free: `read_keymap()`
  = GET report 7, slice 0x80–0xF7; write verification = re-GET and compare bytes.

### Batch 2 plan (pending operator/user go)

1. H1 no-op write test (safest possible first write — writes current values back).
2. H1 micro-write smoke: one harmless key → new scancode, mirror-verified, reverted,
   mirror-verified again. Requires user approval (physical confirmation of the key).
3. Only if H1 fails: sweep 0x20–0x7E with the same proven-inert probe tooling.

## OPERATOR RUN 2 — H1 smoke preflight blocked by channel reset (2026-10-15 ~03:00 UTC)

Findings (all READ-ONLY; zero writes sent to the device tonight):
- First smoke attempt REFUSED at pre-flight: mirror reports active profile 3/1 whose
  config buffer is entirely zeros (bright bytes AND the whole keymap table) — a virgin,
  never-configured slot. Writing the profile-1 payload there would corrupt slot 3.
- Poller (1s GET loop, ~4 min, remap/profile_poll.log): user pressed the official
  wired-guide combo FN+CTRL+Up 2-3x live — the configurator channel did NOT move
  (hdr stayed 07 03 01 01). Hardware profile cycling (LED picker for typing profile)
  is DECOUPLED from the feature-report configurator channel on this unit.
- Selector test: GET(129) with req[2]/[3] ∈ {(1,1),(2,1),(3,1),(0,0)} → responses all
  identical (07 03 01 01 + zeros): the channel does not honor request selectors; it
  always reports the firmware's current protocol-configured slot.
- Report-id prefix: post-P2 _exchange_feature_report returns the 0x07 report id as
  byte 0 (probe-era captures stored the payload without it). Fixture offsets hold for
  the payload (post-prefix); P2 code handles this consistently.
- Root cause of the 1→3 flip found in kernel node timestamps: /dev/hidraw3 re-created
  22:48:58 EDT 2026-10-14 (wireless sleep/resume re-enumeration, ~5 min before the
  first smoke). The re-enum reset the configurator channel to its power-on default
  slot (3, virgin). The user's onboard profile-1 config (white/10% from the earlier
  RGB session) remains intact in NVRAM — untouched by anything tonight.
- Device is physically fine throughout (user typing normally).
IMPLICATIONS: before ANY cmd-1 full-config write, the channel must be serving a
CONFIGURED slot (mirror = real table, not zeros). Candidate recoveries: full
power-cycle (off/on via switch/dongle — different event class than sleep-resume),
or future protocol-level "set current profile". Pending operator gate G3.
