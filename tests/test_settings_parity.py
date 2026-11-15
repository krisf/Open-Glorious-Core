"""G18 Commit 2 — settings parity wire tests: polling rate, input latency,
side lights.

Pinned protocol facts (G18 plan P1–P3):

* P1 — cmd 0x08 SetPollingRatetoDevice is a STANDALONE 264-byte report:
  [0]=0x07, [1]=0x08, [8]=index into (1000, 500, 250, 125), [9]=EP2Enable=1,
  [10]=LEDNoChange=1, byte [11] is V3-boards-only and NEVER written on
  WB5092 (stays 0), everything else zero.
* P2 — the properties report (cmd 0x07): [7] is the side-light switch
  INVERTED (ON->0, OFF->1), [10] carries the RAW input-latency ms (2/8/16).
* P3 — G18 defaults (1000 Hz, side lights ON, 16 ms) keep the default-state
  properties buffer BYTE-IDENTICAL to the pre-G18 behavior.

Invalid values must be refused BEFORE anything touches the wire (zero
reports). Zero device I/O: conftest hard-blocks real IO; FakeHIDDevice is
wired at the IO boundary.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from open_glorious_core import app as app_module
from open_glorious_core.backend import (
    CMD_SET_POLLING,
    INPUT_LATENCY_MS,
    POLLING_RATES,
    POLLING_REPORT_LENGTH,
    BackendError,
    StockGmmkProBackend,
)
from open_glorious_core.device import HidDevice

DEVICE = HidDevice(
    hidraw="/dev/hidraw-test",
    name="GMMK Pro",
    vendor_id=0x218B,
    product_id=0x0A2B,
    physical_id="test-1",
    interface_id=2,
)


@pytest.fixture()
def wired(fake_hid):
    """Real StockGmmkProBackend over the FakeHIDDevice; capture starts clean."""
    backend = StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))
    fake_hid.capture.clear()
    return backend, fake_hid


def _polling_reports(fake) -> list[bytes]:
    return [r for r in fake.capture if r[1] == CMD_SET_POLLING]


def _properties_reports(fake) -> list[bytes]:
    return [r for r in fake.capture if r[1] == 7]


# --- P1: full 264-byte polling payload per rate ------------------------------

@pytest.mark.parametrize(("rate", "index"), list(zip(POLLING_RATES, range(4))))
def test_polling_payload_is_the_full_vendor_buffer(wired, rate: int, index: int) -> None:
    backend, fake = wired
    backend.set_polling_rate(rate, True)
    reports = _polling_reports(fake)
    assert len(reports) == 1, "exactly one standalone cmd-0x08 report"
    expected = bytearray(POLLING_REPORT_LENGTH)
    expected[0] = 0x07  # report ID
    expected[1] = CMD_SET_POLLING
    expected[8] = index  # [1000, 500, 250, 125].index(rate)
    expected[9] = 1  # EP2Enable — vendor always 1
    expected[10] = 1  # LEDNoChange on a standalone send
    assert reports[0] == bytes(expected)  # FULL 264-byte equality
    assert reports[0][11] == 0, "V3-only byte [11] must never be written on WB5092"
    assert backend.polling_rate == rate


def test_polling_state_updates_only_after_the_write(fake_hid, monkeypatch) -> None:
    backend = StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))
    fake_hid.capture.clear()

    def explode(report: bytes) -> None:
        raise AssertionError("write failed here")

    monkeypatch.setattr(backend, "_send_feature_report", explode)
    with pytest.raises(AssertionError):
        backend.set_polling_rate(500, True)
    assert backend.polling_rate == 1000, "failed write must not mutate state"


# --- P2: properties bytes [7] / [10] ------------------------------------------

def test_default_properties_keep_side_lights_on_and_16ms(wired) -> None:
    backend, fake = wired
    backend.set_brightness(80, True)  # any state change re-sends properties
    props = _properties_reports(fake)
    assert props, "properties report sent"
    assert props[-1][7] == 0, "side lights ON -> [7]=0 (inverted)"
    assert props[-1][10] == 16


@pytest.mark.parametrize("latency", INPUT_LATENCY_MS)
@pytest.mark.parametrize("lights", [True, False])
def test_properties_side_lights_times_latency(wired, lights: bool, latency: int) -> None:
    backend, fake = wired
    backend.set_side_lights(lights, True)
    backend.set_input_latency(latency, True)
    props = _properties_reports(fake)
    assert props[-1][7] == (0 if lights else 1)
    assert props[-1][10] == latency


# --- P3: default-state byte-identical regression ------------------------------

def test_default_properties_byte_identical_to_pre_g18(wired) -> None:
    """G18 P3: with defaults, the properties buffer equals the pre-G18 one."""
    backend, fake = wired
    backend.set_brightness(100, True)
    props = _properties_reports(fake)[-1]
    legacy = bytearray(backend.REPORT_LENGTH)
    # Rebuild the PRE-G18 hardcoded buffer: report 7 / cmd 7, profile 1
    # layer 1, effect 7, Win_Lock 0, PerKey_sw 1, [7] hardcoded 0,
    # brightness 100 -> [8]=20, [9] untouched, [10] HARDCODED 16.
    legacy[0] = 0x07
    legacy[1] = 7
    legacy[2] = backend.profile
    legacy[3] = backend.layer
    legacy[4] = 7
    legacy[5] = 0
    legacy[6] = 1
    legacy[7] = 0
    legacy[8] = 20
    legacy[10] = 16
    assert props == bytes(legacy)


# --- Invalid values: refused BEFORE any report --------------------------------

@pytest.mark.parametrize("bad", [999, 0, -1, 1001, "fast", None, True, [500]])
def test_invalid_polling_rate_writes_nothing(wired, bad) -> None:
    backend, fake = wired
    with pytest.raises(BackendError):
        backend.set_polling_rate(bad, True)
    assert fake.capture == [], "invalid polling rate must not touch the wire"
    assert backend.polling_rate == 1000


@pytest.mark.parametrize("bad", [5, 0, 4, 32, "2ms", None, True, [2]])
def test_invalid_input_latency_writes_nothing(wired, bad) -> None:
    backend, fake = wired
    with pytest.raises(BackendError):
        backend.set_input_latency(bad, True)
    assert fake.capture == [], "invalid latency must not touch the wire"
    assert backend.input_latency_ms == 16


@pytest.mark.parametrize("bad", ["yes", 1, 0, None, [True]])
def test_invalid_side_lights_writes_nothing(wired, bad) -> None:
    backend, fake = wired
    with pytest.raises(BackendError):
        backend.set_side_lights(bad, True)
    assert fake.capture == [], "invalid side-lights payload must not touch the wire"
    assert backend.side_lights is True


# --- API round trip (controller run_action + get_state) ------------------------

def _controller(fake_hid, monkeypatch: pytest.MonkeyPatch):
    backend = StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))
    monkeypatch.setattr(app_module, "create_backend", lambda: backend)
    monkeypatch.setattr(app_module, "detect_gmmk_pro", lambda: [DEVICE])
    return app_module.OpenGloriousCoreWebController(), backend, fake_hid


def test_action_round_trip_exposes_new_state(fake_hid, monkeypatch) -> None:
    controller, backend, fake = _controller(fake_hid, monkeypatch)
    fake_hid.capture.clear()

    payload = controller.run_action("set_polling_rate", 500)
    assert payload["ok"] is True
    assert payload["state"]["polling_rate"] == 500
    assert len(_polling_reports(fake_hid)) == 1

    payload = controller.run_action("set_input_latency", 2)
    assert payload["ok"] is True
    assert payload["state"]["input_latency_ms"] == 2

    payload = controller.run_action("set_side_lights", False)
    assert payload["ok"] is True
    assert payload["state"]["side_lights"] is False
    props = _properties_reports(fake_hid)[-1]
    assert props[7] == 1 and props[10] == 2

    state = controller.get_state()
    assert state["state"]["polling_rate"] == 500
    assert state["state"]["input_latency_ms"] == 2
    assert state["state"]["side_lights"] is False


def test_action_invalid_returns_error_shape_without_io(fake_hid, monkeypatch) -> None:
    controller, backend, fake = _controller(fake_hid, monkeypatch)
    fake_hid.capture.clear()
    payload = controller.run_action("set_polling_rate", 999)
    assert payload["ok"] is False
    assert fake_hid.capture == []
    assert backend.polling_rate == 1000
