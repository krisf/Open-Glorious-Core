# GMMK Pro Key Remapping Implementation Plan

> **For Hermes/DSH:** This plan is written to be executed by the DeepSeek Harness (DSH) in
> `~/projects/open-glorious-core`. Execute phases strictly in order, one at a time. Do not
> skip ahead to the device-touching phases without completing the gates between them.

## HARD SAFETY RULES (READ FIRST — violation = phase abort)

1. **NO FIRMWARE FLASHING.** No DFU writes, no bootloader mode, no firmware update
   commands of any kind. The only HID traffic permitted is FEATURE report ID **7**
   on the existing vendor interface. If any command returns EPIPE persistently, that
   mapping is "not supported by stock firmware" — stop, don't probe further on that family.
2. **NO destructive kernel-driver probing on the main hidraw node.** Do NOT use
   `pyusb` `detach_kernel_driver` / `attach_kernel_driver` cycles on the keyboard.
   The backend's own raw `fcntl` `ioctl` path (already in `backend.py`) is the only
   device access method. If pyusb is used at all, it is read-only descriptor reads,
   and `usb.util.dispose_resources(dev)` MUST be called before any re-attach (see
   EPIPE-EBUSY gotcha in skill `glorious-gmmk-linux`).
3. **NO writes without a read-back confirmation** and a recorded hex dump of the exact
   payload. Every probe/capture must be saved as a `.txt`/`.json` artifact with a UTC
   timestamp (log filename should be `UTC` — the system clock is UTC; nothing else in
   the repo depends on that).
4. **Relative paths only.** DSH sandbox denies writes to absolute paths. All artifacts
   go under `remap/` (relative) in the repo.
5. **No service/system changes.** No systemctl, no install.sh, no udev rules touched.
   The operator (Hermes) deploys after final verification.
6. **No `git push`.** DSH's sandbox blocks it (credential-store lock errors). Commits
   stay local; the operator pushes if/when the user asks.

## Goal

Add a **Key Remapping** feature to Open-Glorious-Core that lets the user remap keys on
the GMMK Pro (stock firmware, VID 320F, PID 5092) from the app's Linux UI, without
flashing custom firmware. Remaps are applied as HID feature-report commands and written
to the keyboard's onboard config, exactly like Glorious CORE (Windows) does — that
product's own documentation confirms per-key remapping works on stock firmware, so the
protocol command family already exists.

## Current Context / Assumptions

**Codebase (verified 2026-10-14):**
- `src/open_glorious_core/backend.py` (423 lines) — `StockGmmkProBackend` talks
  FEATURE report ID 7, 256 bytes, to the vendor hidraw node (itf2, signature
  `06 01 ff 09 01 a1 01 85 07`). Known command bytes: `[1]=7, [2]=1` =
  profile-update (persist), `[2]=2` effect, `[2]=7` properties, `[2]=129` (0x81)
  read active profile/layer. `_exchange_feature_report()` does send + read back.
- `src/open_glorious_core/device.py` — already patched by us: `SUPPORTED_PRODUCT_IDS`
  includes `"5092"` (our GMMK Pro wireless). backend.py `_detect_stock_interface()`
  accepts PID 5044 or 5092 + descriptor signature gate.
- `src/open_glorious_core/app.py` — PySide6 shell with
  `OpenGloriousCoreWebController` exposing backend calls to a local HTTP-driven UI
  (`static/index.html`, `static/app.js`).
- No test suite yet (`tests/` absent). venv: `~/.local/share/open-glorious-core/venv`
  (PySide6 6.12, no pytest — install `pytest` into that venv first thing).

