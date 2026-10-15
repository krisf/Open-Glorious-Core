"""Phase 2 + G5 keymap tests — the cmd-0x03 matrix write path.

Evidence chain (no device needed anywhere in this file):

  * ``tests/fixtures/mirror_profile11.bin`` is byte-identical to the response of
    the first ``kind="baseline"`` record in ``remap/probe_results.jsonl`` (real
    hidraw GET of the active profile's 256-byte config mirror, profile 1/layer 1,
    captured by the operator 2026-10-15). All 38 live GETs were byte-identical.
    Probe-era captures stored the payload WITHOUT a report-ID echo, so the codes
    table sits at 0x80 in this fixture.
  * ``remap/PROTOCOL_DECODE.md`` — the command spec read out of Glorious CORE
    2.1.22's own TypeScript: cmd 0x03 KeyMatrix is the keymap write (types at
    ``[8..127]``, codes at ``[128..247]``), cmd 0x01 is ChangeProfile (FOUR
    selector bytes), GET (129) mirrors profile/layer + brightness + the
    120-byte codes block.
  * H1 (cmd 1 carrying a full config payload) is REFUTED on device
    (remap/DISCOVERY.md, operator run 3): the firmware ignores everything past
    byte [3] of cmd 1. Its builder is gone; these tests pin the decoded
    contract, and ``FakeDevice`` models the refutation, not the hypothesis.
  * remap/DISCOVERY.md operator runs 2-3: the LIVE read path answers with the
    ``0x07`` report-ID echoed as byte 0, which shifts the payload by one, so the
    codes table is at 0x81 there. ``locate_codes_table()`` handles both shapes
    and is used by every mirror parse.

Device IO is faked at the ``_exchange_feature_report``/``_send_feature_report``
boundary; conftest keeps real IO hard-blocked.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from open_glorious_core.backend import (
    CMD_CHANGE_PROFILE,
    CMD_GET_CONFIG,
    CMD_KEY_MATRIX,
    CODES_TABLE_ANCHOR,
    KEYMAP_CODES_OFFSET,
    KEYMAP_OFFSET,
    KEYMAP_SLOTS,
    KEYMAP_TYPES_OFFSET,
    KEYMAP_UNASSIGNED,
    BackendError,
    KeymapEntry,
    StockGmmkProBackend,
    locate_codes_table,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mirror_profile11.bin"
MIRROR = FIXTURE_PATH.read_bytes()
FACTORY_TABLE = MIRROR[KEYMAP_OFFSET : KEYMAP_OFFSET + KEYMAP_SLOTS]
DATA_DIR = ROOT / "src" / "open_glorious_core" / "data"
DEFAULT_TYPES = (DATA_DIR / "default_types.bin").read_bytes()


def echo_shape(payload: bytes) -> bytearray:
    """The live hidraw framing: report ID echoed as byte 0, payload shifted."""
    return bytearray(bytes([0x07]) + bytes(payload[:255]))


# ---------------------------------------------------------------------------
# In-memory model of the device: PROVEN GET + decoded SET semantics
# ---------------------------------------------------------------------------


class FakeDevice:
    """GET mirror (proven) + cmd 0x03 KeyMatrix / cmd 0x01 ChangeProfile (decoded).

    GET semantics (proven on device, 38 identical captures): any read answers
    with the current config mirror, ignoring the request bytes. ``echo=True``
    answers in the live shape (0x07 report ID at byte 0, payload shifted by
    one) instead of the probe-era payload-only shape.

    SET cmd 0x03 (SendButtonMatrix2Device): the device adopts ``[2]``/``[3]``
    as the active profile/layer and replaces the codes table with the report's
    ``[128..247]``; the types array ``[8..127]`` is accepted and kept
    device-side — GET never mirrors types, so they are recorded here.

    SET cmd 0x01 (ChangeProfileID): ONLY ``[2]``/``[3]`` are honoured, bytes
    past [3] are ignored. That is exactly the behaviour that refuted H1.

    Any other command (RGB setters, GET requests): recorded, no mirror effect.
    """

    def __init__(
        self,
        mirror: bytes = MIRROR,
        applies_writes: bool = True,
        echo: bool = False,
    ) -> None:
        self.mirror = bytearray(mirror)
        self.applies_writes = applies_writes
        self.echo = echo
        self.types: bytes | None = None
        self.requests: list[bytearray] = []  # exchanges = reads (GET mirror)
        self.writes: list[bytearray] = []  # raw _send_feature_report calls

    def response(self) -> bytearray:
        if self.echo:
            return echo_shape(self.mirror)
        return bytearray(self.mirror)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _exch(_self: StockGmmkProBackend, report: bytearray) -> bytearray:
            self.requests.append(bytearray(report))
            return self.response()

        def _send(_self: StockGmmkProBackend, report: bytearray) -> None:
            self.writes.append(bytearray(report))
            if not self.applies_writes:
                return
            if report[1] == CMD_KEY_MATRIX:
                self.mirror[0], self.mirror[1] = report[2], report[3]
                self.types = bytes(
                    report[KEYMAP_TYPES_OFFSET : KEYMAP_TYPES_OFFSET + KEYMAP_SLOTS]
                )
                self.mirror[KEYMAP_OFFSET : KEYMAP_OFFSET + KEYMAP_SLOTS] = report[
                    KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS
                ]
            elif report[1] == CMD_CHANGE_PROFILE:
                # 4 selector bytes only: nothing past [3] has any effect.
                self.mirror[0], self.mirror[1] = report[2], report[3]

        monkeypatch.setattr(StockGmmkProBackend, "_exchange_feature_report", _exch)
        monkeypatch.setattr(StockGmmkProBackend, "_send_feature_report", _send)


@pytest.fixture()
def device(monkeypatch: pytest.MonkeyPatch) -> tuple[StockGmmkProBackend, FakeDevice]:
    dev = FakeDevice()
    dev.install(monkeypatch)
    backend = StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))
    return backend, dev


@pytest.fixture()
def echo_device(monkeypatch: pytest.MonkeyPatch) -> tuple[StockGmmkProBackend, FakeDevice]:
    """Same device, live framing: every GET carries the 0x07 report-ID echo."""
    dev = FakeDevice(echo=True)
    dev.install(monkeypatch)
    backend = StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))
    return backend, dev


@pytest.fixture()
def dead_ear_device(monkeypatch: pytest.MonkeyPatch) -> tuple[StockGmmkProBackend, FakeDevice]:
    """Fake that answers reads but silently ignores every write (verify-mismatch model)."""
    dev = FakeDevice(applies_writes=False)
    dev.install(monkeypatch)
    backend = StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))
    return backend, dev


def expected_matrix_payload(
    codes: bytes,
    types: bytes = DEFAULT_TYPES,
    profile: int = 1,
    layer: int = 1,
) -> bytearray:
    """The exact 256-byte cmd-0x03 report per PROTOCOL_DECODE.md: [0]=0x07,
    [1]=0x03, [2]=profile, [3]=layer, types at [8..127], codes at [128..247],
    [4..7] and [248..255] zero."""
    report = bytearray(256)
    report[0] = 0x07
    report[1] = CMD_KEY_MATRIX
    report[2] = profile
    report[3] = layer
    report[KEYMAP_TYPES_OFFSET : KEYMAP_TYPES_OFFSET + KEYMAP_SLOTS] = types
    report[KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS] = codes
    return report


# ---------------------------------------------------------------------------
# Fixture provenance
# ---------------------------------------------------------------------------


def test_fixture_is_the_real_first_baseline_dump() -> None:
    """The fixture must equal the first baseline record of the live capture."""
    lines = (ROOT / "remap" / "probe_results.jsonl").read_text(encoding="utf-8")
    first_baseline = next(
        json.loads(line)
        for line in lines.splitlines()
        if line.strip() and json.loads(line).get("kind") == "baseline"
    )
    assert bytes.fromhex(first_baseline["response_hex"]) == MIRROR
    assert len(MIRROR) == 256
    assert MIRROR[0] == 1 and MIRROR[1] == 1  # profile/layer 1,1
    assert MIRROR[6] == MIRROR[7] == 0x14  # brightness 20/200 scale on 2026-10-14


# ---------------------------------------------------------------------------
# locate_codes_table — echo-shape-agnostic mirror parsing
# ---------------------------------------------------------------------------


def test_locate_codes_table_finds_the_table_in_the_no_echo_fixture() -> None:
    assert CODES_TABLE_ANCHOR == bytes((0x29, 0x3A, 0x3B, 0x3C))  # Esc F1 F2 F3
    assert locate_codes_table(MIRROR) == 0x80
    assert MIRROR[0x80 : 0x80 + KEYMAP_SLOTS] == FACTORY_TABLE


def test_locate_codes_table_finds_the_echo_shape() -> None:
    """The live read path echoes the 0x07 report ID as byte 0 -> table at 0x81."""
    response = echo_shape(MIRROR)
    assert response[0] == 0x07
    assert locate_codes_table(response) == 0x81
    assert bytes(response)[0x81 : 0x81 + KEYMAP_SLOTS] == FACTORY_TABLE


def test_locate_codes_table_handles_the_post_factory_reset_response() -> None:
    """Operator run 2/3 shape: ``07 03 01 01 ...`` (echo + active profile 3)."""
    response = bytearray(256)
    response[0:4] = bytes([0x07, 0x03, 0x01, 0x01])
    response[0x81 : 0x81 + KEYMAP_SLOTS] = FACTORY_TABLE
    assert locate_codes_table(response) == 0x81


def test_locate_codes_table_anchor_scan_finds_a_shifted_table() -> None:
    """Neither canonical offset matches -> the anchor scan locates the table."""
    response = bytearray(256)
    response[0:4] = bytes([0x07, 0x03, 0x01, 0x01])
    response[0x90 : 0x90 + KEYMAP_SLOTS] = FACTORY_TABLE
    assert locate_codes_table(response) == 0x90


def test_locate_codes_table_without_anchor_uses_the_echo_slice() -> None:
    """A remapped Esc kills the anchor: fall back to 0x80, or 0x81 behind an echo."""
    remapped = bytearray(MIRROR)
    remapped[0x80] = 0x04  # Esc slot now 'A': no anchor left in the buffer
    assert bytes(remapped).find(CODES_TABLE_ANCHOR) == -1
    assert locate_codes_table(remapped) == 0x80

    remapped_echo = echo_shape(remapped)
    assert bytes(remapped_echo).find(CODES_TABLE_ANCHOR) == -1
    assert locate_codes_table(remapped_echo) == 0x81


def test_locate_codes_table_on_a_virgin_buffer() -> None:
    """Factory-reset buffer (all zeros) parses to the plain slice, both shapes."""
    assert locate_codes_table(bytearray(256)) == 0x80
    virgin_echo = bytearray(256)
    virgin_echo[0] = 0x07
    assert locate_codes_table(virgin_echo) == 0x81


# ---------------------------------------------------------------------------
# read_keymap — PROVEN mirror read path (120-slot codes table, both shapes)
# ---------------------------------------------------------------------------

KNOWN_FACTORY_KEYS = {
    0: 0x29,  # Esc
    1: 0x3A,  # F1
    12: 0x45,  # F12
    13: 0x46,  # PrtSc
    20: 0x35,  # grave `
    21: 0x1E,  # 1
    30: 0x27,  # 0
    31: 0x2D,  # minus
    33: 0x2A,  # Backspace
    34: 0x4C,  # Del (R1)
    40: 0x2B,  # Tab
    41: 0x14,  # Q
    50: 0x13,  # P
    54: 0x4B,  # PgUp (R2)
    58: 0xFF,  # unassigned spare (G17: a dead matrix cell — G11's "empirical CapsLock @58" is RETRACTED)
    60: 0x39,  # Caps Lock — the vendor table's OWN cell, sweep-confirmed on WB5092 (G13/G17)
    61: 0x04,  # A
    73: 0x28,  # Enter
    74: 0x4E,  # PgDn (R3)
    80: 0xE1,  # Left Shift
    91: 0xE5,  # Right Shift
    93: 0x52,  # Up arrow
    94: 0x4D,  # End (R4)
    100: 0xE0,  # Left Ctrl
    101: 0xE3,  # Left Win
    102: 0xE2,  # Left Alt
    105: 0x2C,  # Space
    108: 0xE6,  # Right Alt
    109: 0xA0,  # Fn (vendor raw code; Glorious guide: Alt/Fn/Ctrl right of space)
    110: 0xE4,  # Right Ctrl
    112: 0x50,  # Left arrow
    113: 0x51,  # Down arrow
    114: 0x4F,  # Right arrow
}


def test_read_keymap_returns_all_120_slots_in_order(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, _dev = device
    entries = backend.read_keymap(profile=1, layer=1)
    assert len(entries) == KEYMAP_SLOTS == 120
    assert [e.slot for e in entries] == list(range(KEYMAP_SLOTS))
    assert all(isinstance(e.scancode, int) and 0 <= e.scancode <= 0xFF for e in entries)


def test_read_keymap_decodes_known_factory_keys(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    """Spot values verified row-by-row in remap/DISCOVERY.md (factory keymap)."""
    backend, _dev = device
    got = {e.slot: e.scancode for e in backend.read_keymap()}
    for slot, scancode in KNOWN_FACTORY_KEYS.items():
        assert got[slot] == scancode, f"slot {slot}: got {got[slot]:#04x}, want {scancode:#04x}"


def test_read_keymap_decodes_the_echo_shaped_response(
    echo_device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    """Post-factory-reset framing (0x07 echo): the same table must be decoded."""
    backend, dev = echo_device
    assert dev.response()[0] == 0x07
    got = {e.slot: e.scancode for e in backend.read_keymap()}
    for slot, scancode in KNOWN_FACTORY_KEYS.items():
        assert got[slot] == scancode, f"slot {slot}: got {got[slot]:#04x}, want {scancode:#04x}"


def test_read_keymap_spare_slots_are_unassigned(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, _dev = device
    entries = {e.slot: e for e in backend.read_keymap()}
    for slot in (15, 16, 19, 35, 59, 79, 99, 103, 106, 119):
        assert entries[slot].scancode == KEYMAP_UNASSIGNED == 0xFF
        assert entries[slot].label is None


def test_read_keymap_labels_physical_keys(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, _dev = device
    entries = {e.slot: e for e in backend.read_keymap()}
    assert "Esc" in entries[0].label
    assert entries[41].label == "Q"
    assert entries[105].label == "Space"
    assert entries[41].scancode == 0x14


def test_read_keymap_uses_only_the_proven_129_read(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, dev = device
    backend.read_keymap(profile=1, layer=1)
    # One exchange ran during construction (profile/layer sync); read_keymap
    # adds one more. No raw SET beyond exchanges ever happens on the read path.
    assert dev.writes == []
    assert len(dev.requests) == 2
    request = dev.requests[-1]
    assert request[0] == 7 and request[1] == CMD_GET_CONFIG == 129  # cmd-129 read request
    assert request[2] == 1 and request[3] == 1  # requested profile/layer


def test_read_keymap_rejects_garbage_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dev = FakeDevice(mirror=bytes([0xAA]) * 256)
    dev.install(monkeypatch)
    backend = StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))
    with pytest.raises(BackendError):
        backend.read_keymap(profile=1, layer=1)


def test_read_keymap_refuses_non_active_profile(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    """The mirror only ever exposes the ACTIVE profile (DISCOVERY.md) — saying
    otherwise would be lying, so it must raise instead of returning profile 1's
    table under a profile-2 call."""
    backend, _dev = device
    with pytest.raises(BackendError, match="active"):
        backend.read_keymap(profile=2, layer=1)


def test_read_keymap_without_device_raises(backend: StockGmmkProBackend) -> None:
    with pytest.raises(BackendError):
        backend.read_keymap()


# ---------------------------------------------------------------------------
# cmd 0x03 KeyMatrix report builder — byte-exact against the command spec
# ---------------------------------------------------------------------------

REMAP_FIXTURE = [(13, 0x68), (53, 0x33), (109, 0x2C)]  # PrtSc->F13, \->; , Fn->Space


def test_key_matrix_report_is_byte_exact(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, _dev = device
    table = bytearray(FACTORY_TABLE)
    for slot, scancode in REMAP_FIXTURE:
        table[slot] = scancode

    report = backend._build_key_matrix_report(1, 1, bytes(table))

    assert report == expected_matrix_payload(bytes(table))
    assert len(report) == 256
    assert report[0] == 7  # FEATURE report ID 7
    assert report[1] == CMD_KEY_MATRIX == 0x03  # SendButtonMatrix2Device
    assert report[2] == 1 and report[3] == 1  # profile / layer selectors
    assert report[4:8] == bytes(4)  # no header payload on this command
    assert bytes(report[KEYMAP_TYPES_OFFSET : KEYMAP_TYPES_OFFSET + KEYMAP_SLOTS]) == DEFAULT_TYPES
    assert bytes(report[KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS]) == bytes(table)
    assert KEYMAP_TYPES_OFFSET + KEYMAP_SLOTS == KEYMAP_CODES_OFFSET == 0x80  # [8..127] then [128..247]
    assert report[KEYMAP_CODES_OFFSET + KEYMAP_SLOTS :] == bytes(8)  # tail [248..255] zero


def test_key_matrix_report_selectors_follow_the_argument(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, _dev = device
    for profile, layer in ((1, 1), (2, 3), (3, 2)):
        report = backend._build_key_matrix_report(profile, layer, FACTORY_TABLE)
        assert report[2] == profile and report[3] == layer


def test_key_matrix_report_uses_the_packaged_vendor_types_by_default(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, _dev = device
    report = backend._build_key_matrix_report(1, 1, FACTORY_TABLE)
    types = bytes(report[KEYMAP_TYPES_OFFSET : KEYMAP_TYPES_OFFSET + KEYMAP_SLOTS])
    assert types == (DATA_DIR / "default_types.bin").read_bytes()


def test_key_matrix_report_accepts_an_explicit_types_array(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, _dev = device
    types = bytes([0x05]) + bytes(DEFAULT_TYPES[1:])  # slot 0 as a macro type
    report = backend._build_key_matrix_report(1, 1, FACTORY_TABLE, types)
    assert bytes(report[KEYMAP_TYPES_OFFSET : KEYMAP_TYPES_OFFSET + KEYMAP_SLOTS]) == types
    assert bytes(report[KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS]) == FACTORY_TABLE


def test_key_matrix_report_validates_lengths_and_selectors(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, dev = device
    with pytest.raises(BackendError, match="120"):
        backend._build_key_matrix_report(1, 1, FACTORY_TABLE[:-1])
    with pytest.raises(BackendError, match="120"):
        backend._build_key_matrix_report(1, 1, FACTORY_TABLE, DEFAULT_TYPES + b"\x01")
    with pytest.raises(BackendError, match="byte values"):
        backend._build_key_matrix_report(1, 1, [0x100] * KEYMAP_SLOTS)
    with pytest.raises(BackendError, match="[Pp]rofile must be"):
        backend._build_key_matrix_report(4, 1, FACTORY_TABLE)
    with pytest.raises(BackendError, match="[Ll]ayer must be"):
        backend._build_key_matrix_report(1, 0, FACTORY_TABLE)
    assert dev.writes == []


# ---------------------------------------------------------------------------
# set_keymap_codes — the full-matrix write (cmd 0x03), send-once + read-back
# ---------------------------------------------------------------------------


def test_set_keymap_codes_sends_one_cmd3_and_verifies(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, dev = device
    codes = bytearray(FACTORY_TABLE)
    codes[41] = 0x04  # Q -> A

    message = backend.set_keymap_codes(1, 1, bytes(codes))

    assert len(dev.writes) == 1, "exactly one SET, never retried"
    assert dev.writes[0] == expected_matrix_payload(bytes(codes))
    text = message.lower()
    assert "cmd 3" in text and "verif" in text and "profile 1" in text and "layer 1" in text
    # verification is a fresh GET of the mirror
    assert dev.requests[-1][1] == CMD_GET_CONFIG
    after = {e.slot: e.scancode for e in backend.read_keymap()}
    assert after[41] == 0x04


def test_set_keymap_codes_defaults_types_to_the_vendor_array(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, dev = device
    backend.set_keymap_codes(1, 1, FACTORY_TABLE)
    assert dev.types == DEFAULT_TYPES


def test_set_keymap_codes_honours_explicit_types(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, dev = device
    types = bytes(FACTORY_TABLE)  # any 120-byte array; codes unchanged
    backend.set_keymap_codes(1, 1, FACTORY_TABLE, types)
    assert dev.types == types


def test_set_keymap_codes_works_on_the_echo_shaped_device(
    echo_device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    """Mirror read -> codes located through the echo -> write -> verify (echo)."""
    backend, dev = echo_device
    codes = bytearray(FACTORY_TABLE)
    codes[13] = 0x68
    message = backend.set_keymap_codes(1, 1, bytes(codes))
    assert len(dev.writes) == 1
    assert dev.writes[0] == expected_matrix_payload(bytes(codes))
    assert "verif" in message.lower()
    after = {e.slot: e.scancode for e in backend.read_keymap()}
    assert after[13] == 0x68


def test_set_keymap_codes_verifier_fails_on_byte_mismatch(
    dead_ear_device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    """Device ignores the write but still mirrors the OLD table -> BackendError,
    no success claimed, no retry."""
    backend, dev = dead_ear_device
    codes = bytearray(FACTORY_TABLE)
    codes[41] = 0x04
    with pytest.raises(BackendError) as excinfo:
        backend.set_keymap_codes(1, 1, bytes(codes))
    text = str(excinfo.value).lower()
    assert "verif" in text and "unconfirmed" in text
    assert "wrote" not in text
    assert len(dev.writes) == 1  # failed verification must not re-send


def test_set_keymap_codes_verifier_fails_on_partial_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fake applies every slot but one; the full-table compare must reject."""
    dev = FakeDevice()
    dev.install(monkeypatch)

    def mostly_applies(_self: StockGmmkProBackend, report: bytearray) -> None:
        dev.writes.append(bytearray(report))
        if report[1] == CMD_KEY_MATRIX:
            dev.mirror[0], dev.mirror[1] = report[2], report[3]
            codes = report[KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS]
            dev.mirror[KEYMAP_OFFSET : KEYMAP_OFFSET + KEYMAP_SLOTS] = codes
            dev.mirror[KEYMAP_OFFSET + 53] = FACTORY_TABLE[53]  # undo slot 53

    monkeypatch.setattr(StockGmmkProBackend, "_send_feature_report", mostly_applies)
    backend = StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))
    codes = bytearray(FACTORY_TABLE)
    for slot, scancode in REMAP_FIXTURE:
        codes[slot] = scancode
    with pytest.raises(BackendError, match="slot 53"):
        backend.set_keymap_codes(1, 1, bytes(codes))


