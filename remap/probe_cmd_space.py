#!/usr/bin/env python3
"""GMMK Pro (VID 320F / PID 5092) stock-firmware command-space sweep — Phase 1, READ-ONLY.

PLAN.md Phase 1 tooling. Sends FEATURE report ID 7 requests with candidate command
bytes through the ONLY sanctioned device path: StockGmmkProBackend._exchange_feature_report()
(fcntl HIDIOCSFEATURE/HIDIOCGFEATURE ioctl on the vendor hidraw node). Never pyusb,
never kernel-driver detach, never DFU.

Safety model (HARD SAFETY RULES, PLAN.md + batch instructions):
  * cmd 129 (0x81, read profile/layer) is the ONLY known-safe read; it is used for
    the baseline and as a pre-flight health check before EVERY candidate probe.
  * Every non-129 command is treated as potentially state-changing: it is sent
    EXACTLY ONCE (one SET + one GET, no retry, ever).
  * Commands 1, 2, 7 and 129 are already mapped and are never swept as unknowns;
    1 and 2 and 7 are never sent at all in this phase.
  * If the health check fails at any point the sweep STOPS immediately and an
    explicit abort record is written (where the sweep stopped).
  * Responses whose payload is all-zero (device returned nothing for that command)
    get a JSONL line only; any non-zero response additionally gets its own
    remap/probe_0xNN.txt full hex dump.

Modes:
  --dry-run         print the plan; instantiate the backend device-free
                    (hidraw=None unit mode); touch NO device node at all.
  --list            print the candidate list and exit (no device).
  --run             actually talk to the device. Refuses without the flag.
  --baseline-only   run just the 3x cmd-129 baseline and stop.
  --limit N         probe at most N candidates this invocation (batching, gate G1).
  --skip-probed     skip commands already recorded as probed in probe_results.jsonl.
  --start-at 0xNN   resume the batch from a candidate (inclusive).

Batch 1 (operator-fixed scope): cmd bytes 0x00, 0x03-0x06, 0x08-0x1F
(0x00-0x1F minus known 1, 2, 7 = 29 candidates). Nothing at or above 0x20, and
never 129, in this batch.

Artifacts (all under remap/, UTC timestamps):
  probe_results.jsonl      one JSON line per exchange (baseline / probe / abort)
  probe_0x81_baseline.txt  baseline 129 response hex dump
  probe_0xNN.txt           full hex dump of every non-zero candidate response
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

from open_glorious_core.backend import BackendError, StockGmmkProBackend  # noqa: E402

REMAPPING_DIR = ROOT / "remap"
JSONL_PATH = REMAPPING_DIR / "probe_results.jsonl"
BASELINE_DUMP_PATH = REMAPPING_DIR / "probe_0x81_baseline.txt"

READ_CMD = 129  # 0x81 known-good read: active profile/layer query
KNOWN_COMMANDS = {1, 2, 7, 129}  # mapped already; never swept, 1/2/7 never sent here
MIN_INTERVAL_S = 0.35  # >= 0.3s between every device transaction
BASELINE_REPEATS = 3

# Batch 1: 0x00-0x1F minus known {1, 2, 7}  ->  29 candidates, ascending.
BATCH1 = tuple([0x00] + list(range(0x03, 0x07)) + list(range(0x08, 0x20)))
# Full Phase-1 plan space for LATER batches (after gate G1): 0x00..0x20 + 0x7E..0xFF
# minus known {1, 2, 7, 129}.
FULL_SPACE = tuple(
    c
    for c in list(range(0x00, 0x21)) + list(range(0x7E, 0x100))
    if c not in KNOWN_COMMANDS
)

_last_txn_monotonic: float | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def throttle() -> None:
    """Guarantee >= MIN_INTERVAL_S between device transactions."""
    global _last_txn_monotonic
    if _last_txn_monotonic is not None:
        wait = MIN_INTERVAL_S - (time.monotonic() - _last_txn_monotonic)
        if wait > 0:
            time.sleep(wait)
    _last_txn_monotonic = time.monotonic()


def hexdump(data: bytes) -> str:
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off : off + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{off:04x}  {hexs:<47}  |{ascii_}|")
    return "\n".join(lines)


def append_jsonl(record: dict) -> None:
    with JSONL_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        fh.flush()


def probed_cmds_from_jsonl() -> set[int]:
    done: set[int] = set()
    if not JSONL_PATH.exists():
        return done
    for line in JSONL_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("kind") == "probe" and isinstance(rec.get("cmd"), str):
            try:
                done.add(int(rec["cmd"], 16))
            except ValueError:
                pass
    return done


def meaningful_len(response: bytearray) -> int:
    """Index one past the last non-zero byte (0 if the whole payload is zero)."""
    for idx in range(len(response) - 1, 0, -1):
        if response[idx] != 0:
            return idx + 1
    return 0


def health_check(backend: StockGmmkProBackend) -> tuple[bool, tuple[int, int] | None, bytearray | None, str]:
    """Pre-flight: cmd-129 exchange must parse to a sane profile/layer.

    This is the known-safe read already issued by read_state(); it is the only
    exchange allowed to repeat. Updates backend.profile/layer when sane.
    """
    throttle()
    try:
        response = backend._exchange_feature_report(backend._make_request(READ_CMD))
    except (BackendError, OSError) as exc:
        return False, None, None, f"{type(exc).__name__}: {exc}"
    parsed = backend._parse_profile_layer_response(response)
    if parsed is None:
        return False, None, response, "cmd 129 response did not parse to a sane profile/layer"
    backend.profile, backend.layer = parsed
    return True, parsed, response, ""


def run_baseline(backend: StockGmmkProBackend) -> tuple[bytearray | None, bytearray]:
    """3x cmd-129 exchanges, all sane; save the first to probe_0x81_baseline.txt.

    Returns (baseline_response, zero_report). baseline_response is None (and an
    abort record is written) if any exchange fails or fails to parse.
    """
    responses: list[bytearray] = []
    for i in range(1, BASELINE_REPEATS + 1):
        ok, parsed, response, err = health_check(backend)
        if not ok or response is None:
            append_jsonl(
                {
                    "ts_utc": utc_now(),
                    "kind": "abort",
                    "stage": f"baseline_exchange_{i}_of_{BASELINE_REPEATS}",
                    "cmd": f"0x{READ_CMD:02x}",
                    "response": None,
                    "length": None,
                    "error": err,
                    "note": "sweep NOT started; no candidate command was ever sent",
                }
            )
            print(f"STOP: baseline exchange {i}/{BASELINE_REPEATS} failed: {err}", file=sys.stderr)
            return None, response if response is not None else bytearray(backend.REPORT_LENGTH)
        responses.append(response)
        append_jsonl(
            {
                "ts_utc": utc_now(),
                "kind": "baseline",
                "cmd": f"0x{READ_CMD:02x}",
                "request_hex": backend._make_request(READ_CMD).hex(),
                "response_hex": bytes(response).hex(),
                "length": len(response),
                "meaningful_len": meaningful_len(response),
                "parsed_profile_layer": f"{parsed[0]},{parsed[1]}",
            }
        )
        print(f"[{utc_now()}] baseline {i}/{BASELINE_REPEATS}: sane profile/layer = {parsed[0]},{parsed[1]}")

    identical = all(r == responses[0] for r in responses[1:])
    with BASELINE_DUMP_PATH.open("w", encoding="utf-8") as fh:
        fh.write(
            f"# cmd 129 (0x81) baseline — captured {utc_now()}\n"
            f"# device: {backend.hidraw} | active profile/layer: {backend.profile},{backend.layer}\n"
            f"# exchanges: {BASELINE_REPEATS}x, spaced >= {MIN_INTERVAL_S}s, all parsed sane; "
            f"byte-identical: {identical}\n"
            f"# request (256B, first 4 significant): 07 {READ_CMD:02x} {backend.profile:02x} {backend.layer:02x} ...\n"
            f"# response (256B) hex, continuous:\n{bytes(responses[0]).hex()}\n"
            f"# meaningful (up to last non-zero byte): {meaningful_len(responses[0])} bytes\n"
            f"# full hex dump of baseline response:\n{hexdump(bytes(responses[0]))}\n"
        )
    print(f"baseline saved -> {BASELINE_DUMP_PATH.relative_to(ROOT)} (identical across repeats: {identical})")
    return responses[0], bytearray(backend.REPORT_LENGTH)


def dump_probe(cmd: int, request: bytearray, response: bytearray, note: str) -> Path:
    path = REMAPPING_DIR / f"probe_0x{cmd:02x}.txt"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(
            f"# probe cmd 0x{cmd:02x} — captured {utc_now()}\n"
            f"# request (256B) hex: {bytes(request).hex()}\n"
            f"# response (256B) hex: {bytes(response).hex()}\n"
            f"# meaningful response bytes: {meaningful_len(response)}\n"
            f"# {note}\n"
            f"# full hex dump of response:\n{hexdump(bytes(response))}\n"
        )
    return path


def sweep(backend: StockGmmkProBackend, candidates: list[int]) -> None:
    aborted = False
    completed: list[str] = []
    for cmd in candidates:
        assert cmd not in KNOWN_COMMANDS, f"refusing to sweep known command 0x{cmd:02x}"
        assert cmd != READ_CMD and cmd <= 0x1F, f"batch-1 guard tripped on 0x{cmd:02x}"

        # Pre-flight: keyboard must be alive (sanctioned cmd-129 read) before each probe.
        ok, parsed, _, err = health_check(backend)
        if not ok:
            append_jsonl(
                {
                    "ts_utc": utc_now(),
                    "kind": "abort",
                    "stage": "precheck",
                    "next_cmd": f"0x{cmd:02x}",
                    "cmd": f"0x{READ_CMD:02x}",
                    "response": None,
                    "length": None,
                    "error": err,
                    "completed_cmds": completed,
                    "note": "health check failed BEFORE this candidate; the candidate was NOT sent",
                }
            )
            print(f"STOP: health check failed before cmd 0x{cmd:02x}: {err}", file=sys.stderr)
            aborted = True
            break

        request = bytearray(backend._make_request(cmd))  # [0]=7 [1]=cmd [2]=profile [3]=layer, zeros
        probe_ts = utc_now()
        sent_at = time.monotonic()
        try:
            response = backend._exchange_feature_report(request)  # exactly one SET + one GET
        except (BackendError, OSError) as exc:
            record = {
                "ts_utc": probe_ts,
                "kind": "probe",
                "cmd": f"0x{cmd:02x}",
                "request_hex": bytes(request).hex(),
                "response": None,
                "response_hex": None,
                "length": None,
                "meaningful_len": None,
                "parsed_profile_layer": f"{parsed[0]},{parsed[1]}",
                "precheck_ok": True,
                "error": f"{type(exc).__name__}: {exc}",
            }
            msg = str(exc)
            if "Errno 32" in msg or "EPIPE" in msg or "Broken pipe" in msg:
                record["note"] = "EPIPE: not supported by stock firmware (HARD SAFETY RULE 1) — do not retry"
            append_jsonl(record)
            completed.append(f"0x{cmd:02x}")
            print(f"[{probe_ts}] probe 0x{cmd:02x}: ERROR {record['error']}")
            # Liveness probe AFTER an error (sanctioned read only — never retry the candidate).
            ok2, _, _, err2 = health_check(backend)
            if not ok2:
                append_jsonl(
                    {
                        "ts_utc": utc_now(),
                        "kind": "abort",
                        "stage": "post_error_liveness",
                        "last_cmd": f"0x{cmd:02x}",
                        "cmd": f"0x{READ_CMD:02x}",
                        "response": None,
                        "length": None,
                        "error": err2,
                        "completed_cmds": completed,
                        "note": f"keyboard unresponsive after cmd 0x{cmd:02x} — phase STOP per PLAN.md",
                    }
                )
                print(f"STOP: keyboard unresponsive after cmd 0x{cmd:02x}: {err2}", file=sys.stderr)
                aborted = True
                break
            continue

        mlen = meaningful_len(response)
        differs = mlen > 0  # all-zero payload == the zero baseline response
        record = {
            "ts_utc": probe_ts,
            "kind": "probe",
            "cmd": f"0x{cmd:02x}",
            "request_hex": bytes(request).hex(),
            "response_hex": bytes(response).hex(),
            "length": len(response),
            "meaningful_len": mlen,
            "parsed_profile_layer": f"{parsed[0]},{parsed[1]}",
            "precheck_ok": True,
            "nonzero_response": differs,
            "error": None,
        }
        if differs:
            path = dump_probe(cmd, request, response, f"non-zero response — copied per batch instructions")
            record["dump_file"] = path.name
        append_jsonl(record)
        completed.append(f"0x{cmd:02x}")
        print(f"[{probe_ts}] probe 0x{cmd:02x}: {'NON-ZERO resp (' + str(mlen) + 'B)' if differs else 'zero response'}")

    if aborted:
        sys.exit(2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print plan; no device node access at all")
    ap.add_argument("--list", action="store_true", help="print candidate list and exit")
    ap.add_argument("--run", action="store_true", help="REQUIRED to touch the device")
    ap.add_argument("--baseline-only", action="store_true", help="only run the 3x cmd-129 baseline")
    ap.add_argument("--limit", type=int, default=None, help="max candidates this invocation")
    ap.add_argument("--start-at", default=None, help="resume from this candidate, hex (e.g. 0x0A)")
    ap.add_argument("--skip-probed", action="store_true", help="skip cmds already in probe_results.jsonl")
    ap.add_argument("--full-space", action="store_true", help="use full P1 space (LATER batches only, post G1)")
    args = ap.parse_args()

    candidates = list(FULL_SPACE if args.full_space else BATCH1)
    if set(candidates) & KNOWN_COMMANDS:
        print("FATAL: candidate list contains a known command", file=sys.stderr)
        return 3

    if args.list:
        print(f"{len(candidates)} candidates (batch-1 order, ascending):")
        print(" ".join(f"0x{c:02x}" for c in candidates))
        return 0

    if args.start_at is not None:
        start = int(args.start_at, 16)
        candidates = [c for c in candidates if c >= start]
    if args.skip_probed:
        done = probed_cmds_from_jsonl()
        candidates = [c for c in candidates if c not in done]
    if args.limit is not None:
        candidates = candidates[: args.limit]

    if args.dry_run:
        be = StockGmmkProBackend(hidraw=None)  # unit mode: NEVER opens a device node
        req = be._make_request(candidates[0] if candidates else READ_CMD)
        print(f"[dry-run] {utc_now()} | candidates ({len(candidates)}): "
              + " ".join(f"0x{c:02x}" for c in candidates))
        print(f"[dry-run] request template for first candidate: "
              f"{bytes(req[:8]).hex()} ... ({StockGmmkProBackend.REPORT_LENGTH}B total, "
              f"[0]=7 [1]=cmd [2]={be.profile} [3]={be.layer} zeros elsewhere)")
        print(f"[dry-run] baseline: {BASELINE_REPEATS}x cmd-129, all must parse sane")
        print(f"[dry-run] min interval between transactions: {MIN_INTERVAL_S}s")
        print("[dry-run] NO device node was touched")
        return 0

    if not args.run:
        print("Refusing to touch the device without --run (use --dry-run to preview).", file=sys.stderr)
        return 4

    try:
        backend = StockGmmkProBackend()  # auto-detect + sanctioned 129 sync in __init__
    except (BackendError, OSError) as exc:
        append_jsonl(
            {
                "ts_utc": utc_now(),
                "kind": "abort",
                "stage": "init_profile_sync",
                "cmd": f"0x{READ_CMD:02x}",
                "response": None,
                "length": None,
                "error": f"{type(exc).__name__}: {exc}",
                "note": "device node unreachable at backend construction; sweep NOT started; no candidate command was ever sent",
            }
        )
        print(f"STOP: backend construction failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if backend.hidraw is None:
        append_jsonl(
            {
                "ts_utc": utc_now(),
                "kind": "abort",
                "stage": "interface_detect",
                "cmd": f"0x{READ_CMD:02x}",
                "response": None,
                "length": None,
                "error": "_detect_stock_interface() found no vendor hidraw node (VID 320F, PID 5044/5092 + descriptor signature)",
                "note": "sweep NOT started; no candidate command was ever sent",
            }
        )
        print("STOP: no vendor hidraw node detected — see remap/DISCOVERY.md for the stop point", file=sys.stderr)
        return 2
    print(f"[{utc_now()}] vendor node: {backend.hidraw} | profile/layer: {backend.profile},{backend.layer}")

    baseline, _ = run_baseline(backend)
    if baseline is None:
        return 2
    if args.baseline_only:
        print("baseline-only mode: stopping before any candidate probe")
        return 0

    print(f"[{utc_now()}] sweep start: {len(candidates)} candidates: "
          + " ".join(f"0x{c:02x}" for c in candidates))
    sweep(backend, candidates)
    return 0


if __name__ == "__main__":
    sys.exit(main())