**Protocol knowledge gaps (what Phase 1 is for):** nothing in the codebase, the
upstream issue tracker (only issue #1, GMMK3 support), or public RE write-ups
documents the remap command family on GMMK Pro stock firmware. It must be discovered
empirically in Phase 1 and captured as artifacts before any UI ships.

**Hardware/facts:**
- Keyboard: GMMK PRO wireless, VID 320F / PID 5092, connected to this rig. Vendor
  node = `/dev/hidraw3` (can shift; detect via signature in `_detect_stock_interface`).
- udev rule live: `/etc/udev/rules.d/99-glorious-gmmk.rules` (`320F` hidraw 0666).
- Working setters (verified live): `set_effect(7)` → `set_color_name("white")` →
  `set_brightness(n)`, each auto-sending the profile-update report. That auto-save
  behavior (persist-on-set) is the template for how remap writes should persist.
- RGB settings = per-profile on the device (profile 1-3 × layer 1-3). Remaps are
  expected to be per-profile too (exact scoping to be confirmed in Phase 1).

**Environment for DSH runs:**
- Interpreter: `~/.local/share/open-glorious-core/venv/bin/python`. All scripts add
  `sys.path.insert(0, 'src')` so they exercise the patched `src` tree, not the venv
  copy.
- `report[i]` in existing code = report-ID byte; on-the-wire feature report = ID byte
  + payload. Payload offsets in this plan follow the existing code's 0-based indexing
  (report[0]=ID 7, report[1]=command...).

## Proposed Approach

Four phases, each gated by the operator (Hermes) running an independent verification
gate between phases. Discovery before UI. Read-only probing before any write. Protocol
artifacts (hex captures, enums) land in `remap/` as versioned evidence before any
shipping code consumes them.

```
P0 repo/test scaffolding → P1 protocol discovery (READ-ONLY) → operator gate
→ P2 remap backend + persistence (writes enabled, gated by P1 artifacts)
→ operator gate → P3 UI + docs → final operator acceptance (incl. revert E2E)
```

---

## Phase 0 — Repo scaffolding + test harness

**Objective:** Get a test suite running against the existing backend with the venv.

**Files:**
- Create: `tests/__init__.py`, `tests/conftest.py`, `tests/test_backend_core.py`
- Create: `remap/` dir + `remap/README.md` (artifact index, updated each phase)

**Steps:**
1. `~/.local/share/open-glorious-core/venv/bin/python -m pip install pytest`
2. `tests/conftest.py` — fixture that instantiates
   `StockGmmkProBackend` with `hidraw=None` (pure unit mode, no device IO). To make
   that possible without a device, refactor `_detect_stock_interface` invocation out
   of `__init__` minimally: allow `StockGmmkProBackend(hidraw: Path | None = None)`
   keyword, defaulting to current auto-detect. No behavior change when unset.
3. `tests/test_backend_core.py` — tests pinning CURRENT behavior:
   - `_prepare_properties_buffer` byte layout (offsets 0,1,2,3,4,8,10)
   - `_prepare_effect_buffer` byte layout incl. speed inversion (`report[8] = 100 - speed`)
     and multicolor flags at 10/12, color at 15/16/17
   - `_prepare_profile_update_buffer` layout (cmd 1, profile, layer)
   - `_make_request(129)` → report[1] == 129
   - `_parse_profile_layer_response` returns None on garbage
4. Run: `venv/bin/python -m pytest tests/ -v` → all pass (pure refactor, no logic change).
5. `remap/README.md` — index of protocol artifacts (empty for now, filled by Phase 1).
6. Commit: `feat: add pytest scaffolding + unit-test current RGB byte layout`

**Verify:** `pytest tests/ -v` exit 0, count printed. `git log --oneline -1` shows commit.

---

## Phase 1 — Protocol discovery: read-only probing

**Objective:** Find the remap command family and capture evidence. **ZERO writes.**

**Ground rules for this phase:**
- The ONLY device operation allowed: `_exchange_feature_report()` with command bytes
  that are read-requests. The existing known-good read is cmd **129** (0x81,
  profile/layer query). Discovery sweeps send a request with `[1]=candidate` and
  read the response — that is a feature-report READ transaction, not a config write.
  BUT: any command whose response looks like a state dump (large, structured, matches
  known config layout) is treated as READ-only evidence; anything that causes a
  **visible device behavior change** (RGB flicker, LED test pattern, key latency) or
  makes the keyboard stop responding is treated as a WRITE attempt → back off,
  record, don't repeat.
- If a probe changes something visible: **immediate revert attempt** using the known
  RGB setters (the current config: effect 7, white, brightness as set) and the
  keyboard must come back to a state where `read_state()` returns a sane
  brightness/effect. If the keyboard stops responding entirely for >5s, STOP the
  whole phase; log the blocking command in `remap/DISCOVERY.md` and leave it alone.

**Method:** high-throughput, low-risk: sweep the plausible command space with short
scripted exchanges, capture every response as hex.

**Files:**
- Create: `remap/probe_cmd_space.py` — sweeps command bytes `[1]` ∈ {0x00..0x20,
  0x7E..0xFF} (skip known: 1, 2, 7, 129) at 0.3s intervals; response hex + length +
  parse of profile/layer candidates → append to `remap/probe_results.jsonl`; only
  command IDs whose response differs from the all-zero baseline response get their
  own `remap/probe_<cmd>.txt` dump.
- Create: `remap/DISCOVERY.md` — findings doc, updated as the phase runs.

**Steps:**
1. Write a baseline capture: cmd 129 response with the current known profile/layer
   → `remap/probe_0x81_baseline.txt`.
2. Write `probe_cmd_space.py` with the sweep + safety rules above. Include an
   `--limit N` flag (run in batches of ≤20 commands per invocation, operator gates).
3. **Gate G1** (operator runs this after the first ≤20): sanity-check baseline,
   keyboard still alive, no visible behavior change, `read_state()` sane.
4. Continue sweep in batches until command space covered or stable no-new-findings
   for 2 batches.
5. Analyze responses offline (no device):
   - Group by response length/content; deduplicate.
   - For any response that looks like a state/config dump (dense bytes, plausible
     field sizes), write a hypothesis section in `remap/DISCOVERY.md`: where keymap
     bytes could live (layout: 82 keys for GMMK Pro 75% — 5 rows of 16-17, likely
     1-2 bytes/key: scancode-identifiers, maybe modifiers/layers).
6. **Targeted hypothesis test** — from the hypotheses, the most likely remap command:
   - On **paper first** (DISCOVERY.md): exact byte layout of a write for a single-key
     remap that CORE would send: e.g. cmd byte, profile/layer, key index, scancode,
     modifier flags, trailing checksum/echo byte.
   - Write `remap/capture_plan.md` — what a "capture by comparison" session looks
     like IF the user chooses to run one later (enumerate every CORE-side action and
     the exchange that would need recording). Do NOT run it in this phase; do not
     install anything; it's a future-option doc.
7. Commit: `docs(remap): protocol discovery artifacts + findings`

**Verify (operator gate G2):** `remap/probe_results.jsonl` exists with ≥1 baseline
entry; keyboard alive (`read_state()` via venv works); commits present. Operator
reviews DISCOVERY.md hypotheses and decides whether P2 proceeds on the hypothesis
or gets re-run with a different sweep.

**Failure policy:** if the sweep finds NO responsive command that looks like a
state/keymap dump: write up negative findings in DISCOVERY.md, commit them, and
STOP — the planner (Hermes) decides next (one fallback: software-side remapping
inside the app via uinput — a totally safe fallback, but requires uinput perms and
breaks the "onboard persistence" property; needs user decision, not DSH).

---

## Phase 2 — Remap backend + onboard persistence

**Objective:** Implement `set_keymap(entries)`, `read_keymap()`, and (if P1 found a
reset command) `reset_keymap()` on `StockGmmkProBackend`, using the protocol discovered
in P1.

**Files:**
- Modify: `src/open_glorious_core/backend.py` — add to `StockGmmkProBackend`:
  - `KeymapEntry` dataclass: `key_index: int`, `scancode: int`, `modifier_mask: int`
    (or `None`), `label: str | None` (UI hint only).
  - `read_keymap(profile, layer) -> list[KeymapEntry]`
  - `set_keymap(entries, profile, layer, save=True)` — builds feature report per P1
    layout; sends; **read back and verify** (`read_keymap()` confirms echo); raises
    `BackendError` on mismatch without partial-write claims.
  - `reset_keymap(profile, layer)` — only if a reset command was positively
    identified in P1 artifacts; raise `BackendError("not found")` otherwise.
- Create: `tests/test_keymap_layout.py` — unit tests for the report-builder with the
  exact byte offsets from P1 artifacts. Include a `remap/__files__/`-referenced copy
  of the P1 hex fixture so tests don't need the device.
- Create: `remap/KEYMAP_MAP.md` — GMMK Pro key-index table: for each of the ~82 keys,
  index → physical key label. Populated from P1 analysis (or manual enumeration with
  the user's help if the index scheme is positional row/col).

**Steps:**
1. Write failing tests first (TDD): builder produces exact P1 bytes for a 3-entry
   remap fixture.
2. Implement builder + sender + read-back verifier.
3. Run: `pytest tests/ -v` → all pass.
4. **Device smoke test (operator-gated, written by DSH, run by operator):**
   `remap/smoke_setkeymap.py` — remaps ONE key to a HARMLESS value and back:
   `smoke --key <idx> --to <scancode>` then `--revert`. Default candidate: remap
   `ScrnLk` → `F13`-equivalent or a rarely-used key → `NoEvent` then revert. Script
   MUST print the full hex request + read-back hex + PASS/FAIL. Operator runs it,
   pastes output into `remap/SMOKE.md`.
5. Commit: `feat: stock-firmware keymap read/write backend + tests`

**Verify (operator gate G3):** pytest green; smoke test output pasted; keyboard
still alive; original key still working (the user must physically confirm the
revert — Hermes asks the user "is your key back to normal?" before P3).

---

## Phase 3 — UI + persistence + docs

**Objective:** Expose remapping in the web UI and wire persistence into the existing
app flow.

**Files:**
- Modify: `src/open_glorious_core/app.py` — controller methods
  `get_keymap(profile, layer)`, `set_keymap(entries, profile, layer, save)`,
  `reset_keymap(profile, layer)`; wire into the existing JSON routing pattern.
- Modify: `src/open_glorious_core/static/index.html`, `app.js`, `styles.css` —
  new "Keymap" tab: 
  - visual keyboard layout (GMMK Pro 75% grid, CSS grid of buttons matching
    `remap/KEYMAP_MAP.md` indices);
  - click a key → dropdown of standard scancodes (letters, F-keys, media, nav,
    layer-tap candidates if supported) + modifier checkboxes;
  - "Apply" → `set_keymap(save=True)` (persist onboard like RGB setters do);
  - "Reset" per-key and per-profile;
  - status/error surfaces using the existing message pattern.
- Modify: `README.md` — move "keybind and shortcut editing" from planned to current;
  document the P1-found protocol in `docs/` or `remap/DISCOVERY.md` link.
- Create: `tests/test_app_keymap_routes.py` — route handlers return the right JSON
  given a mocked backend.

**Steps:**
1. Route tests (TDD) → implement controller methods → pass.
2. UI: build the tab with `remap/KEYMAP_MAP.md` as the single source of key indices.
3. Headless UI smoke test: `remap/ui_smoke.py` — instantiates the controller, calls
   `get_keymap(1,1)` and `set_keymap(..., save=False)` (no save = no persistence)
   against a **mocked hidraw** — no real device needed for the route layer.
4. `pytest tests/ -v` all green.
5. Update `remap/README.md` artifact index with all P1-P3 artifacts.
6. Commit: `feat: key remapping UI + onboard persistence + docs`

**Verify (operator gate G4):** pytest green; UI code review by Hermes; user-driven
manual UI test (user clicks through the real app on the rig); keyboard alive.

---

## Final Operator Acceptance (Hermes runs after G4)

1. Re-run `pytest tests/ -v` myself; read the pass counts.
2. Re-read `remap/DISCOVERY.md` + `SMOKE.md`; verify every claim against artifacts.
3. **Revert E2E:** with the user's go-ahead, remap a key in the real app, confirm
   it works, then reset to default and confirm it's back — prove the feature is
   safe to leave enabled.
4. Full secret scan: `grep -rE "sk-|api[_-]?key|token" src tests remap --include="*.py" --include="*.js"`
   → expect only false-positive words like `scancode`; nothing real.
5. `git log --oneline` review: every phase commit present, no stray files.
6. Report to user: what works, what's still unknown about the protocol, and how to
   uninstall (git revert to pre-plan commit `425b2ef` restores stock app).

## Files Likely to Change (summary)

- `src/open_glorious_core/backend.py` — KeymapEntry + read/set/reset on stock backend
- `src/open_glorious_core/app.py` — controller routes
- `src/open_glorious_core/static/{index.html,app.js,styles.css}` — Keymap tab
- `tests/` — new suite (backend byte layout, keymap builder, routes)
- `remap/` — protocol artifacts, discovery docs, smoke scripts + outputs
- `README.md` — status update

## Risks / Tradeoffs / Open Questions

1. **Protocol risk (biggest):** the remap command family may not be discoverable
   without CORE-side captures. Mitigation: P1 is read-only with operator gates;
   documented fallback = software-side remap via uinput (no flashing, but no
   onboard persistence; needs user decision).
2. **Brick-adjacent risk:** a malformed config write could leave the keyboard with a
   bad keymap until defaults are restored (a reset command, if found, fixes it; a
   power cycle likely does too, since CORE's own remaps survive power cycles in
   CORE-managed configs). Mitigation: P1 first, minimal writes, smoke test with
   revert built in, no mass remaps until Phase 3.
3. **Wireless variant (PID 5092):** protocol was RE'd by nobody publicly; possible
   some commands are gated behind wired mode. P1 will reveal whether the read
   command family responds on 5092 at all.
4. **Per-key persistence scope unknown:** profile/layer scoping of keymaps might
   differ from RGB scoping. P1/P2 confirms via read-back before save.
5. **Upstream divergence:** our clone is already patched (PID 5092 + backend gate).
   Phase 0's keyword-`hidraw` param is another local patch; keep a rebase note in
   `remap/README.md`.
6. **Open question for the user:** if P1 fails to find the write command — would they
   accept a capture-by-comparison session (install CORE in a VM, capture USB during
   one remap, us decode it) as the escalation, or prefer the uinput fallback?

---

## OPERATOR AMENDMENT (2026-10-14, G1 discovery): device execution split

DSH's sandbox has no /dev/hidraw* (minimal /dev in its mount namespace; sysfs only,
fail-closed). All DEVICE TOUCHING in P1/P2/P3 is executed by the OPERATOR (Hermes)
running the scripts DSH writes; DSH remains the author of ALL shipped code, tests,
probe tooling, and analysis. Device-execution results (JSONL, hex dumps, smoke
output) are saved by the operator into remap/ and referenced by DSH for analysis.

---

## INCIDENT + SCHEDULING NOTES (2026-10-15 ~01:05-01:50 UTC)

While P1 batch 1 ran, the rig's serving stack experienced a cascade:
1. `radiance-qfn` (vLLM :8180, DSH's model backend) died on a `0.0.0.0:8180` bind
   collision with a stale Maple temp shim left holding the port (swap.sh had warned
   about exactly this in its comments — the shim must die before any engine binds).
2. Recovery cost DSH three aborted sessions (TRANSPORT connection errors at turn 1).
3. Root cause of the port squat + double sweep: the dsh-swe-bench DeepSWE battery
   (`run_dsh_swe_ab.sh`, driver started ~21:19 EDT, and its scheduled cron
   `deepswear-3stack-ab-2026-10-15` = once @ 01:00 EDT) swaps engine configs per
   task×stack via swap.sh — it evicted the live EXL3 stack twice mid-session.
4. Resolution: battery driver + workers killed (partial results kept in
   results/ab-2026-10-15.jsonl), maple temp shim killed, stale container removed,
   EXL3 stack relaunched clean (3rd attempt, engine stable), and the 01:00 EDT
   once-cron PAUSED via cronjob_manage with an audit reason. Resume or reschedule
   it after the keyboard phases complete.

Rule going forward: NO concurrent swapper may touch :8180 while a DSH phase is in
flight — check `pgrep -af "swap.sh|temp_shim"` and the cron list before dispatch.
