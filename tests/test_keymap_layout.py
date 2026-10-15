"""Phase 2 keymap layout tests — TDD against the REAL device-dump fixture.

Evidence chain (no device needed anywhere in this file):
  * ``tests/fixtures/mirror_profile11.bin`` is byte-identical to the response of
    the first ``kind="baseline"`` record in ``remap/probe_results.jsonl`` (real
    hidraw GET of the active profile's 256-byte config mirror, profile 1/layer 1,
    captured by the operator 2026-10-15). All 38 live GETs were byte-identical.
  * Keymap table location + physical row order decoded in remap/DISCOVERY.md
    ("OPERATOR RUN" section); slot -> key-name mapping in remap/KEYMAP_MAP.md.
  * H1 write hypothesis (remap/DISCOVERY.md): profile-update cmd 1 carrying the
    FULL config payload = config write; write-report bytes 2..255 map onto mirror
    bytes 0..253 (same 2-byte shift the proven RGB setters show). UNVERIFIED on
    device — the write path is built against this contract in exactly one place
    (``StockGmmkProBackend._write_keymap_table``) so a fallback stays easy.

Device IO is faked at the ``_exchange_feature_report``/``_send_feature_report``
boundary; conftest keeps real IO hard-blocked.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from open_glorious_core.backend import (
    KEYMAP_OFFSET,
    KEYMAP_SLOTS,
    KEYMAP_UNASSIGNED,
    BackendError,
    KeymapEntry,
    StockGmmkProBackend,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "mirror_profile11.bin"
MIRROR = FIXTURE_PATH.read_bytes()
FACTORY_TABLE = MIRROR[KEYMAP_OFFSET : KEYMAP_OFFSET + KEYMAP_SLOTS]


# ---------------------------------------------------------------------------
# In-memory fake of the device behavior PROVEN in remap/DISCOVERY.md
# ---------------------------------------------------------------------------


class FakeDevice:
    """Mirror GET (proven) + H1 cmd-1 full-payload SET (hypothesis model).

    GET semantics (proven on device): any read returns the current config
    mirror verbatim, ignoring the request bytes.
    SET semantics (H1, unverified): report [0]=7, [1]=cmd; cmd 1 with a full
    payload replaces mirror bytes 0..253 with report bytes 2..255; other
    commands never reach this fake in these tests.
    """

    def __init__(self, mirror: bytes = MIRROR, applies_writes: bool = True) -> None:
        self.mirror = bytearray(mirror)
        self.applies_writes = applies_writes
        self.requests: list[bytearray] = []  # SET(129)+GET exchanges (reads)
        self.writes: list[bytearray] = []  # raw _send_feature_report calls

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _exch(_self: StockGmmkProBackend, report: bytearray) -> bytearray:
            self.requests.append(bytearray(report))
            return bytearray(self.mirror)

        def _send(_self: StockGmmkProBackend, report: bytearray) -> None:
            self.writes.append(bytearray(report))
            if self.applies_writes and report[0] == 7 and report[1] == 1:
                self.mirror[0:254] = report[2:256]

        monkeypatch.setattr(StockGmmkProBackend, "_exchange_feature_report", _exch)
        monkeypatch.setattr(StockGmmkProBackend, "_send_feature_report", _send)


@pytest.fixture()
def device(monkeypatch: pytest.MonkeyPatch) -> tuple[StockGmmkProBackend, FakeDevice]:
    dev = FakeDevice()
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
# read_keymap — PROVEN mirror read path (GET slice 0x80-0xF7)
# ---------------------------------------------------------------------------


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
    known = {
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
        60: 0x39,  # Caps Lock
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
    for slot, scancode in known.items():
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
    assert request[0] == 7 and request[1] == 129  # cmd-129 read request
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
# set_keymap — H1 full-payload write (builder contract pinned byte-exact)
# ---------------------------------------------------------------------------

REMAP_FIXTURE = [(13, 0x68), (53, 0x33), (109, 0x2C)]  # PrtSc->F13, \->; , Fn->Space


def expected_h1_payload(table: bytes, profile: int = 1, layer: int = 1) -> bytearray:
    """The exact 256-byte report H1 requires: [0]=7, [1]=cmd1, [2]=profile,
    [3]=layer, mirror bytes 0..253 at report 2..255, keymap slice at 0x82..0xF9."""
    report = bytearray(256)
    report[0] = 0x07
    report[1] = 0x01
    report[2:] = MIRROR[0:254]
    report[2] = profile
    report[3] = layer
    report[0x82 : 0x82 + KEYMAP_SLOTS] = table
    return report


def test_set_keymap_builds_exact_h1_payload_for_three_entries(
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
    assert dev.writes[0] == expected_h1_payload(bytes(table))


def test_set_keymap_success_requires_readback_and_reports_it(
    device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    backend, dev = device
    message = backend.set_keymap([KeymapEntry(slot=41, scancode=0x04)])  # Q -> A
    assert "verif" in message.lower()
    assert "profile 1" in message and "layer 1" in message
    # a read-back GET happened after the single write
    assert len(dev.writes) == 1
    assert dev.requests[-1][1] == 129
    # the fake applied it; a fresh read sees the change
    after = {e.slot: e.scancode for e in backend.read_keymap()}
    assert after[41] == 0x04


def test_set_keymap_verifier_fails_on_byte_mismatch(
    dead_ear_device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    """Device ignores the write but still mirrors the OLD table -> BackendError,
    no success claimed, no retry."""
    backend, dev = dead_ear_device
    with pytest.raises(BackendError) as excinfo:
        backend.set_keymap([KeymapEntry(slot=41, scancode=0x04)])
    text = str(excinfo.value).lower()
    assert "verif" in text and "unconfirmed" in text
    assert "applied" not in text
    assert len(dev.writes) == 1  # failed verification must not re-send


def test_set_keymap_verifier_fails_on_partial_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fake applies only the FIRST requested slot; verifier must still reject."""
    dev = FakeDevice()
    dev.install(monkeypatch)

    def half_applies(_self: StockGmmkProBackend, report: bytearray) -> None:
        dev.writes.append(bytearray(report))
        if report[1] == 1:
            dev.mirror[0:254] = report[2:256]
            dev.mirror[KEYMAP_OFFSET + 53] = MIRROR[KEYMAP_OFFSET + 53]  # undo slot 53

    monkeypatch.setattr(StockGmmkProBackend, "_send_feature_report", half_applies)
    backend = StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))
    with pytest.raises(BackendError, match="slot 53"):
        backend.set_keymap(
            [KeymapEntry(slot=s, scancode=c) for s, c in REMAP_FIXTURE]
        )


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
    assert reset_payload[0] == 7 and reset_payload[1] == 1
    assert bytes(reset_payload[0x82 : 0x82 + KEYMAP_SLOTS]) == FACTORY_TABLE
    after = {e.slot: e.scancode for e in backend.read_keymap()}
    assert after[13] == 0x46


