"""Shared pytest fixtures.

Phase 0 HARD SAFETY RULE: the entire suite must run device-free. The
keyboard is physically attached to this rig, so we guarantee isolation on
two levels:

1. The ``backend`` fixture constructs ``StockGmmkProBackend`` with an
   explicit ``hidraw=None`` (no auto-detect, no device node).
2. An autouse fixture hard-blocks every method that can reach the device
   (``_send_feature_report``, ``_exchange_feature_report``) as well as
   ``_detect_stock_interface`` itself, so any accidental IO attempt
   fails loudly with an AssertionError instead of touching /dev/hidraw.

Tests that need to exercise auto-detect logic monkeypatch
``_detect_stock_interface`` with a fake (done inside the test, overriding
the autouse guard) and never instantiate the real detection path.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Exercise the patched src/ tree, not any copy installed in the venv.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from open_glorious_core.backend import (
    CMD_KEY_MATRIX,
    KEYMAP_CODES_OFFSET,
    KEYMAP_SLOTS,
    StockGmmkProBackend,
)  # noqa: E402


def _blocked(*args: object, **kwargs: object):
    raise AssertionError(
        "device IO attempted from the device-free Phase 0 test suite"
    )


@pytest.fixture(autouse=True)
def forbid_device_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Autouse guard: no test may send/read feature reports or auto-detect."""
    monkeypatch.setattr(StockGmmkProBackend, "_send_feature_report", _blocked)
    monkeypatch.setattr(StockGmmkProBackend, "_exchange_feature_report", _blocked)
    monkeypatch.setattr(StockGmmkProBackend, "_detect_stock_interface", _blocked)


@pytest.fixture(autouse=True)
def isolate_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Autouse guard (G12): last-good snapshots are per-test tmp files.

    ``lastgood.config_dir`` honours ``$OGC_CONFIG_DIR``, so no test can
    create/overwrite anything under the real ~/.config/open-glorious-core.
    """
    monkeypatch.setenv("OGC_CONFIG_DIR", str(tmp_path / "ogc-config"))


@pytest.fixture()
def backend() -> StockGmmkProBackend:
    """Pure-unit backend: explicit hidraw=None means no device, no detection."""
    return StockGmmkProBackend(hidraw=None)


class FakeHIDDevice:
    """Shared wire-level fake-HID double (G12 E2E).

    Records EVERY ``_send_feature_report`` (the raw 257-byte wire payload) in
    ``capture`` and answers the GET config mirror (0x81) with a fully BLANK
    response — the live GMMK Pro WB (320F:5092) shadow-buffer state where the
    mirror never populates. ``sets`` is the same list as ``capture`` under the
    name the wire tests already use; ``matrices`` is the subset of captured
    cmd-0x03 KeyMatrix reports (the write path the E2E test asserts on).
    """

    def __init__(self) -> None:
        self.capture: list[bytes] = []
        self.matrix: bytes | None = None
        self.bind: tuple[int, int] | None = None

    @property
    def sets(self) -> list[bytes]:
        return self.capture

    @property
    def matrices(self) -> list[bytes]:
        return [report for report in self.capture if report[1] == CMD_KEY_MATRIX]

    def codes_of(self, report: bytes) -> bytes:
        return bytes(report[KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS])

    def _send_feature_report(self, report: bytes) -> None:
        self.capture.append(bytes(report))
        if report[1] == CMD_KEY_MATRIX:
            self.matrix = bytes(
                report[KEYMAP_CODES_OFFSET : KEYMAP_CODES_OFFSET + KEYMAP_SLOTS]
            )
        elif report[1] == 0x01:
            self.bind = (report[2], report[3])

    def _exchange_feature_report(self, report: bytes) -> bytearray:
        # The live WB board: GET answers ALL ZERO (header included), so the
        # mirror is blank and the panel degrades to packaged factory defaults.
        return bytearray(len(report))


@pytest.fixture()
def fake_hid(monkeypatch: pytest.MonkeyPatch) -> FakeHIDDevice:
    """Wire a FakeHIDDevice at the backend IO boundary (device-free).

    Patching the class with the device's BOUND methods (pattern proven in
    tests/test_backend_keymap_shadow.wire) — the backend calls them as
    instance methods; self is already bound to the device."""
    device = FakeHIDDevice()
    monkeypatch.setattr(
        StockGmmkProBackend, "_send_feature_report", device._send_feature_report
    )
    monkeypatch.setattr(
        StockGmmkProBackend, "_exchange_feature_report", device._exchange_feature_report
    )
    return device
