#!/usr/bin/env python3
"""GMMK Pro keymap micro-write smoke test — OPERATOR-RUN ONLY (device IO).

G5 rewrite: the write path is the one decoded out of the vendor's own source
(remap/PROTOCOL_DECODE.md) — **cmd 0x03 KeyMatrix**, ``[0]=0x07 [1]=0x03
[2]=profile+1 [3]=layer+1``, the 120-byte **types** array at ``[8..127]`` and the
120-byte **codes** table at ``[128..247]``. The old H1 mode (cmd 1 "profile
update" carrying a full 256-byte config payload) is GONE: it was REFUTED on
device (remap/DISCOVERY.md, operator run 3 — cmd 1 is ChangeProfile and the
firmware ignores everything past byte [3]).

DSH wrote this file; DSH never runs it — it needs /dev/hidraw access, which only
exists on the rig. The OPERATOR runs it and pastes the output into
remap/SMOKE.md.

Safety model (PLAN.md HARD SAFETY RULES):
  * Feature report ID 7 through StockGmmkProBackend only (fcntl HIDIOCS/CGFEATURE
    on the vendor hidraw node). Never pyusb, never DFU/bootloader, never
    detaching the kernel driver.
  * The read path (GET mirror, cmd 129) is PROVEN side-effect-free (39 identical
    responses in remap/probe_results.jsonl) and is used for every before/after
    observation. The 120-byte codes table is located with
    ``backend.locate_codes_table()`` — it handles the live echo framing
    (0x07 report ID echoed at byte 0 -> table at 0x81) and the payload-only
    capture shape (table at 0x80) alike.
  * profile/layer for a write are taken FROM THE MIRROR SELECTORS of the fresh
    read, not from a guess; the write is refused if they are unparsable.
  * Exactly ONE SET per run, sent after the operator types WRITE, never retried.
    The bytes shown at the gate are the bytes sent; the run then re-reads the
    mirror and byte-compares the whole table.
  * Every transaction is spaced >= 0.35 s.
  * Without ``--run`` nothing is opened: default mode refuses; ``--dry-run``
    builds the exact request on top of the captured mirror
    (tests/fixtures/mirror_profile11.bin) and prints it, touching no device
    node at all.
  * DANGER_SLOTS (no confirmed keycap on this unit) are refused for ``--key``.

Modes (pick one):
  --matrix-write            Re-send the table you just READ, unchanged. The
                            safest first touch of the write path: the payload is
                            the current mirror, so a PASS proves the channel and
                            a FAIL changes nothing semantically.
  --matrix-write --key N --to HEX
                            Same, with ONE slot patched (physical check: press
                            key N, it must behave like the new scancode).
  --revert-matrix           Write the packaged factory defaults (codes =
                            data/default_keymap.bin, types = data/default_types.bin).
  --factory-init            Virgin-buffer recovery after an official factory
                            reset (remap/DISCOVERY.md operator run 2): REFUSED
                            unless the whole located table slice reads 0x00, then
                            writes the factory defaults as cmd 0x03.

Recommended candidate (PLAN.md): slot 13 (PrtSc) -> 0x68 (F13: no OS-side
effect, easily checked with a key tester). NEVER smoke slots 14 / 72 / 92
(matrix artifacts / unconfirmed knob position — remap/KEYMAP_MAP.md).

Usage (operator, on the rig):
  python remap/smoke_setkeymap.py --dry-run --matrix-write
  python remap/smoke_setkeymap.py --dry-run --matrix-write --key 13 --to 0x68
  python remap/smoke_setkeymap.py --run     --matrix-write --key 13 --to 0x68
  python remap/smoke_setkeymap.py --run     --revert-matrix
  python remap/smoke_setkeymap.py --dry-run --factory-init
  python remap/smoke_setkeymap.py --run     --factory-init

Artifacts: one JSON line per invocation appended to remap/smoke_results.jsonl.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from open_glorious_core.backend import (  # noqa: E402
    CMD_GET_CONFIG,
    CMD_KEY_MATRIX,
    KEYMAP_CODES_OFFSET,
    KEYMAP_SLOTS,
    KEYMAP_TYPES_OFFSET,
    BackendError,
    KEYMAP_SLOT_LABELS,
    StockGmmkProBackend,
    locate_codes_table,
)

REMAPPING_DIR = ROOT / "remap"
JSONL_PATH = REMAPPING_DIR / "smoke_results.jsonl"
CAPTURE_PATH = ROOT / "tests" / "fixtures" / "mirror_profile11.bin"

MIN_INTERVAL_S = 0.35  # >= 0.3s between every device transaction
DANGER_SLOTS = (14, 72, 92)  # no confirmed keycap — never smoke these
DEFAULT_TARGET = 0x68  # F13: harmless, key-testable

_last_txn_monotonic: float | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def space_out() -> None:
    global _last_txn_monotonic
    if _last_txn_monotonic is not None:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_txn_monotonic)
        if wait > 0:
            time.sleep(wait)
    _last_txn_monotonic = time.monotonic()


def byte_arg(text: str) -> int:
    value = int(text, 16) if text.lower().startswith("0x") else int(text, 10)
    if not 0 <= value <= 0xFF:
        raise argparse.ArgumentTypeError(f"scancode must be 0x00-0xFF, got {text!r}")
    return value


def dump(label: str, data: bytes | bytearray) -> None:
    print(f"  {label} ({len(data)} B):")
    for i in range(0, len(data), 16):
        print(f"    {i:03X}: {' '.join(f'{b:02x}' for b in data[i : i + 16])}")


def record(entry: dict) -> None:
    with JSONL_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def codes_slice(buf: bytes | bytearray) -> tuple[int, bytes]:
    """(offset, 120-byte codes table) of a mirror response, echo-agnostic."""
    offset = locate_codes_table(buf)
    return offset, bytes(buf[offset : offset + KEYMAP_SLOTS])


def read_mirror_raw(backend: StockGmmkProBackend, profile: int, layer: int) -> bytearray:
    """Proven read path: one cmd-129 GET of the config mirror, spaced."""
    space_out()
    request = backend._make_request(CMD_GET_CONFIG)
    request[2] = profile
    request[3] = layer
    return backend._exchange_feature_report(request)


def send_matrix_and_verify(
    backend: StockGmmkProBackend,
    payload: bytearray,
    want_codes: bytes,
    profile: int,
    layer: int,
    result: dict,
) -> bool:
    """Send the cmd-0x03 payload EXACTLY ONCE, re-read the mirror, byte-compare
    the whole codes table, and report the mirror selectors."""
    print("\n[3/4] SET cmd 0x03 KeyMatrix (exact-once, no retry)")
    space_out()
    backend._send_feature_report(payload)

    print("\n[4/4] post-read (proven GET path; full table byte-compare + selectors)")
    after = read_mirror_raw(backend, profile, layer)
    dump("GET response (config mirror)", after)
    offset, after_table = codes_slice(after)
    dump(f"keymap table after (offset 0x{offset:02X})", after_table)
    selectors = StockGmmkProBackend._parse_profile_layer_response(after)
    selectors_ok = selectors == (profile, layer)
    print(
        f"  mirror selectors: {None if selectors is None else f'{selectors[0]}/{selectors[1]}'}"
        f" (want {profile}/{layer})"
    )
    ok = after_table == bytes(want_codes) and selectors_ok
    result["after_slice_hex"] = after_table.hex()
    result["payload_hex"] = payload.hex()
    result["table_offset"] = offset
    result["selectors_ok"] = selectors_ok
    result["verdict"] = "PASS" if ok else "FAIL"
    print(
        f"\n{'PASS' if ok else 'FAIL'}: read-back table {'equals' if ok else 'DOES NOT EQUAL'} the"
        f" written table and the mirror {'shows' if ok else 'DOES NOT SHOW'} profile/layer"
        f" {profile}/{layer}."
    )
    if not ok:
        print("Do NOT chain more writes. The device state is UNCONFIRMED.")
        print("Paste this output into remap/SMOKE.md.")
    return ok


# ---------------------------------------------------------------------------
# --matrix-write / --revert-matrix
# ---------------------------------------------------------------------------


def run_matrix(args: argparse.Namespace) -> int:
    """Read mirror -> take selectors FROM THE MIRROR -> cmd 0x03 -> send once ->
    GET -> verify the table."""
    revert = bool(args.revert_matrix)
    default_codes = StockGmmkProBackend._default_keymap_table()
    default_types = StockGmmkProBackend._default_types()

    if revert:
        slot = None
        print(f"ts={utc_now()}  REVERT-MATRIX: write the packaged factory defaults (codes + types)")
    else:
        slot = args.key
        label = "" if slot is None else f" ({KEYMAP_SLOT_LABELS.get(slot, f'unlabeled slot {slot}')})"
        target = "" if slot is None else f" -> 0x{args.to:02X}"
        print(
            f"ts={utc_now()}  MATRIX-WRITE: re-send the mirrored table{label}{target}"
            + ("" if slot is not None else " (identity write: the safest first probe)")
        )

    # ------------------------------------------------------------------ dry-run
    if args.dry_run:
        base = CAPTURE_PATH.read_bytes()  # real captured mirror; no device
        backend = StockGmmkProBackend(hidraw=None)  # unit mode; opens nothing
        selectors = StockGmmkProBackend._parse_profile_layer_response(base)
        offset, table = codes_slice(base)
        codes = bytes(default_codes)
        if not revert:
            codes = bytearray(table)
            if slot is not None:
                codes[slot] = args.to
        payload = backend._build_key_matrix_report(
            (selectors or (args.profile, args.layer))[0],
            (selectors or (args.profile, args.layer))[1],
            bytes(codes),
            default_types,
        )
        print("\n--dry-run: built on the CAPTURED mirror (a live --run rebuilds it on the")
        print("freshly-read one); the profile/layer bytes come from the mirror selectors:")
        dump("planned SET report (cmd 0x03)", payload)
        dump("types array in payload [8..127]", payload[KEYMAP_TYPES_OFFSET : KEYMAP_TYPES_OFFSET + KEYMAP_SLOTS])
        dump("codes table in payload [128..247]", payload[KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS])
        print(f"\ncodes table offset in the captured mirror: 0x{offset:02X}")
        print("Nothing was sent (no device node opened).")
        return 0

    # --------------------------------------------------------------------- live
    backend = StockGmmkProBackend(hidraw=args.hidraw) if args.hidraw else StockGmmkProBackend()
    if not backend.is_available():
        print("No GMMK Pro stock hidraw node found:\n  " + backend.install_hint())
        return 3
    print(f"device: {backend.hidraw}  (backend={backend.name})")

    result: dict = {
        "ts_utc": utc_now(),
        "kind": "smoke-setkeymap",
        "mode": "revert-matrix" if revert else "matrix-write",
        "hidraw": str(backend.hidraw),
        "profile": None,
        "layer": None,
        "slot": slot,
        "target": "factory-defaults" if revert else (f"0x{args.to:02X}" if slot is not None else "identity"),
        "revert": revert,
        "verdict": None,
        "error": None,
    }
    try:
        print("\n[1/4] pre-flight mirror read (proven GET path) + selector harvest")
        before = read_mirror_raw(backend, args.profile, args.layer)
        dump("GET response (config mirror)", before)
        offset, before_table = codes_slice(before)
        dump(f"keymap table before (offset 0x{offset:02X})", before_table)
        selectors = StockGmmkProBackend._parse_profile_layer_response(before)
        if selectors is None:
            print("\nREFUSING: the mirror carries no parsable profile/layer selectors, so the")
            print("write target is unknown. Nothing was sent.")
            result["verdict"] = "refused-no-selectors"
            record(result)
            return 2
        profile, layer = selectors
        result["profile"], result["layer"] = profile, layer
        print(f"  writing profile {profile} / layer {layer} (taken from the MIRROR, not the CLI)")
        if before_table == bytes(default_codes):
            print("  (device currently holds the FACTORY keymap)")

        codes = bytearray(before_table)
        if revert:
            codes = bytearray(default_codes)
        elif slot is not None:
            codes[slot] = args.to
        payload = backend._build_key_matrix_report(profile, layer, bytes(codes), default_types)

        print("\n[2/4] planned write (cmd 0x03 KeyMatrix; these exact bytes are what gets sent)")
        dump("SET report (cmd 0x03)", payload)
        dump("types array [8..127]", payload[KEYMAP_TYPES_OFFSET : KEYMAP_TYPES_OFFSET + KEYMAP_SLOTS])
        dump("codes table [128..247]", payload[KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS])

        answer = input("\nType WRITE to send this ONE matrix write to the device: ").strip()
        if answer != "WRITE":
            print("Aborted by operator; nothing was sent.")
            result["verdict"] = "aborted"
            record(result)
            return 2

        result["before_slice_hex"] = before_table.hex()
        ok = send_matrix_and_verify(backend, payload, bytes(codes), profile, layer, result)
        if ok:
            if revert:
                print("Factory defaults restored (codes + vendor types array). Paste this output")
                print("into remap/SMOKE.md.")
            elif slot is not None:
                print(f"Physical check: press the physical '{KEYMAP_SLOT_LABELS.get(slot, slot)}' key —")
                print(f"it must now behave as 0x{args.to:02X}. When confirmed, revert:")
                print("  python remap/smoke_setkeymap.py --run --revert-matrix")
            else:
                print("Identity write confirmed: the write path round-trips the mirrored table.")
                print("Next step is the one-key probe:  --run --matrix-write --key 13 --to 0x68")
        record(result)
        return 0 if ok else 1
    except BackendError as exc:
        result["verdict"] = "FAIL"
        result["error"] = str(exc)
        print(f"\nFAIL: BackendError: {exc}")
        print("The device state is UNCONFIRMED. Re-read (plain GET) — do not blind-retry writes.")
        record(result)
        return 1


# ---------------------------------------------------------------------------
# --factory-init — virgin-buffer recovery
# ---------------------------------------------------------------------------


def run_factory_init(args: argparse.Namespace) -> int:
    """Virgin-buffer recovery after an official factory reset.

    Context (remap/DISCOVERY.md, operator run 2): the board was factory-reset
    with the official FN+ESC+F1+F3+F5 hotkey, and the vendor channel serves a
    VIRGIN never-configured config buffer — the whole codes table reads 0x00 and
    the header reads 00 00 00 00, so the normal path (which harvests selectors
    from the mirror) has nothing to trust.

    Gate: REFUSE unless the located codes table is ALL 0x00 — any nonzero byte
    means the buffer is not virgin and a factory-image write could clobber real
    user data, so nothing is sent.

    Recovery: ONE cmd-0x03 KeyMatrix write carrying the packaged factory dumps —
    codes = data/default_keymap.bin (the factory capture table), types =
    data/default_types.bin (the vendor Buttoninfo_Default types array). Selectors
    come from --profile/--layer (a virgin mirror has none). Post-write: re-GET,
    byte-compare the FULL table and check the mirror selectors.
    """
    default_codes = StockGmmkProBackend._default_keymap_table()
    default_types = StockGmmkProBackend._default_types()
    print(f"ts={utc_now()}  FACTORY-INIT: write factory defaults (profile {args.profile} layer {args.layer})")

    if args.dry_run:
        backend = StockGmmkProBackend(hidraw=None)  # unit mode; opens nothing
        payload = backend._build_key_matrix_report(
            args.profile, args.layer, bytes(default_codes), default_types
        )
        print("\n--dry-run: fixed factory-default bytes — a live --run sends this SAME payload")
        print("after the virgin pre-flight passes:")
        dump("planned SET report (cmd 0x03)", payload)
        dump("codes table in payload [128..247]", payload[KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS])
        print("\nNothing was sent (no device node opened).")
        return 0

    backend = StockGmmkProBackend(hidraw=args.hidraw) if args.hidraw else StockGmmkProBackend()
    if not backend.is_available():
        print("No GMMK Pro stock hidraw node found:\n  " + backend.install_hint())
        return 3
    print(f"device: {backend.hidraw}  (backend={backend.name})")

    result: dict = {
        "ts_utc": utc_now(),
        "kind": "smoke-setkeymap",
        "mode": "factory-init",
        "hidraw": str(backend.hidraw),
        "profile": args.profile,
        "layer": args.layer,
        "slot": None,
        "target": "factory-defaults",
        "revert": False,
        "verdict": None,
        "error": None,
    }
    try:
        print("\n[1/4] pre-flight virgin check (raw cmd-129 GET — proven read path)")
        before = read_mirror_raw(backend, args.profile, args.layer)
        dump("GET response (config mirror)", before)
        offset, before_table = codes_slice(before)
        dump(f"keymap table before (offset 0x{offset:02X})", before_table)
        print(f"  header bytes [0..3]: {' '.join(f'{b:02X}' for b in before[:4])}")
        nonzero = [slot for slot, byte in enumerate(before_table) if byte != 0x00]
        if nonzero:
            first = nonzero[0]
            print(f"\nREFUSING: the keymap table is NOT virgin — {len(nonzero)} of {KEYMAP_SLOTS} bytes")
            print(f"are nonzero (first nonzero byte at slot {first}: 0x{before_table[first]:02X}).")
            print("--factory-init handles ONLY the all-zero state an official factory reset leaves.")
            print("For a configured table use --revert-matrix (it shows the payload first).")
            print("Nothing was sent.")
            result["verdict"] = "refused-non-virgin"
            record(result)
            return 2
        print(f"  virgin confirmed: the entire table slice at 0x{offset:02X} is 0x00")

        payload = backend._build_key_matrix_report(
            args.profile, args.layer, bytes(default_codes), default_types
        )
        print("\n[2/4] planned write (cmd 0x03 KeyMatrix, factory defaults)")
        dump("SET report (cmd 0x03)", payload)

        answer = input("\nType WRITE to send this factory image to the device: ").strip()
        if answer != "WRITE":
            print("Aborted by operator; nothing was sent.")
            result["verdict"] = "aborted"
            record(result)
            return 2

        result["before_slice_hex"] = before_table.hex()
        ok = send_matrix_and_verify(
            backend, payload, bytes(default_codes), args.profile, args.layer, result
        )
        if ok:
            print("Factory defaults restored: table byte-identical to the shipped factory dump,")
            print(f"selectors show ({args.profile},{args.layer}). The normal matrix smoke can run.")
        record(result)
        return 0 if ok else 1
    except BackendError as exc:
        result["verdict"] = "FAIL"
        result["error"] = str(exc)
        print(f"\nFAIL: BackendError: {exc}")
        print("The device state is UNCONFIRMED. Re-read (plain GET) — do not blind-retry writes.")
        record(result)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--key", type=int, default=None, help="with --matrix-write: patch ONE slot 0-119 (remap/KEYMAP_MAP.md)")
    parser.add_argument("--to", type=byte_arg, default=None, help=f"target scancode for --key (default 0x{DEFAULT_TARGET:02X} = F13, harmless)")
    parser.add_argument("--matrix-write", action="store_true", help="re-send the mirrored table (optionally with one slot patched) via cmd 0x03")
    parser.add_argument("--revert-matrix", action="store_true", help="write the packaged factory defaults (codes + types) via cmd 0x03")
    parser.add_argument("--factory-init", action="store_true", help="virgin-buffer recovery: write factory defaults onto an all-zero (factory-reset) table")
    parser.add_argument("--profile", type=int, default=1, help="--factory-init target profile (a virgin mirror has no selectors)")
    parser.add_argument("--layer", type=int, default=1, help="--factory-init target layer")
    parser.add_argument("--hidraw", type=Path, default=None, help="explicit hidraw node (default: auto-detect)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true", help="actually talk to the device")
    mode.add_argument("--dry-run", action="store_true", help="print the exact planned request from the captured mirror; touch nothing")
    args = parser.parse_args()

    if not args.run and not args.dry_run:
        print("Refusing to do anything: pass --run (device!) or --dry-run (no device).")
        return 2

    modes = [name for name in ("matrix_write", "revert_matrix", "factory_init") if getattr(args, name)]
    if len(modes) != 1:
        print("Pick exactly one mode: --matrix-write, --revert-matrix or --factory-init.")
        return 2
    if len(modes) == 1 and args.revert_matrix and args.key is not None:
        print("--revert-matrix writes the packaged factory defaults: --key must not be combined with it.")
        return 2
    if args.factory_init and (args.key is not None or args.to is not None):
        print("--factory-init writes the whole factory image: --key/--to must not be combined with it.")
        return 2
    if args.to is not None and args.key is None:
        print("--to needs --key (which slot to patch).")
        return 2
    if args.key is not None:
        if not args.matrix_write:
            print("--key is only meaningful with --matrix-write.")
            return 2
        if not 0 <= args.key < KEYMAP_SLOTS:
            print(f"--key must be 0-{KEYMAP_SLOTS - 1} (see remap/KEYMAP_MAP.md).")
            return 2
        if args.key in DANGER_SLOTS:
            print(f"REFUSING slot {args.key}: no confirmed keycap on this unit (see KEYMAP_MAP.md notes).")
            return 2
        if args.to is None:
            args.to = DEFAULT_TARGET

    if args.factory_init:
        return run_factory_init(args)
    return run_matrix(args)


if __name__ == "__main__":
    sys.exit(main())
