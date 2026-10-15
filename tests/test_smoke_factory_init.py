"""Tests for ``remap/smoke_setkeymap.py --factory-init`` (virgin-buffer recovery).

Context (remap/DISCOVERY.md, operator run 2): after the official factory-reset
hotkey the vendor channel serves a VIRGIN all-zero config buffer (whole
0x80-0xF7 keymap table 0x00, header 00 00 00 00) and the normal pre-flight
guard (active-slot / factory-byte check) correctly refuses. ``--factory-init``
handles exactly that state:

  * virgin (entire table slice 0x00)  -> ACCEPT: one cmd-1 write of the factory
    image (packaged factory table embedded in the captured mirror fixture),
    re-GET + byte-compare + header (1,1) check -> PASS.
  * ANY nonzero byte in the table     -> REFUSE: nothing is sent.

Device IO is faked at the ``_exchange_feature_report``/``_send_feature_report``
boundary (FakeDevice from tests/test_keymap_layout.py models the PROVEN GET and
the H1 SET hypothesis); conftest keeps real IO hard-blocked. The script module
is loaded from remap/ by path — it is an operator tool, not package code.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from open_glorious_core.backend import KEYMAP_OFFSET, KEYMAP_SLOTS

from tests.test_keymap_layout import FACTORY_TABLE, MIRROR, FakeDevice

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


def expected_factory_payload(profile: int = 1, layer: int = 1) -> bytearray:
    """The exact cmd-1 report: factory capture image + packaged factory table,
    per _build_keymap_write_payload (header (1,1), brightness 0x14 embedded)."""
    report = bytearray(256)
    report[0] = 0x07
    report[1] = 0x01
    report[2:256] = MIRROR[0:254]
    report[2] = profile
    report[3] = layer
    report[0x82 : 0x82 + KEYMAP_SLOTS] = FACTORY_TABLE
    return report


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


def make_virgin_device(monkeypatch: pytest.MonkeyPatch, **virgin_kwargs: object) -> FakeDevice:
    dev = FakeDevice(mirror=virgin_mirror(**virgin_kwargs))  # type: ignore[arg-type]
    dev.install(monkeypatch)
    return dev


LIVE = ["--run", "--factory-init", "--hidraw", "/dev/hidraw-fake"]


# ---------------------------------------------------------------------------
# virgin -> ACCEPT: exact factory-image payload, exact-once, verified
# ---------------------------------------------------------------------------


def test_factory_init_virgin_accepts_and_writes_exact_factory_image(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _smoke, jsonl = smoke
    dev = make_virgin_device(monkeypatch)

    code = run_smoke(monkeypatch, LIVE)

    assert code == 0, "virgin buffer: accepted + read-back verified -> PASS/exit 0"
    assert len(dev.writes) == 1, "exactly one SET, never retried"
    payload = dev.writes[0]
    assert payload == expected_factory_payload()
    assert payload[0] == 0x07 and payload[1] == 0x01  # report id, cmd 1
    assert (payload[2], payload[3]) == (1, 1)  # profile/layer header
    assert payload[8] == payload[9] == 0x14  # brightness from the factory capture
    assert bytes(payload[0x82 : 0x82 + KEYMAP_SLOTS]) == FACTORY_TABLE  # packaged factory table
    assert bytes(payload[2:0x82]) == MIRROR[0:0x80]  # captured mirror image embedded
    # fake applied the H1 write model; the post-read saw the factory image
    assert bytes(dev.mirror[KEYMAP_OFFSET : KEYMAP_OFFSET + KEYMAP_SLOTS]) == FACTORY_TABLE
    assert (dev.mirror[0], dev.mirror[1]) == (1, 1)

    out = capsys.readouterr().out
    assert "virgin confirmed" in out
    assert "PASS" in out

    rec = read_record(jsonl)
    assert rec["mode"] == "factory-init"
    assert rec["verdict"] == "PASS"
    assert rec["target"] == "factory-image"
    assert rec["before_slice_hex"] == "00" * KEYMAP_SLOTS
    assert rec["after_slice_hex"] == FACTORY_TABLE.hex()
    assert rec["payload_hex"] == bytes(expected_factory_payload()).hex()


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

    code = run_smoke(monkeypatch, LIVE)

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
    dev = make_virgin_device(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *_args: "no")

    code = run_smoke(monkeypatch, LIVE)

    assert code == 2
    assert dev.writes == []
    assert "Aborted by operator" in capsys.readouterr().out
    assert read_record(jsonl)["verdict"] == "aborted"


# ---------------------------------------------------------------------------
# any nonzero byte in the table -> REFUSE (nothing sent)
# ---------------------------------------------------------------------------


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
    dev = make_virgin_device(monkeypatch, bright_slot=slot, bright_byte=byte)

    code = run_smoke(monkeypatch, LIVE)

    assert code == 2
    assert dev.writes == [], "refuse must happen BEFORE any SET is sent"
    out = capsys.readouterr().out
    assert "REFUSING" in out and "NOT virgin" in out
    rec = read_record(jsonl)
    assert rec["verdict"] == "refused-non-virgin"


def test_factory_init_refuses_configured_factory_buffer(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A buffer holding the configured factory keymap (the normal healthy
    state) is NOT virgin — --factory-init refuses; the single-key flow handles
    that state."""
    _smoke, jsonl = smoke
    dev = FakeDevice(mirror=MIRROR)
    dev.install(monkeypatch)

    code = run_smoke(monkeypatch, LIVE)

    assert code == 2
    assert dev.writes == []
    assert "REFUSING" in capsys.readouterr().out
    assert read_record(jsonl)["verdict"] == "refused-non-virgin"


# ---------------------------------------------------------------------------
# gates: --run/--dry-run required, arg combos, DANGER_SLOTS does not apply
# ---------------------------------------------------------------------------


def test_factory_init_requires_run_or_dry_run(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_smoke(monkeypatch, ["--factory-init"])
    assert code == 2
    assert "Refusing to do anything" in capsys.readouterr().out


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
    assert "REFUSING slot 14" not in out  # DANGER_SLOTS guard does not apply here

    code = run_smoke(monkeypatch, ["--dry-run", "--factory-init", "--revert"])
    assert code == 2
    assert "not be combined" in capsys.readouterr().out

    code = run_smoke(monkeypatch, ["--dry-run", "--factory-init", "--to", "0x68"])
    assert code == 2
    assert "not be combined" in capsys.readouterr().out


def test_normal_mode_still_requires_key(
    smoke: tuple[object, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_smoke(monkeypatch, ["--dry-run"])
    assert code == 2
    assert "--key is required" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# dry-run: exact payload, zero device access (conftest IO guards stay armed)
# ---------------------------------------------------------------------------


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
    # rebuild the first full dump (the planned 256-byte SET report) from its
    # hex-dump lines and pin it byte-exact against the expected factory image
    dump_lines = re.findall(
        r"^    [0-9A-F]{3}: ((?:[0-9a-f]{2} ){1,15}[0-9a-f]{2})$", out, re.MULTILINE
    )
    payload = bytes.fromhex("".join(dump_lines[:16]))
    assert len(payload) == 256
    assert payload == bytes(expected_factory_payload())
    assert not jsonl.exists()  # dry-run records nothing, sends nothing
