#!/usr/bin/env python3
"""GMMK Pro keymap micro-write smoke test — Phase 2, OPERATOR-RUN ONLY.

This is the FIRST-TOUCH tool for the unverified keymap WRITE path (hypothesis H1
in remap/DISCOVERY.md: profile-update cmd 1 carrying the full 256-byte config
payload persists it). DSH wrote it; DSH never runs it — it needs /dev/hidraw
access, which only exists on the rig. The OPERATOR runs it, then pastes the
output into remap/SMOKE.md (gate G3 in PLAN.md).

Safety model (PLAN.md HARD SAFETY RULES):
  * Feature report ID 7 via StockGmmkProBackend only (fcntl HIDIOCS/CGFEATURE on
    the vendor hidraw node). Never pyusb, never DFU/bootloader, never detaching
    the kernel driver.
  * The read path (GET mirror, cmd 129 request) is PROVEN side-effect-free
    (39 identical responses in remap/probe_results.jsonl) and is used for every
    before/after observation.
  * Exactly ONE key slot is ever touched, and --revert puts the factory byte
    (from the packaged capture data/default_keymap.bin) back. No batch writes.
  * set_keymap() sends the write EXACTLY ONCE (no retry) and re-reads + byte-
    compares the full keymap slice; any mismatch raises BackendError and this
    script immediately suggests --revert.
  * Every transaction is spaced >= 0.35 s.
  * Without --run nothing is opened: default mode refuses; --dry-run builds the
    exact request on top of the factory capture (tests/fixtures/mirror_profile11.bin)
    and prints it, touching no device node at all.
  * --factory-init (virgin-buffer recovery, see run_factory_init) is the ONE
    mode that writes the WHOLE table, and only onto a buffer proven virgin
    (entire 0x80-0xF7 slice reads 0x00). It carries the factory capture image,
    so the DANGER_SLOTS guard does not apply.

Recommended candidate (PLAN.md): a rarely-used key -> a harmless scancode.
  remap/KEYMAP_MAP.md is the slot table. Good candidates: slot 13 (PrtSc) with
  --to 0x68 (F13: no OS-side effect, easily checked with a key tester).
  NEVER smoke-test slots 14 / 72 / 92 (matrix artifacts / unconfirmed knob
  position — see KEYMAP_MAP.md notes).

Usage (operator, on the rig):
  python remap/smoke_setkeymap.py --dry-run --key 13 --to 0x68   # inspect bytes
  python remap/smoke_setkeymap.py --run     --key 13 --to 0x68   # remap 1 key
  python remap/smoke_setkeymap.py --run     --key 13 --revert    # put it back
  python remap/smoke_setkeymap.py --dry-run --factory-init       # inspect image write
  python remap/smoke_setkeymap.py --run     --factory-init       # virgin-buffer recovery

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
    KEYMAP_OFFSET,
    KEYMAP_SLOTS,
    BackendError,
    KeymapEntry,
    KEYMAP_SLOT_LABELS,
    StockGmmkProBackend,
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


def keymap_slice(buf: bytes | bytearray) -> bytes:
    return bytes(buf[KEYMAP_OFFSET : KEYMAP_OFFSET + KEYMAP_SLOTS])


def record(entry: dict) -> None:
    with JSONL_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def run_factory_init(args: argparse.Namespace) -> int:
    """--factory-init: virgin-buffer recovery after an official factory reset.

    Context (remap/DISCOVERY.md, operator run 2): the board was factory-reset
    with the official FN+ESC+F1+F3+F5 hotkey, and the vendor channel now serves
    a VIRGIN never-configured config buffer — the ENTIRE 0x80-0xF7 keymap table
    reads 0x00 and the header reads 00 00 00 00. The normal pre-flight guard
    (which requires the factory byte at the active slot) correctly refuses to
    write there; this mode handles EXACTLY that state.

    Gate: REFUSE unless the whole 0x80-0xF7 table slice is 0x00 — any nonzero
    byte ANYWHERE in the table means the buffer is not virgin and a factory
    image write could clobber real user data, so nothing is sent.

    Recovery: one cmd-1 full 256-byte write carrying the FACTORY IMAGE — the
    packaged factory keymap table (src/open_glorious_core/data/default_keymap.bin)
    embedded in the captured configured mirror (tests/fixtures/mirror_profile11.bin:
    header (1,1) at payload bytes [0]/[1], brightness 0x14), built by the same
    _build_keymap_write_payload as the single-key path. The payload is the
    factory image itself, so the DANGER_SLOTS guard does not apply here.
    Post-write: re-GET, byte-compare the FULL table slice, and verify the
    header shows (profile, layer).
    """
    factory_table = StockGmmkProBackend._default_keymap_table()
    capture = CAPTURE_PATH.read_bytes()
    print(f"ts={utc_now()}  FACTORY-INIT: write factory image (profile {args.profile} layer {args.layer})")

    # ------------------------------------------------------------------ dry-run
    if args.dry_run:
        backend = StockGmmkProBackend(hidraw=None)  # unit mode; opens nothing
        payload = backend._build_keymap_write_payload(bytearray(capture), factory_table, args.profile, args.layer)
        print("\n--dry-run: fixed factory-image bytes — a live --run sends this SAME payload")
        print("(base = the factory capture, not the live mirror) after the virgin pre-flight passes:")
        dump("planned SET report", payload)
        dump("keymap slice in payload", payload[0x82 : 0x82 + KEYMAP_SLOTS])
        print("\nThis is H1 (UNVERIFIED hypothesis). Nothing was sent.")
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
        "mode": "factory-init",
        "hidraw": str(backend.hidraw),
        "profile": args.profile,
        "layer": args.layer,
        "slot": None,
        "target": "factory-image",
        "revert": False,
        "verdict": None,
        "error": None,
    }
    try:
        print("\n[1/4] pre-flight virgin check (raw cmd-129 GET — proven read path)")
        space_out()
        request = backend._make_request(129)
        request[2] = args.profile
        request[3] = args.layer
        before = backend._exchange_feature_report(request)
        before_table = keymap_slice(before)
        dump("GET response (config mirror)", before)
        dump("keymap slice before", before_table)
        print(f"  header bytes [0]/[1]: {before[0]:02X} {before[1]:02X}")
        nonzero = [slot for slot, byte in enumerate(before_table) if byte != 0x00]
        if nonzero:
            first = nonzero[0]
            print(f"\nREFUSING: the keymap table is NOT virgin — {len(nonzero)} of {KEYMAP_SLOTS} bytes")
            print(f"are nonzero (first nonzero byte at slot {first}: 0x{before_table[first]:02X}).")
            print("--factory-init handles ONLY the all-zero state an official factory reset leaves.")
            print("Nothing was sent.")
            result["verdict"] = "refused-non-virgin"
            record(result)
            return 2
        print("  virgin confirmed: the entire 0x80-0xF7 table slice is 0x00")

        payload = backend._build_keymap_write_payload(bytearray(capture), factory_table, args.profile, args.layer)
        print("\n[2/4] planned write (cmd 1, full factory image)")
        dump("SET report (cmd 1, factory image = H1)", payload)

        answer = input("\nType WRITE to send this factory image to the device: ").strip()
        if answer != "WRITE":
            print("Aborted by operator; nothing was sent.")
            result["verdict"] = "aborted"
            record(result)
            return 2

        print("\n[3/4] SET (exact-once, no retry)")
        space_out()
        backend._send_feature_report(payload)

        print("\n[4/4] post-read (proven GET path; full table byte-compare + header check)")
        space_out()
        after = backend._read_config_mirror(args.profile, args.layer)
        after_table = keymap_slice(after)
        dump("GET response (config mirror)", after)
        dump("keymap slice after", after_table)
        header_ok = (after[0], after[1]) == (args.profile, args.layer)
        print(f"  header bytes [0]/[1]: {after[0]:02X} {after[1]:02X} (want {args.profile:02X} {args.layer:02X})")
        ok = bytes(after_table) == bytes(factory_table) and header_ok
        result["before_slice_hex"] = before_table.hex()
        result["after_slice_hex"] = after_table.hex()
        result["payload_hex"] = payload.hex()
        result["header_ok"] = header_ok
        result["verdict"] = "PASS" if ok else "FAIL"
        print(
            f"\n{'PASS' if ok else 'FAIL'}: read-back table {'equals' if ok else 'DOES NOT EQUAL'} the factory"
            f" image and the header {'shows' if ok else 'DOES NOT SHOW'}"
            f" ({args.profile},{args.layer})."
        )
        if not ok:
            print("Do NOT chain more writes. Re-read (plain GET); the device state is UNCONFIRMED.")
            print("Paste this output into remap/SMOKE.md.")
        else:
            print("Factory image restored: table byte-identical to the factory capture, header shows")
            print(f"({args.profile},{args.layer}). The normal single-key smoke can run again.")
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
    parser.add_argument("--key", type=int, default=None, help="keymap slot 0-119 (remap/KEYMAP_MAP.md)")
    parser.add_argument("--to", type=byte_arg, default=None, help=f"target scancode (default 0x{DEFAULT_TARGET:02X} = F13, harmless)")
    parser.add_argument("--revert", action="store_true", help="write the FACTORY byte for --key back (from packaged default_keymap.bin)")
    parser.add_argument("--factory-init", action="store_true", help="virgin-buffer recovery: write the factory capture image onto an all-zero (factory-reset) table")
    parser.add_argument("--profile", type=int, default=1)
    parser.add_argument("--layer", type=int, default=1)
    parser.add_argument("--hidraw", type=Path, default=None, help="explicit hidraw node (default: auto-detect)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true", help="actually talk to the device")
    mode.add_argument("--dry-run", action="store_true", help="print the exact planned request from the factory capture; touch nothing")
    args = parser.parse_args()

    if not args.run and not args.dry_run:
        print("Refusing to do anything: pass --run (device!) or --dry-run (no device).")
        return 2

    if args.factory_init:
        if args.key is not None or args.revert:
            print("--factory-init writes the WHOLE factory image: --key/--revert must not be combined with it.")
            return 2
        if args.to is not None:
            print("--factory-init writes the WHOLE factory image: --to must not be combined with it.")
            return 2
        return run_factory_init(args)

    if args.key is None:
        print("--key is required (0-119, see remap/KEYMAP_MAP.md). For the virgin-buffer")
        print("recovery after an official factory reset use --factory-init.")
        return 2
    if not 0 <= args.key < KEYMAP_SLOTS:
        print(f"--key must be 0-{KEYMAP_SLOTS - 1} (see remap/KEYMAP_MAP.md).")
        return 2
    if args.key in DANGER_SLOTS:
        print(f"REFUSING slot {args.key}: no confirmed keycap on this unit (see KEYMAP_MAP.md notes).")
        return 2

    factory_table = StockGmmkProBackend._default_keymap_table()
    target = factory_table[args.key] if args.revert else (DEFAULT_TARGET if args.to is None else args.to)
    label = KEYMAP_SLOT_LABELS.get(args.key, f"unlabeled slot {args.key}")
    print(f"ts={utc_now()}  slot {args.key} ({label})  ->  0x{target:02X}{'  [REVERT to factory]' if args.revert else ''}")

    # ------------------------------------------------------------------ dry-run
    if args.dry_run:
        base = CAPTURE_PATH.read_bytes()
        backend = StockGmmkProBackend(hidraw=None)  # unit mode; opens nothing
        table = bytearray(keymap_slice(base))
        table[args.key] = target
        payload = backend._build_keymap_write_payload(bytearray(base), bytes(table), args.profile, args.layer)
        print("\n--dry-run: request built on the FACTORY CAPTURE (a live --run rebuilds it on the freshly-read mirror):")
        dump("planned SET report", payload)
        dump("keymap slice in payload", payload[0x82 : 0x82 + KEYMAP_SLOTS])
        print(f"\nThis is H1 (UNVERIFIED hypothesis). Nothing was sent.")
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
        "hidraw": str(backend.hidraw),
        "profile": args.profile,
        "layer": args.layer,
        "slot": args.key,
        "target": f"0x{target:02X}",
        "revert": args.revert,
        "verdict": None,
        "error": None,
    }
    try:
        print("\n[1/4] pre-flight mirror read (proven GET path)")
        space_out()
        before = backend._read_config_mirror(args.profile, args.layer)
        before_table = keymap_slice(before)
        dump("GET response (config mirror)", before)
        dump("keymap slice before", before_table)
        print(f"  slot {args.key}: 0x{before_table[args.key]:02X}")
        if before_table == factory_table:
            print("  (device currently holds the FACTORY keymap)")

        table = bytearray(before_table)
        table[args.key] = target
        payload = backend._build_keymap_write_payload(before, bytes(table), args.profile, args.layer)
        print("\n[2/4] planned write (byte-identical to what set_keymap sends)")
        dump("SET report (cmd 1, full payload = H1)", payload)

        answer = input("\nType WRITE to send this ONE key change to the device: ").strip()
        if answer != "WRITE":
            print("Aborted by operator; nothing was sent.")
            result["verdict"] = "aborted"
            record(result)
            return 2

        print("\n[3/4] set_keymap (single SET, exact-once, then read-back verify)")
        space_out()
        print("  " + backend.set_keymap([KeymapEntry(slot=args.key, scancode=target)], args.profile, args.layer))

        print("\n[4/4] post-read (proven GET path)")
        space_out()
        after = backend._read_config_mirror(args.profile, args.layer)
        after_table = keymap_slice(after)
        dump("GET response (config mirror)", after)
        dump("keymap slice after", after_table)
        print(f"  slot {args.key}: 0x{after_table[args.key]:02X}")

        ok = bytes(after_table) == bytes(table)
        result["before_slice_hex"] = before_table.hex()
        result["after_slice_hex"] = after_table.hex()
        result["payload_hex"] = payload.hex()
        result["verdict"] = "PASS" if ok else "FAIL"
        print(f"\n{'PASS' if ok else 'FAIL'}: read-back {'equals' if ok else 'DOES NOT EQUAL'} the requested table.")
        if not ok:
            print("Do NOT chain more writes. Re-read and consider: --run --key {} --revert".format(args.key))
        else:
            print(f"Physical check: press the physical '{label}' key — it must now behave as 0x{target:02X}"
                  f"{' (factory behavior restored)' if args.revert else ''}.")
            if not args.revert:
                print("When confirmed, revert:  python remap/smoke_setkeymap.py --run --key {} --revert".format(args.key))
            else:
                print("If the key works normally again, gate G3 physical check passed — paste this output into remap/SMOKE.md.")
        record(result)
        return 0 if ok else 1
    except BackendError as exc:
        result["verdict"] = "FAIL"
        result["error"] = str(exc)
        print(f"\nFAIL: BackendError: {exc}")
        print("The device state is UNCONFIRMED. Re-run a read (--dry-run shows the plan; a plain")
        print("GET via the app or --run --key {} --revert after re-reading) — do not blind-retry writes.".format(args.key))
        record(result)
        return 1


if __name__ == "__main__":
    sys.exit(main())
