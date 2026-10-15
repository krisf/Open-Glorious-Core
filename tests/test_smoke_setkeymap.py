"""Tests for ``remap/smoke_setkeymap.py`` (the operator's write-path tool).

The tool is the G5 rewrite onto the decoded protocol (remap/PROTOCOL_DECODE.md):
every mode writes through **cmd 0x03 KeyMatrix**, reads through the proven GET
(cmd 129), locates the codes table with ``locate_codes_table()`` (echo shape and
payload shape), takes profile/layer FROM THE MIRROR SELECTORS, sends exactly one
SET behind the interactive WRITE gate, and byte-compares the whole table
afterwards. The refuted H1 mode (cmd 1 full-config payload) is gone.

Modes under test:
  --matrix-write            identity write / one-slot patch, selectors from mirror
  --revert-matrix           packaged factory codes + types
  --factory-init            virgin all-zero gate -> factory defaults, then verify

Device IO is faked at the ``_exchange_feature_report``/``_send_feature_report``
boundary (FakeDevice from tests/test_keymap_layout.py models the PROVEN GET and
the decoded cmd-0x03/cmd-0x01 SET semantics); conftest keeps real IO hard-blocked.
The script module is loaded from remap/ by path — it is an operator tool, not
package code.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from open_glorious_core.backend import (
    CMD_KEY_MATRIX,
    KEYMAP_CODES_OFFSET,
    KEYMAP_OFFSET,
    KEYMAP_SLOTS,
    KEYMAP_TYPES_OFFSET,
)

from tests.test_keymap_layout import (
    DEFAULT_TYPES,
    FACTORY_TABLE,
    MIRROR,
    FakeDevice,
    echo_shape,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "remap" / "smoke_setkeymap.py"

_spec = importlib.util.spec_from_file_location("smoke_setkeymap", SCRIPT_PATH)
SMOKE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SMOKE)


def virgin_mirror(bright_slot: int | None = None, bright_byte: int = 0x01) -> bytes:
    """A factory-reset state mirror: header 00 00 00 00, whole table 0x00."""
    buf = bytearray(256)
    if bright_slot is not None:
        buf[KEYMAP_OFFSET + bright_slot] = bright_byte
    return bytes(buf)


def configured_mirror(profile: int = 3, layer: int = 2, table: bytes = FACTORY_TABLE) -> bytes:
    """A configured mirror with distinctive selectors (payload-only framing)."""
    buf = bytearray(256)
    buf[0], buf[1] = profile, layer
    buf[6], buf[7] = 0x14, 0x14
    buf[KEYMAP_OFFSET : KEYMAP_OFFSET + KEYMAP_SLOTS] = table
    return bytes(buf)


def expected_matrix(
    codes: bytes,
    types: bytes = DEFAULT_TYPES,
    profile: int = 1,
    layer: int = 1,
) -> bytes:
    report = bytearray(256)
    report[0] = 0x07
    report[1] = CMD_KEY_MATRIX
    report[2], report[3] = profile, layer
    report[KEYMAP_TYPES_OFFSET : KEYMAP_TYPES_OFFSET + KEYMAP_SLOTS] = types
    report[KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS] = codes
    return bytes(report)


@pytest.fixture()
def smoke(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[object, Path]:
    """Script module with the JSONL artifact redirected and the WRITE gate fed.

    space_out is neutralized (no test may sleep); real device IO stays blocked
    by the autouse conftest guard unless a FakeDevice is installed per test.
    """
    jsonl = tmp_path / "smoke_results.jsonl"
    monkeypatch.setattr(SMOKE, "JSONL_PATH", jsonl)
    monkeypatch.setattr(SMOKE, "space_out", lambda: None)
    monkeypatch.setattr("builtins.input", lambda *_args: "WRITE")
    return SMOKE, jsonl


def run_smoke(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["smoke_setkeymap.py", *argv])
    return SMOKE.main()


def read_record(jsonl: Path) -> dict:
    lines = [line for line in jsonl.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1, "one JSON line per invocation"
    return json.loads(lines[0])


def first_dump(out: str) -> bytes:
    """Rebuild the first 256-byte hex dump printed by the script."""
    dump_lines = re.findall(
        r"^    [0-9A-F]{3}: ((?:[0-9a-f]{2} ){1,15}[0-9a-f]{2})$", out, re.MULTILINE
    )
    payload = bytes.fromhex("".join(dump_lines[:16]))
    assert len(payload) == 256
    return payload


LIVE = ["--run", "--hidraw", "/dev/hidraw-fake"]
FACTORY_INIT = LIVE + ["--factory-init"]


# ---------------------------------------------------------------------------
# --factory-init: virgin -> ACCEPT factory defaults via cmd 0x03
# ---------------------------------------------------------------------------


def test_factory_init_virgin_accepts_and_writes_factory_defaults(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _smoke, jsonl = smoke
    dev = FakeDevice(mirror=virgin_mirror())
    dev.install(monkeypatch)

    code = run_smoke(monkeypatch, FACTORY_INIT)

    assert code == 0, "virgin buffer: accepted + read-back verified -> PASS/exit 0"
    assert len(dev.writes) == 1, "exactly one SET, never retried"
    payload = bytes(dev.writes[0])
    assert payload == expected_matrix(FACTORY_TABLE)
    assert payload[0] == 0x07 and payload[1] == CMD_KEY_MATRIX == 0x03
    assert (payload[2], payload[3]) == (1, 1)  # --profile/--layer (virgin has no selectors)
    assert payload[4:8] == bytes(4)
    assert bytes(dev.writes[0][KEYMAP_TYPES_OFFSET : KEYMAP_TYPES_OFFSET + KEYMAP_SLOTS]) == DEFAULT_TYPES
    assert bytes(dev.writes[0][KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS]) == FACTORY_TABLE
    assert bytes(dev.mirror[KEYMAP_OFFSET : KEYMAP_OFFSET + KEYMAP_SLOTS]) == FACTORY_TABLE
    assert (dev.mirror[0], dev.mirror[1]) == (1, 1)

    out = capsys.readouterr().out
    assert "virgin confirmed" in out
    assert "PASS" in out

    rec = read_record(jsonl)
    assert rec["mode"] == "factory-init"
    assert rec["verdict"] == "PASS"
    assert rec["target"] == "factory-defaults"
    assert rec["before_slice_hex"] == "00" * KEYMAP_SLOTS
    assert rec["after_slice_hex"] == FACTORY_TABLE.hex()
    assert rec["payload_hex"] == expected_matrix(FACTORY_TABLE).hex()
    assert rec["selectors_ok"] is True


def test_factory_init_accepts_the_echo_shaped_virgin_response(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Post-reset live framing (0x07 echo, virgin table at 0x81) passes the gate
    and verifies at the located offset."""
    _smoke, jsonl = smoke
    dev = FakeDevice(mirror=virgin_mirror(), echo=True)
    dev.install(monkeypatch)

    code = run_smoke(monkeypatch, FACTORY_INIT)

    assert code == 0
    out = capsys.readouterr().out
    assert "virgin confirmed: the entire table slice at 0x81 is 0x00" in out
    assert read_record(jsonl)["verdict"] == "PASS"