def test_set_keymap_codes_refuses_a_non_active_profile_without_writing(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    """The mirror only exposes the ACTIVE profile: writing profile 2 blind is
    refused BEFORE anything is sent (the old H1 path sent it and hoped)."""
    backend, dev = device
    with pytest.raises(BackendError, match="not active"):
        backend.set_keymap_codes(2, 1, FACTORY_TABLE)
    assert dev.writes == []


def test_set_keymap_codes_rejects_wrong_length_codes(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, dev = device
    with pytest.raises(BackendError, match="120"):
        backend.set_keymap_codes(1, 1, FACTORY_TABLE + b"\x00")
    with pytest.raises(BackendError, match="120"):
        backend.set_keymap_codes(1, 1, b"\x29" * (KEYMAP_SLOTS - 1))
    assert dev.writes == []


def test_set_keymap_codes_without_device_raises(backend: StockGmmkProBackend) -> None:
    with pytest.raises(BackendError):
        backend.set_keymap_codes(1, 1, FACTORY_TABLE)


# ---------------------------------------------------------------------------
# set_keymap — patch the mirrored table, write it back with cmd 0x03
# ---------------------------------------------------------------------------


def test_set_keymap_builds_the_exact_matrix_payload(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, dev = device
    table = bytearray(FACTORY_TABLE)
    for slot, scancode in REMAP_FIXTURE:
        table[slot] = scancode

    backend.set_keymap(
        [KeymapEntry(slot=s, scancode=c) for s, c in REMAP_FIXTURE],
        profile=1,
        layer=1,
    )

    assert len(dev.writes) == 1, "exactly one SET, never retried"
    assert dev.writes[0] == expected_matrix_payload(bytes(table))


def test_set_keymap_success_requires_readback_and_reports_it(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, dev = device
    message = backend.set_keymap([KeymapEntry(slot=41, scancode=0x04)])  # Q -> A
    assert "verif" in message.lower()
    assert "profile 1" in message and "layer 1" in message
    # a read-back GET happened after the single write
    assert len(dev.writes) == 1
    assert dev.requests[-1][1] == CMD_GET_CONFIG
    # the fake applied it; a fresh read sees the change
    after = {e.slot: e.scancode for e in backend.read_keymap()}
    assert after[41] == 0x04


def test_set_keymap_verifier_fails_on_byte_mismatch(
    dead_ear_device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, dev = dead_ear_device
    with pytest.raises(BackendError) as excinfo:
        backend.set_keymap([KeymapEntry(slot=41, scancode=0x04)])
    text = str(excinfo.value).lower()
    assert "verif" in text and "unconfirmed" in text
    assert "applied" not in text
    assert len(dev.writes) == 1  # failed verification must not re-send


# ---------------------------------------------------------------------------
# cmd 0x01 ChangeProfile — four selector bytes, nothing more
# ---------------------------------------------------------------------------


def test_change_profile_sends_only_the_four_selector_bytes(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, dev = device
    message = backend.change_profile(2, 3)

    assert len(dev.writes) == 1, "sent exactly once"
    report = dev.writes[0]
    assert len(report) == 256
    assert report[0] == 7
    assert report[1] == CMD_CHANGE_PROFILE == 0x01
    assert report[2] == 2  # profile selector
    assert report[3] == 3  # layer selector
    assert all(byte == 0 for byte in report[4:])  # no payload: firmware ignores it
    assert "verif" in message.lower() or "confirmed" in message.lower()


def test_change_profile_updates_backend_state(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, dev = device
    backend.change_profile(3, 2)
    assert (backend.profile, backend.layer) == (3, 2)
    assert (dev.mirror[0], dev.mirror[1]) == (3, 2)
    # the mirror follows the active slot: reading the new active profile works
    entries = backend.read_keymap(profile=3, layer=2)
    assert len(entries) == KEYMAP_SLOTS


def test_change_profile_failure_is_reported_as_unconfirmed(
    dead_ear_device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, dev = dead_ear_device
    with pytest.raises(BackendError, match="UNCONFIRMED"):
        backend.change_profile(2, 1)
    assert len(dev.writes) == 1  # never retried
    assert (backend.profile, backend.layer) == (1, 1)


def test_change_profile_validates_selectors_and_sends_nothing(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, dev = device
    for bad in ((0, 1), (4, 1), (1, 0), (1, 4)):
        with pytest.raises(BackendError):
            backend.change_profile(*bad)
    assert dev.writes == []


def test_change_profile_without_device_raises(backend: StockGmmkProBackend) -> None:
    with pytest.raises(BackendError):
        backend.change_profile(2, 1)


def test_h1_full_config_write_builder_is_gone() -> None:
    """The refuted H1 builder must not survive anywhere in the backend."""
    for name in ("_build_keymap_write_payload", "_write_keymap_table", "_prepare_profile_update_buffer"):
        assert not hasattr(StockGmmkProBackend, name), name


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_keymap_entry_validates_slot_and_scancode() -> None:
    with pytest.raises(BackendError):
        KeymapEntry(slot=120, scancode=0x04)
    with pytest.raises(BackendError):
        KeymapEntry(slot=-1, scancode=0x04)
    with pytest.raises(BackendError):
        KeymapEntry(slot=0, scancode=0x100)
    with pytest.raises(BackendError):
        KeymapEntry(slot=0, scancode=-1)
    ok = KeymapEntry(slot=0, scancode=0xFF, label="Esc")
    assert (ok.slot, ok.scancode, ok.label) == (0, 0xFF, "Esc")


def test_set_keymap_rejects_empty_and_duplicate_entries(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, dev = device
    with pytest.raises(BackendError):
        backend.set_keymap([])
    with pytest.raises(BackendError):
        backend.set_keymap(
            [KeymapEntry(slot=41, scancode=0x04), KeymapEntry(slot=41, scancode=0x05)]
        )
    assert dev.writes == []


# ---------------------------------------------------------------------------
# reset_keymap — factory defaults shipped inside the package
# ---------------------------------------------------------------------------


def test_reset_keymap_restores_factory_table(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, dev = device
    backend.set_keymap([KeymapEntry(slot=s, scancode=c) for s, c in REMAP_FIXTURE])
    mid = {e.slot: e.scancode for e in backend.read_keymap()}
    assert mid[13] == 0x68  # mutated

    message = backend.reset_keymap(profile=1, layer=1)
    assert "verif" in message.lower()
    assert len(dev.writes) == 2
    reset_payload = dev.writes[-1]
    assert reset_payload[0] == 7 and reset_payload[1] == CMD_KEY_MATRIX
    # G17 (retracts the G12 overlay): reset writes the PLAIN vendor factory
    # table — the vector is caps-complete as shipped: codes[60]=0x39 /
    # types[60]=0x01 is the sweep-confirmed CapsLock cell (G13 sweep); the
    # G11 slot-58 claim was a misattribution, so no special bytes at 58.
    reset_codes = bytes(reset_payload[KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS])
    reset_types = bytes(reset_payload[KEYMAP_TYPES_OFFSET : KEYMAP_TYPES_OFFSET + KEYMAP_SLOTS])
    assert reset_codes == FACTORY_TABLE
    assert reset_types == DEFAULT_TYPES
    assert reset_codes[60] == 0x39 and reset_types[60] == 0x01
    assert reset_codes[58] == 0xFF  # dead matrix spare keeps its vendor byte
    after = {e.slot: e.scancode for e in backend.read_keymap()}
    assert after[13] == 0x46


def test_reset_keymap_writes_factory_table_regardless_of_current_state(
    dead_ear_device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    """Payload is built from the shipped factory dumps (codes + types), never
    from whatever the device currently mirrors."""
    backend, dev = dead_ear_device
    dev.mirror[KEYMAP_OFFSET + 13] = 0x68  # device currently remapped, ignores writes
    with pytest.raises(BackendError, match="slot 13"):  # verify sees 0x68 vs factory 0x46
        backend.reset_keymap()
    assert len(dev.writes) == 1
    # G17: the same PLAIN vendor factory table on the reset payload — the
    # sweep-confirmed caps cell (slot 60) carries 0x39 as shipped; slot 58
    # (dead spare) stays 0xFF.
    reset_codes = bytes(dev.writes[0][KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS])
    assert reset_codes == FACTORY_TABLE
    assert reset_codes[60] == 0x39


# ---------------------------------------------------------------------------
# Shipped default data integrity
# ---------------------------------------------------------------------------


def test_shipped_factory_keymap_matches_the_real_dump() -> None:
    packaged = DATA_DIR / "default_keymap.bin"
    operator_copy = ROOT / "remap" / "default_keymap.bin"
    packaged_bytes = packaged.read_bytes()
    assert len(packaged_bytes) == KEYMAP_SLOTS
    assert packaged_bytes == FACTORY_TABLE
    assert operator_copy.read_bytes() == FACTORY_TABLE


def test_shipped_default_types_length_and_histogram() -> None:
    """remap/PROTOCOL_DECODE.md, Buttoninfo_Default types section:
    0x01 x77 normal, 0x02 x7, 0x03 x1 (consumer Mute), 0xFF x35 unused."""
    types = DEFAULT_TYPES
    assert len(types) == KEYMAP_SLOTS == 120
    assert Counter(types) == {0x01: 77, 0x02: 7, 0x03: 1, 0xFF: 35}


def test_shipped_default_types_agree_with_the_slot_map() -> None:
    """Cross-check the vendor types array against remap/KEYMAP_MAP.md: the 7
    type-0x02 slots are exactly the modifiers, type 0x03 sits at slot 14 (the
    knob/mute position PROTOCOL_DECODE calls the consumer Mute button), and
    type 0xFF marks the unassigned trailing spares — every slot KEYMAP_MAP has
    no keycap for, except the three matrix artifacts (14, 72, 92) which carry a
    real type."""
    from open_glorious_core.backend import KEYMAP_SLOT_LABELS

    types = DEFAULT_TYPES
    assert [slot for slot, value in enumerate(types) if value == 0x02] == [
        80, 91, 100, 101, 102, 108, 110
    ]  # LShift RShift LCtrl LWin LAlt RAlt RCtrl
    assert [KEYMAP_SLOT_LABELS.get(slot) for slot in (80, 91, 100, 101, 102, 108, 110)] == [
        "Left Shift",
        "Right Shift",
        "Left Ctrl",
        "Left Win",
        "Left Alt",
        "Right Alt",
        "Right Ctrl",
    ]
    assert [slot for slot, value in enumerate(types) if value == 0x03] == [14]
    assert FACTORY_TABLE[14] == 0x02  # KEYMAP_MAP.md: vendor-internal knob/mute byte
    ff_slots = {slot for slot, value in enumerate(types) if value == 0xFF}
    unlabeled = {slot for slot in range(KEYMAP_SLOTS) if slot not in KEYMAP_SLOT_LABELS}
    # 14/72/92: matrix artifacts, not spares. G17: slot 60 is LABELED again —
    # the vendor table's Caps Lock cell (type 0x01, byte 0x39) is the real
    # caps cell on WB5092 (G13 sweep). Slot 58 is a plain factory-0xFF dead
    # spare, and every 0xFF-type spare is unlabeled by definition, so the
    # artifacts are the only non-FF unlabeled cells.
    assert unlabeled - ff_slots == {14, 72, 92}
    assert types[14] == 0x03 and types[72] == 0x01 and types[92] == 0x01
    assert types[60] == 0x01 and types[58] == 0xFF  # G17 sweep-verified byte+type facts
    assert len(ff_slots) == 35


def test_backend_default_loaders_read_the_shipped_files() -> None:
    assert StockGmmkProBackend._default_types() == DEFAULT_TYPES
    assert StockGmmkProBackend._default_keymap_table() == FACTORY_TABLE
