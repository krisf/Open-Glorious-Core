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

from open_glorious_core.backend import StockGmmkProBackend  # noqa: E402


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


@pytest.fixture()
def backend() -> StockGmmkProBackend:
    """Pure-unit backend: explicit hidraw=None means no device, no detection."""
    return StockGmmkProBackend(hidraw=None)