def test_factory_init_post_verify_fail_verdict(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Device ignores the write (dead ear): virgin pre-flight passes, the write
    is sent ONCE, the post-read mirror is still virgin -> FAIL, exit 1, no retry."""
    _smoke, jsonl = smoke
    dev = FakeDevice(mirror=virgin_mirror(), applies_writes=False)
    dev.install(monkeypatch)

    code = run_smoke(monkeypatch, FACTORY_INIT)

    assert code == 1
    assert len(dev.writes) == 1
    out = capsys.readouterr().out
    assert "virgin confirmed" in out and "FAIL" in out
    rec = read_record(jsonl)
    assert rec["verdict"] == "FAIL"


def test_factory_init_aborted_by_operator_sends_nothing(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _smoke, jsonl = smoke
    dev = FakeDevice(mirror=virgin_mirror())
    dev.install(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *_args: "no")

    code = run_smoke(monkeypatch, FACTORY_INIT)

    assert code == 2
    assert dev.writes == []
    assert "Aborted by operator" in capsys.readouterr().out
    assert read_record(jsonl)["verdict"] == "aborted"


@pytest.mark.parametrize("slot", [0, 13, 60, 119])
@pytest.mark.parametrize("byte", [0x01, 0x46, 0xFF])
def test_factory_init_refuses_any_nonzero_table_byte(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    slot: int,
    byte: int,
) -> None:
    _smoke, jsonl = smoke
    dev = FakeDevice(mirror=virgin_mirror(bright_slot=slot, bright_byte=byte))
    dev.install(monkeypatch)

    code = run_smoke(monkeypatch, FACTORY_INIT)

    assert code == 2
    assert dev.writes == [], "refuse must happen BEFORE any SET is sent"
    out = capsys.readouterr().out
    assert "REFUSING" in out and "NOT virgin" in out
    assert read_record(jsonl)["verdict"] == "refused-non-virgin"


def test_factory_init_refuses_configured_factory_buffer(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A buffer holding the configured factory keymap (the normal healthy
    state) is NOT virgin — --factory-init refuses; --revert-matrix handles it."""
    _smoke, jsonl = smoke
    dev = FakeDevice(mirror=MIRROR)
    dev.install(monkeypatch)

    code = run_smoke(monkeypatch, FACTORY_INIT)

    assert code == 2
    assert dev.writes == []
    out = capsys.readouterr().out
    assert "REFUSING" in out and "--revert-matrix" in out
    assert read_record(jsonl)["verdict"] == "refused-non-virgin"


def test_factory_init_dry_run_prints_exact_payload_touches_no_device(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _smoke, jsonl = smoke
    # NO FakeDevice: conftest's hard IO block stays armed — a dry-run that
    # tries any device IO raises AssertionError and fails this test.

    code = run_smoke(monkeypatch, ["--dry-run", "--factory-init"])

    assert code == 0
    out = capsys.readouterr().out
    assert "Nothing was sent" in out
    assert first_dump(out) == expected_matrix(FACTORY_TABLE)
    assert not jsonl.exists()  # dry-run records nothing, sends nothing


def test_factory_init_rejects_slot_arguments(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # slot 14 is a DANGER_SLOT: the refusal message must be the arg-combo
    # refusal, proving the DANGER_SLOTS guard does not apply to --factory-init
    # (the payload is the whole factory image, every slot included by design).
    code = run_smoke(monkeypatch, ["--dry-run", "--factory-init", "--key", "14"])
    assert code == 2
    out = capsys.readouterr().out
    assert "not be combined" in out
    assert "REFUSING slot 14" not in out

    code = run_smoke(monkeypatch, ["--dry-run", "--factory-init", "--to", "0x68"])
    assert code == 2
    assert "not be combined" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# --matrix-write: identity write / one-slot patch, selectors FROM THE MIRROR
# ---------------------------------------------------------------------------


def test_matrix_write_identity_uses_mirror_selectors(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Profile/layer come from the MIRROR header, not from the CLI: a device on
    profile 3/layer 2 is written at 3/2 even though --profile defaults to 1."""
    _smoke, jsonl = smoke
    dev = FakeDevice(mirror=configured_mirror(profile=3, layer=2))
    dev.install(monkeypatch)

    code = run_smoke(monkeypatch, LIVE + ["--matrix-write"])

    assert code == 0
    assert len(dev.writes) == 1
    payload = bytes(dev.writes[0])
    assert payload == expected_matrix(FACTORY_TABLE, profile=3, layer=2)
    out = capsys.readouterr().out
    assert "taken from the MIRROR, not the CLI" in out
    assert "profile 3 / layer 2" in out
    rec = read_record(jsonl)
    assert rec["mode"] == "matrix-write"
    assert (rec["profile"], rec["layer"]) == (3, 2)
    assert rec["target"] == "identity"
    assert rec["verdict"] == "PASS"
    assert rec["after_slice_hex"] == FACTORY_TABLE.hex()


def test_matrix_write_patches_one_slot(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _smoke, jsonl = smoke
    dev = FakeDevice(mirror=configured_mirror(profile=1, layer=1))
    dev.install(monkeypatch)

    code = run_smoke(monkeypatch, LIVE + ["--matrix-write", "--key", "13", "--to", "0x68"])

    assert code == 0
    want = bytearray(FACTORY_TABLE)
    want[13] = 0x68
    assert len(dev.writes) == 1
    assert bytes(dev.writes[0]) == expected_matrix(bytes(want))
    out = capsys.readouterr().out
    assert "PrtSc" in out  # the slot label is shown
    assert "--revert-matrix" in out  # the revert hint
    rec = read_record(jsonl)
    assert rec["slot"] == 13 and rec["target"] == "0x68"
    assert rec["after_slice_hex"] == bytes(want).hex()


def test_matrix_write_refuses_without_parsable_selectors(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A virgin mirror (header 00 00 00 00) gives no selectors: the write target
    is unknown, so nothing is sent."""
    _smoke, jsonl = smoke
    dev = FakeDevice(mirror=virgin_mirror())
    dev.install(monkeypatch)

    code = run_smoke(monkeypatch, LIVE + ["--matrix-write"])

    assert code == 2
    assert dev.writes == []
    assert "no parsable profile/layer selectors" in capsys.readouterr().out
    assert read_record(jsonl)["verdict"] == "refused-no-selectors"


def test_matrix_write_verify_fail_is_reported(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _smoke, jsonl = smoke
    dev = FakeDevice(mirror=configured_mirror(), applies_writes=False)
    dev.install(monkeypatch)

    code = run_smoke(monkeypatch, LIVE + ["--matrix-write", "--key", "13", "--to", "0x68"])

    assert code == 1
    assert len(dev.writes) == 1  # never retried
    out = capsys.readouterr().out
    assert "FAIL" in out and "UNCONFIRMED" in out
    assert read_record(jsonl)["verdict"] == "FAIL"


def test_matrix_write_dry_run_touches_no_device(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _smoke, jsonl = smoke
    code = run_smoke(monkeypatch, ["--dry-run", "--matrix-write", "--key", "13", "--to", "0x68"])
    assert code == 0
    out = capsys.readouterr().out
    want = bytearray(FACTORY_TABLE)
    want[13] = 0x68
    assert first_dump(out) == expected_matrix(bytes(want))
    assert not jsonl.exists()


# ---------------------------------------------------------------------------
# --revert-matrix: packaged factory codes + types
# ---------------------------------------------------------------------------


def test_revert_matrix_writes_packaged_defaults(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _smoke, jsonl = smoke
    remapped = bytearray(FACTORY_TABLE)
    remapped[13] = 0x68
    remapped[41] = 0x04
    dev = FakeDevice(mirror=configured_mirror(profile=2, layer=1, table=bytes(remapped)))
    dev.install(monkeypatch)

    code = run_smoke(monkeypatch, LIVE + ["--revert-matrix"])

    assert code == 0
    assert len(dev.writes) == 1
    payload = bytes(dev.writes[0])
    assert payload == expected_matrix(FACTORY_TABLE, profile=2, layer=1)
    assert "Factory defaults restored" in capsys.readouterr().out
    rec = read_record(jsonl)
    assert rec["mode"] == "revert-matrix"
    assert rec["verdict"] == "PASS"
    assert rec["before_slice_hex"] == bytes(remapped).hex()
    assert rec["after_slice_hex"] == FACTORY_TABLE.hex()


def test_revert_matrix_dry_run_is_the_same_payload(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _smoke, jsonl = smoke
    code = run_smoke(monkeypatch, ["--dry-run", "--revert-matrix"])
    assert code == 0
    # built on the fixture capture: its selectors are (1,1)
    assert first_dump(capsys.readouterr().out) == expected_matrix(FACTORY_TABLE)
    assert not jsonl.exists()


# ---------------------------------------------------------------------------
# gates: mode selection, arg validation, DANGER_SLOTS
# ---------------------------------------------------------------------------


def test_requires_run_or_dry_run(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_smoke(monkeypatch, ["--matrix-write"])
    assert code == 2
    assert "Refusing to do anything" in capsys.readouterr().out


def test_requires_exactly_one_mode(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_smoke(monkeypatch, ["--dry-run"])
    assert code == 2
    assert "exactly one mode" in capsys.readouterr().out

    code = run_smoke(monkeypatch, ["--dry-run", "--matrix-write", "--revert-matrix"])
    assert code == 2
    assert "exactly one mode" in capsys.readouterr().out


def test_danger_slots_refused_for_key(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _smoke, jsonl = smoke
    dev = FakeDevice(mirror=configured_mirror())
    dev.install(monkeypatch)
    for slot in (14, 72, 92):
        code = run_smoke(monkeypatch, ["--dry-run", "--matrix-write", "--key", str(slot)])
        assert code == 2
        assert "REFUSING slot" in capsys.readouterr().out
    assert dev.writes == []
    assert not jsonl.exists()  # refused at the arg gate, before anything runs


def test_key_range_and_to_without_key(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_smoke(monkeypatch, ["--dry-run", "--matrix-write", "--key", "120"])
    assert code == 2
    assert "must be 0-119" in capsys.readouterr().out

    code = run_smoke(monkeypatch, ["--dry-run", "--revert-matrix", "--key", "13"])
    assert code == 2
    assert "not be combined" in capsys.readouterr().out

    code = run_smoke(monkeypatch, ["--dry-run", "--matrix-write", "--to", "0x68"])
    assert code == 2
    assert "--to needs --key" in capsys.readouterr().out

    code = run_smoke(monkeypatch, ["--dry-run", "--revert-matrix", "--key", "14", "--to", "0xFF"])
    assert code == 2
    assert "not be combined" in capsys.readouterr().out


def test_h1_single_key_mode_is_gone(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The refuted H1 single-key mode (bare --key, cmd 1 payload, --revert) is
    not reachable: a bare --key is no mode, and every write goes through
    --matrix-write / --revert-matrix / --factory-init on cmd 0x03."""
    code = run_smoke(monkeypatch, ["--dry-run", "--key", "13", "--to", "0x68"])
    assert code == 2
    assert "exactly one mode" in capsys.readouterr().out
    for name in ("_build_keymap_write_payload", "_write_keymap_table"):
        assert not hasattr(SMOKE.StockGmmkProBackend, name)