def test_reset_keymap_writes_factory_table_regardless_of_current_state(
    dead_ear_device: tuple[StockGmmkProBackend, FakeDevice],
) -> None:
    """Payload is built from remap/default_keymap.bin's bytes (the factory dump),
    never from whatever the device currently mirrors."""
    backend, dev = dead_ear_device
    dev.mirror[KEYMAP_OFFSET + 13] = 0x68  # device currently remapped, ignores writes
    with pytest.raises(BackendError, match="slot 13"):  # verify sees 0x68 vs factory 0x46
        backend.reset_keymap()
    assert len(dev.writes) == 1
    assert bytes(dev.writes[0][0x82 : 0x82 + KEYMAP_SLOTS]) == FACTORY_TABLE


# ---------------------------------------------------------------------------
# Shipped default data integrity
# ---------------------------------------------------------------------------


def test_shipped_factory_keymap_matches_the_real_dump() -> None:
    packaged = ROOT / "src" / "open_glorious_core" / "data" / "default_keymap.bin"
    operator_copy = ROOT / "remap" / "default_keymap.bin"
    packaged_bytes = packaged.read_bytes()
    assert len(packaged_bytes) == KEYMAP_SLOTS
    assert packaged_bytes == FACTORY_TABLE
    assert operator_copy.read_bytes() == FACTORY_TABLE
