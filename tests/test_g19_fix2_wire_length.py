"""G19-FIX2 — feature-report WIRE LENGTH for the new G19 commands.

Device-proven defect (2026-11-16, operator present): the GMMK Pro WB
(320f:5092, KeyMatrixLength=120) STALLS (EPIPE on the hidraw feature ioctl)
when the cmd-0x06 per-key layout report goes out at 264 bytes; the first
256 bytes of the SAME buffer are accepted. Vendor ground truth
(GmmkSeries.ts SetFeatureReport): KeyMatrixLength <= 120 sends
``hid.SetFeatureReport(DeviceId, 0x07, 256, buf)`` — 256 WIRE bytes from a
264-byte allocated buffer. Exactly three call sites changed (backend.py):
apply_per_key_layout (0x06), write_macro_type_table (0x04),
write_macro_data (0x05). Buffers stay 264; builders unchanged.

NOTE ON THE BRIEF'S REGRESSION BULLET ("properties/effect/keymap/bind/
profile-update STILL 264 bytes"): at THIS code level the physically proven
paths have NEVER put 264 in the ioctl — the properties/effect/keymap/bind/
profile-update framing is the 256-byte REPORT_LENGTH buffer and only the
standalone polling buffer is 264. The contract ("wire_length=None keeps
today's len(report) behavior") means: those paths emit EXACTLY what HEAD
emitted. That is what the regression pins below assert, byte for byte.

Zero device I/O: conftest hard-blocks real IO and FakeHIDDevice captures the
WIRES (it slices to wire_length exactly like the real ioctl truncates). The
one test that touches the real send method monkeypatches os.open/os.close/
fcntl.ioctl in-process and pins the ioctl size field — still zero device IO.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from open_glorious_core import backend as backend_module
from open_glorious_core.backend import (
    CMD_CHANGE_PROFILE,
    CMD_KEY_MATRIX,
    CMD_MACRO_DATA,
    CMD_MACRO_TYPE_TABLE,
    CMD_SET_LAYOUT,
    CMD_SET_POLLING,
    G19_WIRE_LENGTH,
    KEYMAP_SLOTS,
    MACRO_MAX_EVENTS,
    MACRO_REPORT_LENGTH,
    PER_KEY_KEY_NUMBER,
    PER_KEY_LAYOUT_BYTES,
    PER_KEY_PAGE_BYTES,
    PER_KEY_PAGE_COUNT,
    PER_KEY_LAYOUT_REPORT_LENGTH,
    BackendError,
    StockGmmkProBackend,
    build_macro_events_bytes,
)

# Captured at IMPORT time (collection), before the autouse conftest guard
# replaces the method — lets one test exercise the REAL send body with the
# OS layer faked in-process.
_REAL_SEND = StockGmmkProBackend._send_feature_report

EVENTS = [
    {"down": True, "code": 0x04, "delay": 120},
    {"down": False, "code": 0x04, "delay": 5},
    {"down": True, "code": 0x09, "delay": 300},
]


@pytest.fixture()
def wired(fake_hid):
    """Real StockGmmkProBackend over the FakeHIDDevice; capture starts clean."""
    backend = StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))
    fake_hid.capture.clear()
    return backend, fake_hid


def _cmd_reports(fake, cmd: int) -> list[bytes]:
    return [r for r in fake.capture if r[1] == cmd]


def _slots() -> dict:
    # Esc (slot 0) red, Q (slot 41) blue — the exact operator-confirmed pair.
    return {
        "0": {"vis": "lit", "rgb": [255, 0, 0]},
        "41": {"vis": "lit", "rgb": [0, 0, 255]},
    }


# --- cmd 0x06 layout page: 256 wire bytes, header + payload intact ------------

def test_layout_page_wire_is_256_with_header_and_payload_intact(wired) -> None:
    backend, fake = wired
    backend.set_per_key_enabled(True)
    backend.per_key["brightness"] = 55
    backend.per_key["slots"] = _slots()
    layout = backend.build_per_key_layout()
    assert len(layout) == PER_KEY_LAYOUT_BYTES

    backend.apply_per_key_layout()

    pages = _cmd_reports(fake, CMD_SET_LAYOUT)
    assert len(pages) == PER_KEY_PAGE_COUNT
    for page_index, page in enumerate(pages):
        assert len(page) == G19_WIRE_LENGTH == 256, "256 WIRE bytes, not 264"
        assert page[0] == 0x07, "report ID"
        assert page[1] == CMD_SET_LAYOUT, "cmd 0x06"
        # header bytes [2..6] unchanged by the truncation
        assert page[2] == backend.profile
        assert page[3] == backend.layer
        assert page[4] == PER_KEY_KEY_NUMBER == 83
        assert page[5] == page_index
        assert page[6] == round(55 * 20 / 100)
        # the full 200-byte page payload survives at [8..207]
        start = page_index * PER_KEY_PAGE_BYTES
        assert page[8 : 8 + PER_KEY_PAGE_BYTES] == layout[start : start + PER_KEY_PAGE_BYTES]
    # the dropped bytes are ONLY the 256..263 tail: the wire is an exact
    # prefix of the still-264-byte builder buffer.
    full = bytearray(PER_KEY_LAYOUT_REPORT_LENGTH)
    backend._prepare_per_key_layout_buffer(full, 0)
    assert len(full) == 264, "the BUFFER stays 264"
    assert pages[0] == bytes(full[:256]) and pages[0] != bytes(full)
    # operator-confirmed slots ride the wire: Esc (0, page 0) red,
    # Q (slot 41 -> layout byte 205 -> page 1) blue.
    assert pages[0][8 : 8 + 5] == bytes([0xFF, 0, 255, 0, 0])
    assert pages[1][8 + 5 : 8 + 10] == bytes([0xFF, 41, 0, 0, 255])


def test_three_layout_pages_in_order_only_byte5_differs(wired) -> None:
    backend, fake = wired
    backend.set_per_key_enabled(True)  # empty slots: identical zero payloads
    backend.apply_per_key_layout()
    pages = _cmd_reports(fake, CMD_SET_LAYOUT)
    assert [p[5] for p in pages] == [0, 1, 2], "pages sent in order 0,1,2"
    assert len(pages[0]) == len(pages[1]) == len(pages[2]) == 256
    for other in pages[1:]:
        assert other[:5] == pages[0][:5]
        assert other[6:] == pages[0][6:]


# --- cmd 0x04 macro type table -------------------------------------------------

def test_macro_type_table_wire_is_256(wired) -> None:
    backend, fake = wired
    backend.write_macro_type_table(2, 3, {58: 1, 119: 0})
    sent = _cmd_reports(fake, CMD_MACRO_TYPE_TABLE)
    assert len(sent) == 1
    wire = sent[0]
    assert len(wire) == G19_WIRE_LENGTH == 256
    assert wire[0] == 0x07 and wire[1] == CMD_MACRO_TYPE_TABLE
    assert wire[2] == 3 and wire[3] == 4, "profile+1 / layer+1 selectors intact"
    # slot 119 pair sits at [8+238..239] — the HIGHEST table bytes, fully
    # inside the 256-byte wire; slot 58 carries FF FF.
    assert wire[8 + 119 * 2 : 8 + 119 * 2 + 2] == bytes([0x00, 0x01])
    assert wire[8 + 58 * 2 : 8 + 58 * 2 + 2] == bytes([0xFF, 0xFF])
    full = backend._build_macro_type_table_report(2, 3, {58: 1, 119: 0})
    assert len(full) == MACRO_REPORT_LENGTH == 264, "the BUFFER stays 264"
    assert wire == bytes(full[:256])


# --- cmd 0x05 macro data -------------------------------------------------------

def test_macro_data_wire_is_256_event_stream_intact(wired) -> None:
    backend, fake = wired
    backend.write_macro_data(3, EVENTS)
    sent = _cmd_reports(fake, CMD_MACRO_DATA)
    assert len(sent) == 1
    wire = sent[0]
    assert len(wire) == G19_WIRE_LENGTH == 256
    assert wire[0] == 0x07 and wire[1] == CMD_MACRO_DATA and wire[2] == 3
    stream = build_macro_events_bytes(EVENTS)
    assert wire[8 : 8 + len(stream)] == stream, "event stream intact"
    assert wire[8 + len(stream) : 256] == bytes(256 - 8 - len(stream)), "zeros = end marker"
    full = backend._build_macro_data_report(3, EVENTS)
    assert len(full) == 264 and wire == bytes(full[:256])


def test_macro_data_max_stream_fits_the_256_wire(wired) -> None:
    """82 events = 246 payload bytes at [8..253]: the whole vendor payload
    region [8..255] lives INSIDE the 256-byte wire — nothing is lost."""
    backend, fake = wired
    events = [{"down": True, "code": 0x04 + (i % 40), "delay": i + 1} for i in range(MACRO_MAX_EVENTS)]
    backend.write_macro_data(0, events)
    wire = _cmd_reports(fake, CMD_MACRO_DATA)[0]
    assert len(wire) == 256
    stream = build_macro_events_bytes(events)
    assert len(stream) == MACRO_MAX_EVENTS * 3 == 246
    assert wire[8 : 8 + 246] == stream
    assert wire[254:256] == bytes(2)


# --- regression pins: the proven paths are byte-identical to HEAD --------------

def test_properties_effect_profile_update_sends_unchanged(wired) -> None:
    backend, fake = wired
    backend._apply_current_state()  # per-key DISABLED: the HEAD trio
    assert [r[1] for r in fake.capture] == [0x07, 0x02, CMD_CHANGE_PROFILE]
    properties = bytearray(backend.REPORT_LENGTH)
    effect = bytearray(backend.REPORT_LENGTH)
    profile_update = bytearray(backend.REPORT_LENGTH)
    backend._prepare_properties_buffer(properties)
    backend._prepare_effect_buffer(effect)
    backend._prepare_change_profile_buffer(profile_update)
    # None wire_length => the FULL buffer on the wire, exactly like HEAD
    # (this framing is the proven 256-byte properties buffer; the G19 fix
    # must not have touched it).
    assert fake.capture[0] == bytes(properties) and len(fake.capture[0]) == 256
    assert fake.capture[1] == bytes(effect) and len(fake.capture[1]) == 256
    assert fake.capture[2] == bytes(profile_update) and len(fake.capture[2]) == 256


def test_keymap_bind_and_polling_sends_unchanged(wired) -> None:
    backend, fake = wired
    backend.set_keymap_codes(1, 1, bytes(KEYMAP_SLOTS), bytes(KEYMAP_SLOTS))
    matrix = _cmd_reports(fake, CMD_KEY_MATRIX)
    assert len(matrix) == 1
    expected = backend._build_key_matrix_report(1, 1, bytes(KEYMAP_SLOTS), bytes(KEYMAP_SLOTS))
    assert matrix[0] == bytes(expected) and len(matrix[0]) == 256

    fake.capture.clear()
    backend.change_profile(1, 1)
    bind = _cmd_reports(fake, CMD_CHANGE_PROFILE)
    assert len(bind) == 1
    assert bind[0] == bytes(backend._build_change_profile_report(1, 1)) and len(bind[0]) == 256

    fake.capture.clear()
    backend.set_polling_rate(500, True)
    polling = _cmd_reports(fake, CMD_SET_POLLING)
    # the STANDALONE 264-byte polling buffer keeps its full 264-byte framing
    assert len(polling) == 1 and len(polling[0]) == 264


# --- the real ioctl: only the SIZE field shrinks, the buffer stays whole -------

def test_real_send_overrides_only_the_ioctl_size(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercise the REAL _send_feature_report with the OS layer faked — the
    # conftest class-level block is overridden on purpose here; no /dev/hidraw
    # is ever opened (os.open itself is replaced in-process).
    monkeypatch.setattr(StockGmmkProBackend, "_send_feature_report", _REAL_SEND)
    opened: list = []
    calls: list[tuple[int, int]] = []

    def fake_open(path, flags):  # noqa: ANN001 - in-process fake
        opened.append(path)
        return 999  # fake fd; never a real descriptor

    def fake_ioctl(fd, cmd, buffer, mutable):  # noqa: ANN001
        calls.append((cmd, len(buffer)))

    monkeypatch.setattr(backend_module.os, "open", fake_open)
    monkeypatch.setattr(backend_module.os, "close", lambda fd: None)
    monkeypatch.setattr(backend_module.fcntl, "ioctl", fake_ioctl)

    # hidraw=None skips the __init__ mirror sync; the path is set directly so
    # the real _send_feature_report below runs against the faked OS layer.
    backend = StockGmmkProBackend(hidraw=None)
    backend.hidraw = Path("/dev/hidraw-fake")
    buffer264 = bytearray(MACRO_REPORT_LENGTH)
    buffer264[0] = 0x07
    buffer264[1] = CMD_MACRO_DATA

    backend._send_feature_report(buffer264)  # HEAD behaviour
    assert calls == [(backend_module._hid_iocsfeature(264), 264)]

    calls.clear()
    backend._send_feature_report(buffer264, wire_length=G19_WIRE_LENGTH)
    assert calls == [(backend_module._hid_iocsfeature(256), 264)], (
        "ioctl SIZE 256 while the 264-byte buffer is handed over intact"
    )

    calls.clear()
    with pytest.raises(BackendError):
        backend._send_feature_report(buffer264, wire_length=265)
    with pytest.raises(BackendError):
        backend._send_feature_report(buffer264, wire_length=0)
    assert calls == [], "invalid wire lengths fail BEFORE any ioctl"


def test_wire_length_constant_is_the_vendor_256() -> None:
    assert G19_WIRE_LENGTH == 256
    assert G19_WIRE_LENGTH < PER_KEY_LAYOUT_REPORT_LENGTH == MACRO_REPORT_LENGTH == 264
