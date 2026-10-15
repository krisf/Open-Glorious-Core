"""Phase 0 characterization tests.

These pin CURRENT ``StockGmmkProBackend`` behavior byte-for-byte before any
remap work starts (PLAN.md Phase 0 step 3). They must stay green through the
pure ``hidraw=`` keyword refactor and act as a regression tripwire later.

Every test here is device-free: the ``backend`` fixture passes an explicit
``hidraw=None`` and conftest hard-blocks all device-IO methods.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from open_glorious_core.backend import BackendError, StockGmmkProBackend


# ---------------------------------------------------------------------------
# Construction / the hidraw= keyword refactor
# ---------------------------------------------------------------------------


def test_explicit_hidraw_none_is_no_device_and_skips_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_detect(self: StockGmmkProBackend):
        calls.append(1)
        return Path("/dev/hidraw-fake")

    monkeypatch.setattr(StockGmmkProBackend, "_detect_stock_interface", fake_detect)
    monkeypatch.setattr(
        StockGmmkProBackend,
        "_sync_active_profile_layer",
        lambda self: calls.append(2),
    )

    backend = StockGmmkProBackend(hidraw=None)

    assert backend.hidraw is None
    assert backend.is_available() is False
    # Neither auto-detect nor profile/layer sync (a device exchange) ran.
    assert calls == []


def test_explicit_hidraw_path_is_used_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def fake_detect(self: StockGmmkProBackend):
        calls.append(1)
        return Path("/dev/hidraw-fake")

    monkeypatch.setattr(StockGmmkProBackend, "_detect_stock_interface", fake_detect)
    monkeypatch.setattr(
        StockGmmkProBackend,
        "_sync_active_profile_layer",
        lambda self: calls.append(2),
    )

    explicit = Path("/dev/hidraw-explicit")
    backend = StockGmmkProBackend(hidraw=explicit)

    assert backend.hidraw is explicit
    assert backend.is_available() is True
    # Auto-detect skipped; sync runs exactly once (unchanged legacy behavior
    # for a present device).
    assert calls == [2]


def test_default_construction_still_auto_detects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []
    fake_node = Path("/dev/hidraw-fake")

    def fake_detect(self: StockGmmkProBackend):
        calls.append(1)
        return fake_node

    monkeypatch.setattr(StockGmmkProBackend, "_detect_stock_interface", fake_detect)
    monkeypatch.setattr(
        StockGmmkProBackend,
        "_sync_active_profile_layer",
        lambda self: calls.append(2),
    )

    backend = StockGmmkProBackend()

    assert backend.hidraw == fake_node
    assert backend.is_available() is True
    # Byte-for-byte legacy default: detect once, then sync once.
    assert calls == [1, 2]


def test_default_construction_without_device_does_not_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    monkeypatch.setattr(
        StockGmmkProBackend,
        "_detect_stock_interface",
        lambda self: calls.append(1) or None,
    )
    monkeypatch.setattr(
        StockGmmkProBackend,
        "_sync_active_profile_layer",
        lambda self: calls.append(2),
    )

    backend = StockGmmkProBackend()

    assert backend.hidraw is None
    assert backend.is_available() is False
    assert calls == [1]


def test_deviceless_defaults(backend: StockGmmkProBackend) -> None:
    """Constructor defaults are unchanged by the refactor."""
    assert backend.hidraw is None
    assert backend.profile == 1
    assert backend.layer == 1
    assert backend.color_name == "white"
    assert backend.color_rgb == (255, 255, 255)
    assert backend.state.brightness == 100
    assert backend.state.effect == StockGmmkProBackend.SOLID_EFFECT
    assert backend.state.speed == 50
    assert backend.REPORT_ID == 7
    assert backend.REPORT_LENGTH == 256


def test_deviceless_operations_raise(backend: StockGmmkProBackend) -> None:
    with pytest.raises(BackendError):
        backend.read_state()
    with pytest.raises(BackendError):
        backend._apply_current_state()


# ---------------------------------------------------------------------------
# _prepare_properties_buffer — offsets 0,1,2,3,4,8,10
# ---------------------------------------------------------------------------


def test_properties_buffer_layout(backend: StockGmmkProBackend) -> None:
    backend.profile = 2
    backend.layer = 3
    backend.state.effect = 6
    backend.state.brightness = 55

    buf = bytearray(StockGmmkProBackend.REPORT_LENGTH)
    backend._prepare_properties_buffer(buf)

    assert len(buf) == 256
    assert buf[0] == 7  # feature report ID
    assert buf[1] == 7  # properties command
    assert buf[2] == 2  # profile
    assert buf[3] == 3  # layer
    assert buf[4] == 6  # effect
    assert buf[5] == 0
    assert buf[6] == 1
    assert buf[7] == 0
    assert buf[8] == round(55 * 20 / 100)  # brightness scaled to 0-20
    assert buf[9] == 0
    assert buf[10] == 16


@pytest.mark.parametrize(
    ("brightness", "expected"),
    [(0, 0), (50, 10), (55, 11), (100, 20)],
)
def test_properties_brightness_scaling(
    backend: StockGmmkProBackend, brightness: int, expected: int
) -> None:
    backend.state.brightness = brightness
    buf = bytearray(256)
    backend._prepare_properties_buffer(buf)
    assert buf[8] == expected
    # Untouched tail stays zero.
    assert all(byte == 0 for byte in buf[11:])


# ---------------------------------------------------------------------------
# _prepare_effect_buffer — speed inversion, multicolor flags, RGB color
# ---------------------------------------------------------------------------


def test_effect_buffer_layout_solid(backend: StockGmmkProBackend) -> None:
    backend.state.effect = 7  # Normally On (not in MULTICOLOR_EFFECTS)
    backend.state.speed = 40
    backend.state.brightness = 80
    backend.color_rgb = (10, 20, 30)

    buf = bytearray(256)
    backend._prepare_effect_buffer(buf)

    assert buf[0] == 7  # feature report ID
    assert buf[1] == 2  # effect command
    assert buf[2] == 7  # effect id
    assert buf[8] == 60  # speed INVERTED: 100 - 40
    assert buf[9] == round(80 * 20 / 100)  # brightness scaled
    assert buf[10] == 0  # solid: multicolor flag off
    assert buf[11] == 0
    assert buf[12] == 0  # solid: second multicolor flag off
    assert buf[13] == 0
    assert buf[14] == 0
    assert buf[15] == 10  # red
    assert buf[16] == 20  # green
    assert buf[17] == 30  # blue
    assert all(byte == 0 for byte in buf[18:])


def test_effect_buffer_layout_multicolor(backend: StockGmmkProBackend) -> None:
    backend.state.effect = 13  # Rainbow (multicolor)

    buf = bytearray(256)
    backend._prepare_effect_buffer(buf)

    assert buf[2] == 13
    assert buf[10] == 1  # multicolor flag on
    assert buf[12] == 1  # second multicolor flag on
    # Color still written from color_rgb (default white).
    assert (buf[15], buf[16], buf[17]) == (255, 255, 255)


@pytest.mark.parametrize(
    ("speed", "inverted"),
    [(0, 100), (25, 75), (50, 50), (100, 0)],
)
def test_effect_speed_inversion(
    backend: StockGmmkProBackend, speed: int, inverted: int
) -> None:
    backend.state.speed = speed
    buf = bytearray(256)
    backend._prepare_effect_buffer(buf)
    assert buf[8] == inverted


def test_effect_speed_inversion_is_clamped(backend: StockGmmkProBackend) -> None:
    """The clamp is applied AFTER inversion, pinning current behavior."""
    backend.state.speed = 150  # inverted: -50 -> clamped to 0
    buf = bytearray(256)
    backend._prepare_effect_buffer(buf)
    assert buf[8] == 0

    backend.state.speed = -10  # inverted: 110 -> clamped to 100
    backend._prepare_effect_buffer(buf)
    assert buf[8] == 100


# ---------------------------------------------------------------------------
# _prepare_profile_update_buffer — cmd 1, profile, layer
# ---------------------------------------------------------------------------


def test_profile_update_buffer_layout(backend: StockGmmkProBackend) -> None:
    backend.profile = 3
    backend.layer = 2

    buf = bytearray(256)
    backend._prepare_profile_update_buffer(buf)

    assert buf[0] == 7  # feature report ID
    assert buf[1] == 1  # profile-update (persist) command
    assert buf[2] == 3  # profile
    assert buf[3] == 2  # layer
    assert all(byte == 0 for byte in buf[4:])


# ---------------------------------------------------------------------------
# _make_request
# ---------------------------------------------------------------------------


def test_make_request_129(backend: StockGmmkProBackend) -> None:
    report = backend._make_request(129)

    assert len(report) == 256
    assert report[0] == 7  # report ID byte
    assert report[1] == 129  # 0x81 read active profile/layer
    assert report[2] == backend.profile
    assert report[3] == backend.layer
    assert all(byte == 0 for byte in report[4:])


def test_make_request_carries_profile_and_layer(backend: StockGmmkProBackend) -> None:
    backend.profile = 2
    backend.layer = 1
    report = backend._make_request(7)
    assert report[0] == 7
    assert report[1] == 7
    assert report[2] == 2
    assert report[3] == 1


# ---------------------------------------------------------------------------
# _parse_profile_layer_response
# ---------------------------------------------------------------------------


def test_parse_profile_layer_valid_first_candidate(backend: StockGmmkProBackend) -> None:
    response = bytearray(256)
    response[1], response[2] = 2, 3
    assert backend._parse_profile_layer_response(response) == (2, 3)


def test_parse_profile_layer_second_candidate(backend: StockGmmkProBackend) -> None:
    response = bytearray(256)
    response[2], response[3] = 1, 2
    assert backend._parse_profile_layer_response(response) == (1, 2)


def test_parse_profile_layer_third_candidate(backend: StockGmmkProBackend) -> None:
    response = bytearray(256)
    response[8], response[9] = 3, 1
    assert backend._parse_profile_layer_response(response) == (3, 1)


def test_parse_profile_layer_first_candidate_wins(backend: StockGmmkProBackend) -> None:
    response = bytearray(256)
    response[1], response[2] = 1, 1  # valid, first in candidate order
    response[2], response[3] = 1, 2  # would also be valid
    response[8], response[9] = 3, 3  # would also be valid
    assert backend._parse_profile_layer_response(response) == (1, 1)


def test_parse_profile_layer_all_zero_garbage_returns_none(
    backend: StockGmmkProBackend,
) -> None:
    assert backend._parse_profile_layer_response(bytearray(256)) is None


def test_parse_profile_layer_high_byte_garbage_returns_none(
    backend: StockGmmkProBackend,
) -> None:
    junk = bytearray([200]) * 256
    assert backend._parse_profile_layer_response(junk) is None


def test_parse_profile_layer_partial_pairs_out_of_range_return_none(
    backend: StockGmmkProBackend,
) -> None:
    response = bytearray(256)
    # response[2] is shared by candidate pairs 1 and 2, so an out-of-range
    # value there invalidates both at once.
    response[1] = 2  # pair 1 = (2, 0): layer 0 invalid
    response[2] = 0  # pair 2 = (0, 9): profile 0 invalid
    response[3] = 9
    response[8], response[9] = 4, 1  # pair 3 = (4, 1): profile 4 invalid
    assert backend._parse_profile_layer_response(response) is None
