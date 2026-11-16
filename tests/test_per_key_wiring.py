"""G19A Commit 1 — win-lock flag + per-key RGB layout writer wiring.

Pinned protocol facts (G19A_PLAN.md — vendor Glorious CORE 2.1.22 ground
truth, do NOT re-derive):

* Properties report (cmd 0x07): byte [5] is Win_Lock — the vendor HARDCODES
  0 (no win-lock UI ships for this board). Our toggle defaults UNLOCKED so
  the default wire stays byte-identical; flipping it changes ONLY byte [5].
* Per-key layout (cmd 0x06, standalone 264-byte report): [2]/[3] are the
  profile/layer selectors, [4]=KeyNumber 83, [5]=page 0..2,
  [6]=round(per-key brightness * 20 / 100), and [8..207] carry the 200-byte
  page slice of the 600-byte layout buffer (120 slots x 5 bytes:
  +0 vis 0x00/0xFF/0xFE, +1 the slot index itself, +2..4 RGB; absent slot
  = five zero bytes). Side LEDs are ordinary slots — no special encoding.
* Vendor Apply order: properties 0x07 -> effect 0x02 -> layout pages
  0x06 x3 -> profile-update 0x01. Pages join the flow ONLY while per-key
  mode is enabled.
* There is NO LED/win-lock readback on this board (blank-shadow cmd-129
  mirror): the setters are local-mirror-only, the store is the source of
  truth, and imports stage without ever touching the device.

Zero device I/O: conftest hard-blocks real IO; FakeHIDDevice captures the
byte-exact reports at the IO boundary.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from open_glorious_core import app as app_module
from open_glorious_core import store
from open_glorious_core.backend import (
    CMD_SET_LAYOUT,
    PER_KEY_KEY_NUMBER,
    PER_KEY_LAYOUT_BYTES,
    PER_KEY_PAGE_BYTES,
    PER_KEY_VIS_BYTES,
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


def _default_properties() -> bytes:
    """The DEFAULT properties report, hand-pinned byte for byte (the wire
    HEAD emitted before G19A — win-lock off, side lights on, 100%, 16 ms)."""
    expected = bytearray(256)
    expected[0] = 0x07  # report ID
    expected[1] = 0x07  # properties/effect cmd
    expected[2] = 1  # profile
    expected[3] = 1  # layer
    expected[4] = 7  # default effect (Normally On)
    expected[5] = 0  # Win_Lock — default UNLOCKED keeps HEAD's bytes
    expected[6] = 1  # PerKey_sw — vendor always writes 1 (already does)
    expected[7] = 0  # side lights ON (inverted)
    expected[8] = 20  # brightness 100 * 20 / 100
    expected[10] = 16  # input latency raw ms
    return bytes(expected)


def _default_effect() -> bytes:
    expected = bytearray(256)
    expected[0] = 0x07
    expected[1] = 0x02
    expected[2] = 7  # effect
    expected[8] = 50  # 100 - speed(50)
    expected[9] = 20  # brightness scaled
    # effect 7 is NOT multicolor: [10] and [12] stay 0
    expected[15] = 255
    expected[16] = 255
    expected[17] = 255
    return bytes(expected)


# --- defaults: the wire is byte-identical to HEAD -----------------------------

def test_disabled_defaults_emit_exactly_the_head_reports_byte_identical(wired) -> None:
    backend, fake = wired
    assert backend.win_lock is False
    assert backend.per_key == {"enabled": False, "brightness": 100, "slots": {}}

    backend._apply_current_state()

    assert [r[1] for r in fake.capture] == [0x07, 0x02, 0x01]
    assert fake.capture[0] == _default_properties()  # FULL 256-byte equality
    assert fake.capture[1] == _default_effect()  # FULL 256-byte equality
    profile_update = bytearray(256)
    profile_update[0] = 0x07
    profile_update[1] = 0x01
    profile_update[2] = 1
    profile_update[3] = 1
    assert fake.capture[2] == bytes(profile_update)
    assert _cmd_reports(fake, CMD_SET_LAYOUT) == [], "disabled mode adds NO extra reports"


def test_win_lock_toggle_writes_nothing_until_apply(wired) -> None:
    backend, fake = wired
    message = backend.set_win_lock(True)
    assert fake.capture == [], "the toggle itself must not touch the device"
    assert "locked" in message.lower()

    backend._apply_current_state()
    properties = _cmd_reports(fake, 0x07)[0]
    assert properties[5] == 1, "byte [5] flips to locked"
    # ...and ONLY byte [5] differs from the pinned default report.
    head = bytearray(_default_properties())
    head[5] = 1
    assert properties == bytes(head)

    backend.set_win_lock(False)
    fake.capture.clear()
    backend._apply_current_state()
    assert _cmd_reports(fake, 0x07)[0] == _default_properties()


@pytest.mark.parametrize("bad", ["true", 1, None, 0.0])
def test_win_lock_rejects_non_bool_before_any_write(wired, bad) -> None:
    backend, fake = wired
    with pytest.raises(BackendError):
        backend.set_win_lock(bad)
    assert backend.win_lock is False
    assert fake.capture == []


# --- layout buffer: byte-exact ------------------------------------------------

def test_layout_buffer_slots_are_five_bytes_each_byte_exact(wired) -> None:
    backend, _ = wired
    backend.per_key["slots"] = {
        "0": {"vis": "lit", "rgb": [255, 0, 0]},
        "3": {"vis": "breath", "rgb": [0, 0, 255]},
        "15": {"vis": "lit", "rgb": [10, 20, 30]},  # a side LED: ordinary slot
        "119": {"vis": "off", "rgb": [1, 2, 3]},
    }
    layout = backend.build_per_key_layout()
    assert len(layout) == PER_KEY_LAYOUT_BYTES == 600
    assert layout[0:5] == bytes([0xFF, 0, 255, 0, 0])
    assert layout[15:20] == bytes([0xFE, 3, 0, 0, 255])
    assert layout[75:80] == bytes([0xFF, 15, 10, 20, 30])
    assert layout[595:600] == bytes([0x00, 119, 1, 2, 3])
    assert PER_KEY_VIS_BYTES == {"off": 0x00, "lit": 0xFF, "breath": 0xFE}
    # every untouched slot stays five zero bytes
    touched = {0, 3, 15, 119}
    for slot in range(120):
        if slot not in touched:
            base = slot * 5
            assert layout[base : base + 5] == bytes(5), f"slot {slot} must stay zero"


def test_clear_returns_the_slot_to_five_zero_bytes(wired) -> None:
    backend, fake = wired
    backend.set_per_key_color(42, [9, 9, 9])
    assert backend.per_key["slots"]["42"] == {"vis": "lit", "rgb": [9, 9, 9]}
    backend.clear_per_key_slot(42)
    assert "42" not in backend.per_key["slots"]
    assert backend.build_per_key_layout()[210:215] == bytes(5)
    backend.clear_per_key_slot(42)  # idempotent
    assert fake.capture == []


def test_color_update_keeps_vis_breathing_stays_breathing(wired) -> None:
    backend, fake = wired
    backend.set_per_key_color(7, [1, 2, 3], vis="breath")
    assert backend.per_key["slots"]["7"]["vis"] == "breath"
    backend.set_per_key_color(7, [200, 0, 0])  # color picker: rgb only
    assert backend.per_key["slots"]["7"] == {"vis": "breath", "rgb": [200, 0, 0]}
    assert fake.capture == []


@pytest.mark.parametrize(
    ("slot", "rgb", "vis"),
    [
        (120, [0, 0, 0], None),  # slot out of range
        (-1, [0, 0, 0], None),
        (True, [0, 0, 0], None),  # bool is not a slot index
        ("0", [0, 0, 0], None),
        (5, [0, 0, 256], None),  # rgb channel out of range
        (5, [0, 0], None),  # not a triple
        (5, [0, 0, 25.5], None),
        (5, [0, 0, 0], "on"),  # vis literal not in the three vendor names
        (5, [0, 0, 0], "0xFE"),  # string radix bug class
    ],
)
def test_invalid_slot_color_vis_refused_before_any_write(wired, slot, rgb, vis) -> None:
    backend, fake = wired
    with pytest.raises(BackendError):
        backend.set_per_key_color(slot, rgb, vis)
    assert backend.per_key["slots"] == {}
    assert fake.capture == []


@pytest.mark.parametrize(("percent", "wire"), [(0, 0), (55, 11), (100, 20)])
def test_per_key_brightness_maps_to_twenty_levels(wired, percent, wire) -> None:
    backend, fake = wired
    backend.set_per_key_brightness(percent)
    assert backend.per_key["brightness"] == percent
    assert fake.capture == []
    backend.set_per_key_enabled(True)
    backend._apply_current_state()
    for page in _cmd_reports(fake, CMD_SET_LAYOUT):
        assert page[6] == wire


@pytest.mark.parametrize("bad", [101, -1, "50", True, None])
def test_per_key_brightness_rejects_out_of_range(wired, bad) -> None:
    backend, fake = wired
    with pytest.raises(BackendError):
        backend.set_per_key_brightness(bad)
    assert fake.capture == []


# --- the enabled Apply flow: vendor order and exact pages ----------------------

def _few_slots() -> dict:
    return {
        "0": {"vis": "lit", "rgb": [255, 0, 0]},
        "15": {"vis": "breath", "rgb": [0, 255, 0]},  # side LED slot
        "119": {"vis": "lit", "rgb": [0, 0, 255]},
    }


def test_enabled_apply_sends_properties_effect_pages_then_profile(wired) -> None:
    backend, fake = wired
    backend.set_per_key_enabled(True)
    backend.per_key["brightness"] = 55
    backend.per_key["slots"] = _few_slots()
    assert fake.capture == [], "enabling writes nothing by itself"

    backend._apply_current_state()

    assert [r[1] for r in fake.capture] == [0x07, 0x02, 0x06, 0x06, 0x06, 0x01]
    layout = backend.build_per_key_layout()
    pages = _cmd_reports(fake, CMD_SET_LAYOUT)
    assert len(pages) == 3, "three pages, sent sequentially"
    for page_index, page in enumerate(pages):
        assert len(page) == 264
        assert page[0] == 0x07
        assert page[2] == backend.profile
        assert page[3] == backend.layer
        assert page[4] == PER_KEY_KEY_NUMBER == 83
        assert page[5] == page_index, "only byte [5] changes between pages"
        assert page[6] == round(55 * 20 / 100)
        start = page_index * PER_KEY_PAGE_BYTES
        assert page[8 : 8 + PER_KEY_PAGE_BYTES] == layout[start : start + PER_KEY_PAGE_BYTES]
    # page 0 carries slot 0, page 2 carries slot 119 (self-identifying +1)
    assert pages[0][8 : 8 + 5] == bytes([0xFF, 0, 255, 0, 0])
    assert pages[0][8 + 75 : 8 + 80] == bytes([0xFE, 15, 0, 255, 0])
    # slot 119 sits at layout offset 595 -> page-2 offset 595 - 400 = 195
    assert pages[2][8 + 195 : 8 + 200] == bytes([0xFF, 119, 0, 0, 255])


def test_disabling_again_removes_the_pages_from_the_flow(wired) -> None:
    backend, fake = wired
    backend.set_per_key_enabled(True)
    backend.per_key["slots"] = _few_slots()
    backend._apply_current_state()
    assert len(_cmd_reports(fake, CMD_SET_LAYOUT)) == 3
    backend.set_per_key_enabled(False)
    fake.capture.clear()
    backend._apply_current_state()
    assert [r[1] for r in fake.capture] == [0x07, 0x02, 0x01]
    assert fake.capture[0] == _default_properties()


# --- controller + store ---------------------------------------------------------

def test_win_lock_and_per_key_persist_to_the_local_store(fake_hid, monkeypatch) -> None:
    controller, backend, _ = _controller(fake_hid, monkeypatch)
    assert controller.run_action("set_win_lock", True)["ok"] is True
    assert controller.run_action("set_per_key_enable", True)["ok"] is True
    assert controller.run_action(
        "set_per_key_brightness", 40
    )["ok"] is True
    assert controller.run_action(
        "set_per_key_color", {"slot": 66, "rgb": [12, 34, 56], "vis": "breath"}
    )["ok"] is True

    block = store.get_layer(store.load(), SERIAL, 1, 1)
    assert block is not None
    settings = block["settings"]
    assert settings["win_lock"] is True
    assert settings["per_key"] == {
        "enabled": True,
        "brightness": 40,
        "slots": {"66": {"vis": "breath", "rgb": [12, 34, 56]}},
    }


def test_get_state_exposes_win_lock_and_per_key_mirror(fake_hid, monkeypatch) -> None:
    controller, backend, _ = _controller(fake_hid, monkeypatch)
    state = controller.get_state()
    assert state["ok"] is True
    assert state["state"]["win_lock"] is False
    assert state["state"]["per_key"] == {"enabled": False, "brightness": 100, "slots": {}}

    backend.set_per_key_color(3, [1, 2, 3])
    state = controller.get_state()
    assert state["state"]["per_key"]["slots"] == {"3": {"vis": "lit", "rgb": [1, 2, 3]}}


@pytest.mark.parametrize(
    ("value", "needle"),
    [
        ({"slot": 120, "rgb": [0, 0, 0]}, "0-119"),
        ({"slot": 0, "rgb": [0, 0, 256]}, "0-255"),
        ({"slot": 0, "rgb": [0, 0, 0], "vis": "glow"}, "off"),
        ({"slot": "x", "rgb": [0, 0, 0]}, "slot"),
        ("not-an-object", "object"),
    ],
)
def test_per_key_color_action_rejects_invalid_payloads(fake_hid, monkeypatch, value, needle) -> None:
    controller, _, fake = _controller(fake_hid, monkeypatch)
    result = controller.run_action("set_per_key_color", value)
    assert result["ok"] is False
    assert needle in result["message"].lower() or needle in result["message"]
    assert fake.capture == []


# --- HTTP routes ---------------------------------------------------------------

def _serve(ctrl):
    Handler = app_module.AppRequestHandler

    def handler(*args, **kwargs):
        return Handler(*args, directory=str(app_module.STATIC_DIR), controller=ctrl, **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _post_json(base: str, path: str, payload) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    request = urllib.request.Request(
        base + path, data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {}


def test_http_settings_routes_ok_and_400_and_zero_writes(fake_hid, monkeypatch) -> None:
    controller, _, fake = _controller(fake_hid, monkeypatch)
    server, base = _serve(controller)
    try:
        status, result = _post_json(base, "/api/win-lock", {"value": True})
        assert status == 200 and result["ok"] is True
        assert result["state"]["win_lock"] is True

        status, result = _post_json(base, "/api/per-key/enable", {"value": True})
        assert status == 200 and result["state"]["per_key"]["enabled"] is True

        status, result = _post_json(base, "/api/per-key/brightness", {"value": 65})
        assert status == 200 and result["state"]["per_key"]["brightness"] == 65

        status, result = _post_json(
            base, "/api/per-key/color", {"value": {"slot": 5, "rgb": [1, 2, 3]}}
        )
        assert status == 200
        assert result["state"]["per_key"]["slots"] == {"5": {"vis": "lit", "rgb": [1, 2, 3]}}

        # 400s: slot 120, rgb 256, bad vis, brightness 101
        for path, value in [
            ("/api/per-key/color", {"slot": 120, "rgb": [0, 0, 0]}),
            ("/api/per-key/color", {"slot": 5, "rgb": [0, 0, 256]}),
            ("/api/per-key/color", {"slot": 5, "rgb": [0, 0, 0], "vis": "bright"}),
            ("/api/per-key/brightness", {"value": 101}),
        ]:
            status, result = _post_json(base, path, {"value": value})
            assert status == 400 and result["ok"] is False, (path, value)

        status, result = _post_json(base, "/api/per-key/clear", {"value": 5})
        assert status == 200 and result["state"]["per_key"]["slots"] == {}

        assert fake.capture == [], "settings routes NEVER write the device"

        # persisted round trip through the store
        settings = store.get_layer(store.load(), SERIAL, 1, 1)["settings"]
        assert settings["win_lock"] is True
        assert settings["per_key"] == {"enabled": True, "brightness": 65, "slots": {}}

        with urllib.request.urlopen(base + "/api/state") as response:
            state = json.loads(response.read())
        assert state["state"]["win_lock"] is True
        assert state["state"]["per_key"]["brightness"] == 65
    finally:
        server.shutdown()
        server.server_close()


def test_export_carries_win_lock_and_per_key(fake_hid, monkeypatch) -> None:
    controller, _, fake = _controller(fake_hid, monkeypatch)
    assert controller.run_action("set_win_lock", True)["ok"] is True
    assert controller.run_action(
        "set_per_key_color", {"slot": 11, "rgb": [7, 7, 7]}
    )["ok"] is True
    document = controller.export_profile()["document"]
    assert document["settings"]["win_lock"] is True
    assert document["settings"]["per_key"]["slots"] == {"11": {"vis": "lit", "rgb": [7, 7, 7]}}
    assert fake.capture == []


def test_import_validates_per_key_and_stages_without_writes(fake_hid, monkeypatch) -> None:
    controller, _, fake = _controller(fake_hid, monkeypatch)
    valid = {
        "settings": {
            "win_lock": True,
            "per_key": {
                "enabled": True,
                "brightness": 80,
                "slots": {"9": {"vis": "breath", "rgb": [10, 20, 30]}},
            },
        }
    }
    result = controller.import_profile(valid)
    assert result["ok"] is True, result["message"]
    assert fake.capture == [], "an import can never write the device"
    settings = store.get_layer(store.load(), SERIAL, 1, 1)["settings"]
    assert settings["per_key"]["slots"]["9"] == {"vis": "breath", "rgb": [10, 20, 30]}

    for bad_settings in (
        {"per_key": {"slots": {"120": {"vis": "lit", "rgb": [0, 0, 0]}}}},
        {"per_key": {"slots": {"9": {"vis": "lit", "rgb": [0, 0, 256]}}}},
        {"per_key": {"slots": {"9": {"vis": "glow", "rgb": [0, 0, 0]}}}},
        {"per_key": {"enabled": "yes"}},
    ):
        result = controller.import_profile({"settings": bad_settings})
        assert result["ok"] is False, bad_settings
    assert fake.capture == []
    settings = store.get_layer(store.load(), SERIAL, 1, 1)["settings"]
    assert settings["per_key"]["brightness"] == 80, "rejected imports change nothing"
