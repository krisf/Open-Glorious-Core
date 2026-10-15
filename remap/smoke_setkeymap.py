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

Recommended candidate (PLAN.md): a rarely-used key -> a harmless scancode.
  remap/KEYMAP_MAP.md is the slot table. Good candidates: slot 13 (PrtSc) with
  --to 0x68 (F13: no OS-side effect, easily checked with a key tester).
  NEVER smoke-test slots 14 / 72 / 92 (matrix artifacts / unconfirmed knob
  position — see KEYMAP_MAP.md notes).

Usage (operator, on the rig):
  python remap/smoke_setkeymap.py --dry-run --key 13 --to 0x68   # inspect bytes
  python remap/smoke_setkeymap.py --run     --key 13 --to 0x68   # remap 1 key
  python remap/smoke_setkeymap.py --run     --key 13 --revert    # put it back

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--key", type=int, required=True, help="keymap slot 0-119 (remap/KEYMAP_MAP.md)")
    parser.add_argument("--to", type=byte_arg, default=0x68, help="target scancode (default 0x68 = F13, harmless)")
    parser.add_argument("--revert", action="store_true", help="write the FACTORY byte for --key back (from packaged default_keymap.bin)")
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
    if not 0 <= args.key < KEYMAP_SLOTS:
        print(f"--key must be 0-{KEYMAP_SLOTS - 1} (see remap/KEYMAP_MAP.md).")
        return 2
    if args.key in DANGER_SLOTS:
        print(f"REFUSING slot {args.key}: no confirmed keycap on this unit (see KEYMAP_MAP.md notes).")
        return 2

    factory_table = StockGmmkProBackend._default_keymap_table()
    target = factory_table[args.key] if args.revert else args.to
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
