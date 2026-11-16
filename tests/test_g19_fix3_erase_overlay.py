"""G19-FIX3 — disabling per-key after a PERSISTED apply must erase the overlay.

Physical-board proof (GMMK Pro WB 320f:5092, FW 0104, G19_FIX3_PLAN.md): an
Apply with per-key enabled sends the 3 layout pages AND the profile-update
report that PERSISTS them to flash. The effect report (0x02) has no per-key
exit flag, so disabling per-key afterwards leaves the painted keys colored —
"lighting unchanged". The only exit the board honors is an explicit erase:
3 all-zero cmd-0x06 pages, then the effect takes over (all white). The fix
tracks the persisted apply with the store-backed boolean per_key["applied"]:

* enabled Apply          -> pages + applied=True  (persisted to the store)
* disabled Apply, applied -> 3 ALL-ZERO pages + applied=False
* disabled, never applied -> NO pages at all: the never-used default wire
  stays byte-identical to HEAD (pinned here and in test_per_key_wiring).

`applied` rides the settings snapshot, so a restart/crash with a stuck
overlay still erases on the next Apply.

Zero device I/O: conftest hard-blocks real IO (NEVER /dev/hidraw*); the
FakeHIDDevice double captures the byte-exact reports at the IO boundary.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from open_glorious_core import app as app_module
from open_glorious_core import store
from open_glorious_core.backend import (
    CMD_SET_LAYOUT,
    G19_WIRE_LENGTH,
    PER_KEY_KEY_NUMBER,
    PER_KEY_PAGE_BYTES,
    PER_KEY_PAGE_COUNT,
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
SERIAL = DEVICE.physical_id


@pytest.fixture()
def wired(fake_hid):
    """Real StockGmmkProBackend over the FakeHIDDevice; capture starts clean."""
    backend = StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))
    fake_hid.capture.clear()
    return backend, fake_hid


def _controller(fake_hid, monkeypatch: pytest.MonkeyPatch):
    backend = StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))
    monkeypatch.setattr(app_module, "create_backend", lambda: backend)
    monkeypatch.setattr(app_module, "detect_gmmk_pro", lambda: [DEVICE])
    return app_module.OpenGloriousCoreWebController(), backend, fake_hid


def _cmd_reports(fake, cmd: int) -> list[bytes]:
    return [r for r in fake.capture if r[1] == cmd]


def _head_disabled_reports() -> list[bytes]:
    """HEAD's exact disabled-path wire (byte-identical defaults, as pinned
    in test_per_key_wiring): properties, effect, profile-update — nothing
    else."""
    properties = bytearray(256)
    properties[0] = 0x07
    properties[1] = 0x07
    properties[2] = 1  # profile
    properties[3] = 1  # layer
    properties[4] = 7  # default effect
    properties[6] = 1  # PerKey_sw (vendor always writes 1)
    properties[8] = 20  # brightness 100
    properties[10] = 16  # input latency raw ms
    effect = bytearray(256)
    effect[0] = 0x07
    effect[1] = 0x02
    effect[2] = 7
    effect[8] = 50  # 100 - speed(50)
    effect[9] = 20
    effect[15] = 255
    effect[16] = 255
    effect[17] = 255
    profile_update = bytearray(256)
    profile_update[0] = 0x07
    profile_update[1] = 0x01
    profile_update[2] = 1
    profile_update[3] = 1
    return [bytes(properties), bytes(effect), bytes(profile_update)]


def _painted_slots() -> dict:
    return {
        "0": {"vis": "lit", "rgb": [255, 0, 0]},  # Esc — the operator pair
        "1": {"vis": "lit", "rgb": [0, 0, 255]},  # Tab
    }


def _assert_erase_pages(fake, brightness_wire: int = 20) -> list[bytes]:
    """3 cmd-0x06 pages on the wire, every slot byte ZERO, G19-FIX2 shape."""
    pages = _cmd_reports(fake, CMD_SET_LAYOUT)
    assert len(pages) == PER_KEY_PAGE_COUNT == 3
    for page_index, page in enumerate(pages):
        assert len(page) == G19_WIRE_LENGTH == 256, "256 wire bytes per page"
        assert page[0] == 0x07
        assert page[4] == PER_KEY_KEY_NUMBER == 83
        assert page[5] == page_index, "page selectors 0, 1, 2"
        # brightness header byte: same mapping as the normal builder
        assert page[6] == brightness_wire
        # the FULL 200-byte slot payload is zero (ignore stored slots)
        assert page[8 : 8 + PER_KEY_PAGE_BYTES] == bytes(PER_KEY_PAGE_BYTES)
        assert page.count(0xFF) == 0 and page.count(0xFE) == 0
    return pages


# --- 1: enabled Apply paints AND records the persisted flag -------------------

def test_enabled_apply_sends_pages_and_persists_applied_true(fake_hid, monkeypatch) -> None:
    controller, backend, fake = _controller(fake_hid, monkeypatch)
    assert controller.run_action("set_per_key_enable", True)["ok"] is True
    assert controller.run_action(
        "set_per_key_color", {"slot": 0, "rgb": [255, 0, 0]}
    )["ok"] is True
    assert fake.capture == [], "the settings actions alone never touch the wire"

    # Apply: any lighting setter runs the full _apply_current_state chain.
    assert controller.run_action("set_effect", 7)["ok"] is True

    assert [r[1] for r in fake.capture] == [0x07, 0x02, 0x06, 0x06, 0x06, 0x01]
    assert len(_cmd_reports(fake, CMD_SET_LAYOUT)) == 3
    assert backend.per_key["applied"] is True

    # the flag PERSISTED — a restart must still know about the stuck overlay
    settings = store.get_layer(store.load(), SERIAL, 1, 1)["settings"]
    assert settings["per_key"]["applied"] is True


# --- 2: disable + Apply after a persisted apply ERASES ------------------------

def test_disable_after_persisted_apply_sends_three_all_zero_pages(wired) -> None:
    backend, fake = wired
    backend.set_per_key_enabled(True)
    backend.per_key["slots"] = _painted_slots()
    backend._apply_current_state()
    assert backend.per_key["applied"] is True
    assert len(_cmd_reports(fake, CMD_SET_LAYOUT)) == 3

    backend.set_per_key_enabled(False)
    fake.capture.clear()
    backend._apply_current_state()

    # the effect report STILL goes out, and the erase rides before the
    # profile-update (vendor order preserved)
    assert [r[1] for r in fake.capture] == [0x07, 0x02, 0x06, 0x06, 0x06, 0x01]
    assert fake.capture[1][1] == 0x02, "effect report still sent"
    pages = _assert_erase_pages(fake)
    for page in pages:
        assert page[8 : 8 + PER_KEY_PAGE_BYTES] == bytes(PER_KEY_PAGE_BYTES)
    assert backend.per_key["applied"] is False


# --- 3: never-enabled, never-applied Apply stays byte-identical to HEAD -------

def test_never_applied_disable_sends_no_pages_wire_byte_identical(wired) -> None:
    backend, fake = wired
    assert backend.per_key["applied"] is False

    backend._apply_current_state()

    # exact report SEQUENCE and COUNT of HEAD's disabled path — nothing else
    assert [r[1] for r in fake.capture] == [0x07, 0x02, 0x01]
    assert _cmd_reports(fake, CMD_SET_LAYOUT) == [], "NO layout pages ever"
    # and every report is byte-identical to HEAD's disabled wire
    assert fake.capture == _head_disabled_reports()
    assert backend.per_key["applied"] is False

    # disabling while never applied changes nothing on any later Apply
    backend.set_per_key_enabled(False)
    fake.capture.clear()
    backend._apply_current_state()
    assert fake.capture == _head_disabled_reports()


# --- 4: the erase ignores stored slots ----------------------------------------

def test_erase_is_all_zero_regardless_of_stored_colors(wired) -> None:
    backend, fake = wired
    backend.set_per_key_enabled(True)
    backend.per_key["brightness"] = 55
    backend.per_key["slots"] = _painted_slots()
    backend._apply_current_state()
    painted = _cmd_reports(fake, CMD_SET_LAYOUT)
    assert painted[0][8 : 8 + 5] == bytes([0xFF, 0, 255, 0, 0]), "page 0 painted"

    backend.set_per_key_enabled(False)
    fake.capture.clear()
    backend._apply_current_state()

    # all-zero pages EVEN THOUGH the store still holds the colors ...
    _assert_erase_pages(fake, brightness_wire=round(55 * 20 / 100))
    # ... which stay untouched for the next re-enable (erase never wipes slots)
    assert backend.per_key["slots"] == _painted_slots()


# --- 5: store validator round-trip --------------------------------------------

def test_store_validates_applied_and_old_records_load_unchanged() -> None:
    # accepts a bool and echoes it back
    assert store.validate_per_key({"enabled": False, "applied": True}) == {
        "enabled": False,
        "applied": True,
    }
    # absent -> optional (mirrors the enabled handling); readers default False
    legacy = store.validate_per_key({"enabled": True, "brightness": 80, "slots": {}})
    assert "applied" not in legacy
    assert legacy.get("applied", False) is False
    # non-bool rejected (1/0 are NOT booleans here)
    for bad in ("yes", 1, 0, None, [True]):
        with pytest.raises(store.StoreError):
            store.validate_per_key({"applied": bad})
    # an OLD store record (no applied key) persists and loads unchanged
    store.record(
        SERIAL,
        1,
        1,
        settings={"per_key": {"enabled": True, "brightness": 90, "slots": {}}},
    )
    settings = store.get_layer(store.load(), SERIAL, 1, 1)["settings"]
    assert settings["per_key"] == {"enabled": True, "brightness": 90, "slots": {}}
    assert settings["per_key"].get("applied", False) is False
    # ... and the merged new-shape write round-trips through save/load
    store.record(SERIAL, 1, 1, settings={"per_key": {"enabled": True, "applied": True}})
    settings = store.get_layer(store.load(), SERIAL, 1, 1)["settings"]
    assert settings["per_key"]["applied"] is True


# --- 6: restart correctness — a stuck overlay erases on the next Apply --------

def test_restart_with_stuck_overlay_erases_on_next_apply(wired) -> None:
    backend, fake = wired
    # persist the stuck state: disabled, but the board still HOLDS the overlay
    store.record(
        SERIAL,
        1,
        1,
        settings={
            "per_key": {
                "enabled": False,
                "applied": True,
                "brightness": 100,
                "slots": _painted_slots(),
            }
        },
    )

    # RESTART: a fresh backend hydrates the stored block (the store is the
    # source of truth — same shape, same defaults).
    stored = store.get_layer(store.load(), SERIAL, 1, 1)["settings"]["per_key"]
    backend.per_key = {**backend.per_key, **stored}
    assert backend.per_key["enabled"] is False
    assert backend.per_key["applied"] is True

    backend._apply_current_state()

    assert [r[1] for r in fake.capture] == [0x07, 0x02, 0x06, 0x06, 0x06, 0x01]
    _assert_erase_pages(fake)
    assert backend.per_key["applied"] is False
    # the colors themselves stay stored for a future re-enable
    assert backend.per_key["slots"] == _painted_slots()


def test_restart_from_old_record_without_applied_key_is_safe(wired) -> None:
    """A store written before G19-FIX3 has no "applied" key: loading it must
    read as applied=False (no crash, no pages) — the never-used default wire
    stays byte-identical."""
    backend, fake = wired
    store.record(
        SERIAL,
        1,
        1,
        settings={"per_key": {"enabled": False, "brightness": 100, "slots": {}}},
    )
    stored = store.get_layer(store.load(), SERIAL, 1, 1)["settings"]["per_key"]
    assert "applied" not in stored
    backend.per_key.update(stored)  # old-shape dict: NO "applied" key at all

    backend._apply_current_state()

    assert [r[1] for r in fake.capture] == [0x07, 0x02, 0x01]
    assert _cmd_reports(fake, CMD_SET_LAYOUT) == []
    assert fake.capture == _head_disabled_reports()
