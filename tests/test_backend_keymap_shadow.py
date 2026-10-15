"""G7 Commit 1 — factory-default helper + shadow-aware matrix verification.

The factory-reset board answers the GET config mirror with an ALL-ZERO codes
table forever, even though cmd 0x03 writes physically land (the mirror is
push-only: remap/PROTOCOL_DECODE.md "G6 VERDICT (10-15) — WRITE PATH
CONFIRMED PHYSICALLY"; the GUI source-of-truth note lives in the same doc).
The mirror being blank is therefore "read-back cannot verify", NOT a write
failure, and the GUI must display the packaged factory defaults instead
(``Backend.default_keymap_table()``, data shipped in
``src/open_glorious_core/data/`` — the app never reads ``remap/``).

Device IO is faked at the ``_exchange_feature_report``/``_send_feature_report``
boundary; conftest keeps real IO hard-blocked (zero-device rule).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from open_glorious_core.backend import (
    CMD_GET_CONFIG,
    CMD_KEY_MATRIX,
    KEYMAP_BLANK_TABLE,
    KEYMAP_CODES_OFFSET,
    KEYMAP_SLOTS,
    BackendError,
    KeymapEntry,
    StockGmmkProBackend,
)

ROOT = Path(__file__).resolve().parents[1]
SHIPPED_TABLE = (ROOT / "src" / "open_glorious_core" / "data" / "default_keymap.bin").read_bytes()

# G12: reset_keymap writes the factory table through the EMPIRICAL overlay
# (codes[58]=0x39, types[58]=0x01) — the raw vendor byte 0xFF at 58 physically
# kills CapsLock on this firmware (remap/KEYMAP_MAP.md EMPIRICAL-FIRMWARE,
# G11 incident), so a factory reset keeps the caps keycap alive.
OVERLAY_TABLE = bytes(bytearray(SHIPPED_TABLE)[:58] + b"\x39" + bytearray(SHIPPED_TABLE)[59:])

SHADOW_TAIL = (
    "device mirror is blank so read-back could not verify — physical key "
    "test is the only confirmation."
)
TRUE_MISMATCH_TEXT = (
    "Keymap write verification FAILED: read-back does not match the payload "
    "(first difference at slot 0: mirror 0x29, payload 0x00; 120 of 120 slots differ). "
    "The write is UNCONFIRMED and the device state is unknown — re-read the "
    "keymap before attempting anything else."
)
FACTORY_VS_BLANK_SLOTS = sum(1 for b in SHIPPED_TABLE if b != 0x00)  # all 120: 0xFF != 0x00


class ShadowDevice:
    """Factory-reset model: GET mirror codes table stays 0x00 x 120 forever.

    Accepts cmd 0x03 (adopts [2]/[3], stores the matrix device-side — the
    writes LAND, PROTOCOL_DECODE.md G6) and cmd 0x01, but the GET mirror only
    ever exposes the valid profile/layer header plus the blank shadow table.
    """

    def __init__(self, profile: int = 1, layer: int = 1) -> None:
        self.profile = profile
        self.layer = layer
        self.sets: list[bytes] = []
        self.matrix: bytes | None = None

    def set(self, report: bytes) -> None:
        self.sets.append(bytes(report))
        if report[1] == CMD_KEY_MATRIX:
            self.profile, self.layer = report[2], report[3]
            self.matrix = bytes(report[KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS])
        elif report[1] == 0x01:
            self.profile, self.layer = report[2], report[3]

    def exchange(self, report: bytes) -> bytearray:
        if report[1] != CMD_GET_CONFIG:
            raise AssertionError(f"unsupported GET request {report[1]:#02x}")
        mirror = bytearray(256)
        mirror[1], mirror[2] = self.profile, self.layer  # valid header
        return mirror  # codes table: 0x00 x 120 — the blank shadow, forever


class AdoptingDevice(ShadowDevice):
    """The non-shadow mirror (pre-reset device): GET exposes the last written
    codes table, so read-back CAN verify."""

    def __init__(self, profile: int = 1, layer: int = 1) -> None:
        super().__init__(profile, layer)
        self.table = bytearray(SHIPPED_TABLE)

    def exchange(self, report: bytes) -> bytearray:
        if report[1] != CMD_GET_CONFIG:
            raise AssertionError(f"unsupported GET request {report[1]:#02x}")
        mirror = bytearray(256)
        mirror[1], mirror[2] = self.profile, self.layer
        mirror[KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS] = self.table
        return mirror

    def set(self, report: bytes) -> None:
        super().set(report)
        if report[1] == CMD_KEY_MATRIX:
            self.table = bytearray(
                report[KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS]
            )


class DeadEarDevice(ShadowDevice):
    """The TRUE-mismatch model: non-zero table, never adopts any write —
    exactly the shape that must keep raising with the hard-error text."""

    def __init__(self, profile: int = 1, layer: int = 1) -> None:
        super().__init__(profile, layer)
        self.table = bytearray(SHIPPED_TABLE)

    def exchange(self, report: bytes) -> bytearray:
        return AdoptingDevice.exchange(self, report)

    def set(self, report: bytes) -> None:
        self.sets.append(bytes(report))


class FullyBlankDevice(ShadowDevice):
    """G8 live-device model (GMMK Pro WB 320F:5092, operator gate 10-15).

    The GET mirror answers ALL ZERO — header included, zero nonzero bytes in
    [4:] — TRUE EVEN IMMEDIATELY after a successful cmd 0x01 bind (tested at
    0 s / +1 s / +4 s), so _parse_profile_layer_response yields None. The G7
    strict guard refused every writer on exactly this board; G8 makes the
    shadow case first-class: writers send, once, with the honest caveat.
    """

    def exchange(self, report: bytes) -> bytearray:
        if report[1] != CMD_GET_CONFIG:
            raise AssertionError(f"unsupported GET request {report[1]:#02x}")
        return bytearray(256)  # 00 00 00 00 head + all-zero payload, forever


def wire(monkeypatch: pytest.MonkeyPatch, device: ShadowDevice) -> StockGmmkProBackend:
    """Fake the device at the IO boundary, then build a backend on a fake path.

    conftest hard-blocks the real IO methods class-wide; monkeypatch.setattr
    on the class overrides that guard for this test only, exactly like the
    FakeDevice.install pattern in tests/test_keymap_layout.py.
    """
    monkeypatch.setattr(StockGmmkProBackend, "_exchange_feature_report", device.exchange)
    monkeypatch.setattr(StockGmmkProBackend, "_send_feature_report", device.set)
    return StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))


# ---------------------------------------------------------------------------
# default_keymap_table() — the public packaged factory helper
# ---------------------------------------------------------------------------


def test_default_keymap_table_public_matches_shipped_file() -> None:
    table = StockGmmkProBackend.default_keymap_table()
    assert isinstance(table, bytes)
    assert len(table) == KEYMAP_SLOTS
    assert table == SHIPPED_TABLE


def test_default_keymap_table_matches_internal_helper() -> None:
    assert (
        StockGmmkProBackend.default_keymap_table()
        == StockGmmkProBackend._default_keymap_table()
    )


def test_default_keymap_table_is_static_no_device(backend: StockGmmkProBackend) -> None:
    # Callable on the class and on a device-less instance: pure data read.
    assert not backend.is_available()
    assert backend.default_keymap_table() == StockGmmkProBackend.default_keymap_table()


# ---------------------------------------------------------------------------
# Shadow-aware matrix verification
# ---------------------------------------------------------------------------


def test_write_keymap_matrix_returns_true_on_blank_shadow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = ShadowDevice()
    backend = wire(monkeypatch, device)
    codes = bytes(SHIPPED_TABLE)
    assert backend._write_keymap_matrix(codes, StockGmmkProBackend._default_types(), 1, 1) is True
    # The write still went out, exactly once.
    assert len(device.sets) == 1
    assert device.matrix == codes  # device-side adoption: the write LANDED


def test_write_keymap_matrix_returns_false_when_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = wire(monkeypatch, AdoptingDevice())
    codes = bytearray(SHIPPED_TABLE)
    codes[0] = 0x68  # Esc -> F13 (the G6 live-test bind)
    assert (
        backend._write_keymap_matrix(bytes(codes), StockGmmkProBackend._default_types(), 1, 1)
        is False
    )


def test_write_keymap_matrix_blank_payload_on_blank_mirror_is_verified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The blank-shadow exemption is only for a NON-matching blank read-back:
    # payload == blank table verifies byte-for-byte like any other payload.
    backend = wire(monkeypatch, ShadowDevice())
    assert (
        backend._write_keymap_matrix(
            KEYMAP_BLANK_TABLE, StockGmmkProBackend._default_types(), 1, 1
        )
        is False
    )


def test_write_keymap_matrix_true_mismatch_keeps_exact_hard_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = wire(monkeypatch, DeadEarDevice())
    with pytest.raises(BackendError) as excinfo:
        backend._write_keymap_matrix(
            KEYMAP_BLANK_TABLE, StockGmmkProBackend._default_types(), 1, 1
        )
    # Exact prior text, byte-for-byte: first diff slot 0 (Esc 0x29 vs 0x00),
    # every slot differs (0xFF unassigned != 0x00 blank), hard "UNCONFIRMED"
    # wording — the shadow exemption must not swallow real mismatches.
    assert str(excinfo.value) == TRUE_MISMATCH_TEXT


# ---------------------------------------------------------------------------
# Public write methods: shadow-caveat messages
# ---------------------------------------------------------------------------


def test_set_keymap_codes_shadow_message_is_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = ShadowDevice()
    backend = wire(monkeypatch, device)
    message = backend.set_keymap_codes(1, 1, SHIPPED_TABLE)
    assert message.startswith(
        f"Wrote the full {KEYMAP_SLOTS}-slot key matrix to profile 1 layer 1 (cmd 3 KeyMatrix); "
    )
    assert message.endswith(SHADOW_TAIL)
    assert "device mirror is blank" in message
    assert "physical key test" in message
    assert "verified" not in message
    assert len(device.sets) == 1  # exactly ONE report, never retried


def test_set_keymap_shadow_message_is_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = wire(monkeypatch, ShadowDevice())
    entries = [KeymapEntry(slot=13, scancode=0x68), KeymapEntry(slot=0, scancode=0x29)]
    message = backend.set_keymap(entries, profile=1, layer=1)
    assert message == (
        "Wrote 2 keymap change(s) to profile 1 layer 1 (cmd 3 KeyMatrix); " + SHADOW_TAIL
    )


def test_reset_keymap_shadow_message_is_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Active mirror is (2,3): reset must be issued against the ACTIVE pair.
    device = ShadowDevice(profile=2, layer=3)
    backend = wire(monkeypatch, device)
    message = backend.reset_keymap(2, 3)
    assert message == (
        "Wrote the factory default keymap to profile 2 layer 3 (cmd 3 KeyMatrix); "
        + SHADOW_TAIL
    )
    # The factory table (G12 overlay applied: slot 58 = 0x39) was written
    # device-side.
    assert device.matrix == OVERLAY_TABLE
    assert device.matrix[58] == 0x39


# ---------------------------------------------------------------------------
# G8: fully blank mirror (live WB board) — writers SEND, never refuse
# ---------------------------------------------------------------------------


def test_fully_blank_mirror_set_keymap_codes_sends_once_with_caveat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FullyBlankDevice()
    backend = wire(monkeypatch, device)
    message = backend.set_keymap_codes(1, 1, SHIPPED_TABLE)
    # Sent, not refused: the caveat message, never a verification claim.
    assert message.startswith(
        f"Wrote the full {KEYMAP_SLOTS}-slot key matrix to profile 1 layer 1 (cmd 3 KeyMatrix); "
    )
    assert message.endswith(SHADOW_TAIL)
    assert "verified" not in message
    # Exactly ONE _send_feature_report (the cmd 0x03 write); the pre-read and
    # the single read-back go through the GET exchange, no second write.
    assert len(device.sets) == 1
    assert device.sets[0][1] == CMD_KEY_MATRIX
    assert device.matrix == SHIPPED_TABLE  # write adopted device-side


def test_fully_blank_mirror_set_keymap_sends_once_with_caveat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FullyBlankDevice()
    backend = wire(monkeypatch, device)
    entries = [KeymapEntry(slot=0, scancode=0x68)]
    message = backend.set_keymap(entries, profile=1, layer=1)
    assert message == (
        "Wrote 1 keymap change(s) to profile 1 layer 1 (cmd 3 KeyMatrix); " + SHADOW_TAIL
    )
    assert len(device.sets) == 1
    # Blank mirror base table + the patch — target pair taken as given.
    want = bytearray(KEYMAP_SLOTS)
    want[0] = 0x68
    assert device.matrix == bytes(want)


def test_fully_blank_mirror_reset_keymap_sends_once_with_caveat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device = FullyBlankDevice()
    backend = wire(monkeypatch, device)
    message = backend.reset_keymap(1, 1)
    assert message == (
        "Wrote the factory default keymap to profile 1 layer 1 (cmd 3 KeyMatrix); "
        + SHADOW_TAIL
    )
    assert len(device.sets) == 1
    assert device.matrix == OVERLAY_TABLE  # factory table + G12 caps overlay


def test_parsable_mirror_still_refuses_non_active_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G8 tolerance is for BLANK mirrors only: a parsable mirror reporting a
    different active pair keeps the OLD refusal, before anything is sent."""
    device = ShadowDevice(profile=1, layer=1)
    backend = wire(monkeypatch, device)
    with pytest.raises(BackendError, match="not active"):
        backend.set_keymap_codes(2, 1, SHIPPED_TABLE)
    assert device.sets == []


# ---------------------------------------------------------------------------
# Non-shadow devices keep the verified wording (regression tripwire)
# ---------------------------------------------------------------------------


def test_set_keymap_codes_verified_message_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = wire(monkeypatch, AdoptingDevice())
    message = backend.set_keymap_codes(1, 1, SHIPPED_TABLE)
    assert message == (
        f"Wrote the full {KEYMAP_SLOTS}-slot key matrix to profile 1 layer 1 "
        "(cmd 3 KeyMatrix); read-back verified against the full codes table "
        "(types are not mirrored by GET and cannot be read back)."
    )


def test_set_keymap_verified_message_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = wire(monkeypatch, AdoptingDevice())
    entries = [KeymapEntry(slot=13, scancode=0x68)]
    message = backend.set_keymap(entries, profile=1, layer=1)
    assert message == (
        "Applied 1 keymap change(s) on profile 1 layer 1; "
        "read-back verified against the full codes table."
    )


def test_reset_keymap_verified_message_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = wire(monkeypatch, AdoptingDevice())
    message = backend.reset_keymap(1, 1)
    assert message == (
        "Restored the factory default keymap on profile 1 layer 1; "
        "read-back verified against the full codes table."
    )


def test_factory_vs_blank_slot_count_matches_hard_error() -> None:
    # The exact-hard-error test above pins "120 of 120 slots differ"; keep
    # that number honest if the shipped factory table ever changes.
    assert FACTORY_VS_BLANK_SLOTS == 120
