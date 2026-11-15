"""G18 commit 3 — local store + blank-mirror fallback + profile export/import.

Pinned behaviors (G18 plan P4 + commit 3):

* store.py: strict schema round-trip, ATOMIC writes (tmp + os.replace —
  a failed write can never corrupt the previous document), corrupt/missing
  file -> empty document, NEVER a crash.
* Every successful settings write and keymap apply persists to the store.
* Blank-mirror fallback: blank GET mirror + stored keymap -> the STORED
  state is displayed, source "store", exact banner
  "Device mirror is blank — showing your last saved configuration.";
  blank mirror + EMPTY store -> today's factory-default display unchanged.
* /api/export: JSON attachment (Content-Disposition), export shape pinned.
* /api/import: validates schema + every code against the catalog + slot
  ints 0..119; on success stores + stages, on failure stages NOTHING — and
  import/export PHYSICALLY NEVER WRITE THE DEVICE (zero fake-HID reports).

Zero device I/O (conftest blocks the real IO boundary; $XDG_DATA_HOME is
isolated per test by the autouse guard in conftest.py).
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
    KEYMAP_SLOTS,
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


# --- store.py unit tests (root pinned to tmp) --------------------------------

def test_store_round_trip_and_export_shape(tmp_path: Path) -> None:
    settings = {"brightness": 60, "polling_rate": 500, "side_lights": False}
    keymap = {"0": 0x04, "60": 0x39}
    store.record(SERIAL, 2, 3, settings=settings, keymap=keymap, root=tmp_path)

    document = store.load(root=tmp_path)
    assert document["version"] == 1
    block = store.get_layer(document, SERIAL, 2, 3)
    assert block is not None
    assert block["settings"] == settings
    assert block["keymap"] == keymap
    assert store.store_path(tmp_path) == tmp_path / "open-glorious-core" / "store.json"

    exported = store.export_layer(SERIAL, 2, 3, root=tmp_path)
    assert exported == {
        "version": 1,
        "device_serial": SERIAL,
        "profile": 2,
        "layer": 3,
        "settings": settings,
        "keymap": keymap,
    }
    assert store.get_layer(document, SERIAL, 1, 1) is None
    assert store.export_layer("other-serial", 2, 3, root=tmp_path) is None


def test_store_merges_partial_updates(tmp_path: Path) -> None:
    store.record(SERIAL, 1, 1, settings={"brightness": 50}, root=tmp_path)
    store.record(SERIAL, 1, 1, settings={"polling_rate": 250, "brightness": 80}, root=tmp_path)
    store.record(SERIAL, 1, 1, keymap={"5": 0x15}, root=tmp_path)
    store.record(SERIAL, 1, 1, keymap={"6": 0x16, "5": 0x06}, root=tmp_path)
    block = store.get_layer(store.load(root=tmp_path), SERIAL, 1, 1)
    assert block["settings"] == {"brightness": 80, "polling_rate": 250}
    assert block["keymap"] == {"5": 0x06, "6": 0x16}


def test_store_write_is_atomic(tmp_path: Path, monkeypatch) -> None:
    """A crash between tmp-write and rename leaves the OLD document intact,
    and a completed save never leaves the .tmp file behind."""
    store.record(SERIAL, 1, 1, settings={"brightness": 50}, root=tmp_path)
    path = store.store_path(tmp_path)
    before = path.read_text(encoding="utf-8")

    def crash_mid_write(self: Path, *args, **kwargs):
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(Path, "write_text", crash_mid_write)
    with pytest.raises(OSError):
        store.record(SERIAL, 1, 1, settings={"brightness": 99}, root=tmp_path)
    monkeypatch.undo()

    assert path.read_text(encoding="utf-8") == before  # old doc intact
    assert store.get_layer(store.load(root=tmp_path), SERIAL, 1, 1)["settings"] == {
        "brightness": 50
    }

    store.record(SERIAL, 1, 1, settings={"brightness": 99}, root=tmp_path)
    assert not path.with_name(path.name + ".tmp").exists()
    assert store.get_layer(store.load(root=tmp_path), SERIAL, 1, 1)["settings"] == {
        "brightness": 99
    }


@pytest.mark.parametrize("garbage", ["", "not json{", "[1,2,3]", "17"])
def test_corrupt_file_loads_empty(tmp_path: Path, garbage: str) -> None:
    path = store.store_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(garbage, encoding="utf-8")
    assert store.load(root=tmp_path) == {"version": 1, "devices": {}}


@pytest.mark.parametrize(
    "document",
    [
        {"version": 2, "devices": {}},                      # wrong version
        {"version": 1, "devices": []},                       # devices not object
        {"version": 1, "devices": {"s": {"profiles": "x"}}},  # profiles not object
        {"version": 1, "devices": {"s": {"profiles": {"9": {"layers": {}}}}}},  # profile 9
        {"version": 1, "devices": {"s": {"profiles": {"1": {"layers": {"0": {}}}}}}},  # layer 0
        {"version": 1, "devices": {"s": {"profiles": {"1": {"layers": {"1": {"keymap": {"120": 4}}}}}}}},  # slot 120
        {"version": 1, "devices": {"s": {"profiles": {"1": {"layers": {"1": {"settings": {"brightness": 101}}}}}}}},  # bright 101
        {"version": 1, "devices": {"s": {"profiles": {"1": {"layers": {"1": {"oops": 1}}}}}}},  # unknown key
    ],
)
def test_schema_violations_load_empty(tmp_path: Path, document: dict) -> None:
    path = store.store_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    assert store.load(root=tmp_path) == {"version": 1, "devices": {}}


@pytest.mark.parametrize(
    "keymap",
    [
        "not-a-dict",
        {"a": 7},          # non-int slot key
        {"-1": 7},         # negative slot
        {"120": 7},        # slot above the matrix
        {"0": True},       # bool code
        {"0": 256},        # code above a byte
        {"0": "7"},        # string code
    ],
)
def test_keymap_validation_rejects(keymap) -> None:
    with pytest.raises(store.StoreError):
        store.validate_keymap(keymap)


@pytest.mark.parametrize(
    "settings",
    [
        "not-a-dict",
        {"brightness": 101},
        {"speed": -1},
        {"polling_rate": 999},
        {"input_latency_ms": 5},
        {"side_lights": 1},          # int is NOT a bool here
        {"color_name": ""},
        {"battery": 42},             # unknown key
    ],
)
def test_settings_validation_rejects(settings) -> None:
    with pytest.raises(store.StoreError):
        store.validate_settings(settings)


# --- controller wiring: persist on success, fallback, never crash ------------

def _controller(monkeypatch: pytest.MonkeyPatch):
    backend = StockGmmkProBackend(hidraw=Path("/dev/hidraw-fake"))
    monkeypatch.setattr(app_module, "create_backend", lambda: backend)
    monkeypatch.setattr(app_module, "detect_gmmk_pro", lambda: [DEVICE])
    return app_module.OpenGloriousCoreWebController(), backend


def _applied_codes_offset_one() -> list[int]:
    """Factory table with slot 60 moved to NumpadDivide (0x54)."""
    factory = list(StockGmmkProBackend(hidraw=None).default_keymap_table())
    codes = [int(byte) for byte in factory]
    codes[60] = 0x54
    return codes


def test_settings_write_persists_to_store(fake_hid, monkeypatch) -> None:
    controller, backend = _controller(monkeypatch)
    payload = controller.run_action("set_polling_rate", 500)
    assert payload["ok"] is True
    block = store.get_layer(store.load(), SERIAL, backend.profile, backend.layer)
    assert block is not None
    assert block["settings"]["polling_rate"] == 500
    assert block["settings"]["brightness"] == 100  # full snapshot rides along


def test_failed_settings_write_persists_nothing(fake_hid, monkeypatch) -> None:
    controller, _ = _controller(monkeypatch)
    payload = controller.run_action("set_polling_rate", 999)
    assert payload["ok"] is False
    assert store.load() == {"version": 1, "devices": {}}


def test_apply_keymap_persists_and_fallback_shows_it(fake_hid, monkeypatch) -> None:
    controller, _ = _controller(monkeypatch)
    codes = _applied_codes_offset_one()
    payload = controller.run_action("apply-keymap", {"codes": codes})
    assert payload["ok"] is True

    block = store.get_layer(store.load(), SERIAL, 1, 1)
    assert block is not None
    assert {int(k): v for k, v in block["keymap"].items()} == {
        slot: code for slot, code in enumerate(codes)
    }

    # Blank mirror (FakeHIDDevice answers zeros) + stored keymap -> STORED
    # state on screen with the honest fallback banner.
    key_state = controller.get_keystate()
    assert key_state["ok"] is True
    assert key_state["keymap"]["source"] == "store"
    assert key_state["message"] == app_module.KEYMAP_STORE_FALLBACK_BANNER
    assert key_state["message"] == (
        "Device mirror is blank — showing your last saved configuration."
    )
    slot_codes = {slot["slot"]: slot["code"] for slot in key_state["keymap"]["slots"]}
    assert slot_codes[60] == 0x54
    factory = StockGmmkProBackend(hidraw=None).default_keymap_table()
    assert slot_codes[0] == int(factory[0])


def test_reset_keymap_store_agrees_with_device(fake_hid, monkeypatch) -> None:
    controller, _ = _controller(monkeypatch)
    assert controller.run_action("apply-keymap", {"codes": _applied_codes_offset_one()})["ok"]
    assert controller.run_action("reset-keymap", None)["ok"] is True
    # After a factory reset the device holds the factory table — the store
    # must NOT resurrect the old remap through the fallback.
    key_state = controller.get_keystate()
    factory = StockGmmkProBackend(hidraw=None).default_keymap_table()
    slot_codes = {slot["slot"]: slot["code"] for slot in key_state["keymap"]["slots"]}
    assert slot_codes[60] == int(factory[60])


def test_blank_mirror_empty_store_factory_path_unchanged(fake_hid, monkeypatch) -> None:
    controller, _ = _controller(monkeypatch)
    key_state = controller.get_keystate()
    assert key_state["ok"] is True
    assert key_state["keymap"]["source"] == "factory-defaults"
    assert key_state["message"] == app_module.KEYMAP_BLANK_MIRROR_MESSAGE
    factory = StockGmmkProBackend(hidraw=None).default_keymap_table()
    slot_codes = {slot["slot"]: slot["code"] for slot in key_state["keymap"]["slots"]}
    assert slot_codes[60] == int(factory[60])


def test_corrupt_store_file_never_crashes_the_panel(fake_hid, monkeypatch) -> None:
    controller, _ = _controller(monkeypatch)
    path = store.store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ this is not json", encoding="utf-8")
    key_state = controller.get_keystate()  # must degrade, not raise
    assert key_state["ok"] is True
    assert key_state["keymap"]["source"] == "factory-defaults"
    # And the next successful write rebuilds a valid document.
    assert controller.run_action("set_brightness", 70)["ok"] is True
    block = store.get_layer(store.load(), SERIAL, 1, 1)
    assert block["settings"]["brightness"] == 70


# --- import validation (zero device reports, staging only) --------------------

VALID_IMPORT = {"profile": 1, "layer": 1, "keymap": {"60": 0x54, "0": 0x04}}


def test_import_valid_stages_without_touching_device(fake_hid, monkeypatch) -> None:
    controller, _ = _controller(monkeypatch)
    fake_hid.capture.clear()
    result = controller.import_profile(dict(VALID_IMPORT))
    assert result["ok"] is True
    assert result["keymap"] == {"60": 0x54, "0": 0x04}
    assert "staged only, nothing written to the device" in result["message"]
    # THE pin: import produced ZERO device reports.
    assert fake_hid.capture == []
    # ...and the store now carries the imported codes.
    block = store.get_layer(store.load(), SERIAL, 1, 1)
    assert block["keymap"] == {"60": 0x54, "0": 0x04}


@pytest.mark.parametrize(
    "payload, needle",
    [
        (None, "must be a JSON object"),
        ({}, "no keymap/settings block"),
        ({"keymap": "not-a-dict"}, "keymap must be an object"),
        ({"keymap": {"60": 0x00}}, "not an assignable key"),       # unassign code
        ({"keymap": {"60": 0xFE}}, "not an assignable key"),       # off-catalog byte
        ({"keymap": {"abc": 0x04}}, "slot keys must be integers"),  # non-int slot
        ({"keymap": {"-2": 0x04}}, "slot keys must be integers"),   # negative slot
        ({"keymap": {"120": 0x04}}, "must be an int 0..119"),             # past the matrix
        ({"keymap": {"0": 256}}, "must be an int 0..255"),
        ({"keymap": {"0": 4}, "settings": {"brightness": 101}}, "brightness must be an int 0..100"),
        ({"keymap": {"0": 4}, "profile": 9}, "profile must be an int 1..3"),
        ({"devices": {"x": {"profiles": {"1": {"layers": {"1": {}}}}}}, "version": 1, "profile": 2, "layer": 2},
         "has no profile 2 layer 2"),
    ],
)
def test_import_rejects_invalid_without_writing(fake_hid, monkeypatch, payload, needle) -> None:
    controller, _ = _controller(monkeypatch)
    fake_hid.capture.clear()
    result = controller.import_profile(payload)
    assert result["ok"] is False, result
    assert needle in result["message"], result
    assert fake_hid.capture == []
    assert store.load() == {"version": 1, "devices": {}}  # nothing staged


def test_import_full_store_document(fake_hid, monkeypatch) -> None:
    controller, _ = _controller(monkeypatch)
    fake_hid.capture.clear()
    document = {
        "version": 1,
        "devices": {
            "some-other-serial": {
                "profiles": {"1": {"layers": {"2": {
                    "settings": {"side_lights": False},
                    "keymap": {"12": 0x29},
                }}}}
            }
        },
    }
    result = controller.import_profile(document)
    assert result["ok"] is True, result["message"]
    assert (result["profile"], result["layer"]) == (1, 2)
    assert fake_hid.capture == []
    block = store.get_layer(store.load(), SERIAL, 1, 2)
    assert block["keymap"] == {"12": 0x29}


# --- HTTP endpoints ------------------------------------------------------------

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


def test_http_export_import_round_trip(fake_hid, monkeypatch) -> None:
    controller, _ = _controller(monkeypatch)
    # Save something first (settings write persists through the store hook).
    assert controller.run_action("set_polling_rate", 250)["ok"] is True
    server, base = _serve(controller)
    try:
        request = urllib.request.Request(base + "/api/export?profile=1&layer=1")
        with urllib.request.urlopen(request) as response:
            assert response.status == 200
            disposition = response.headers.get("Content-Disposition", "")
            assert disposition.startswith("attachment;")
            assert "open-glorious-core-p1-l1.json" in disposition
            document = json.loads(response.read())
        assert document["version"] == 1
        assert document["device_serial"] == SERIAL
        assert document["profile"] == 1 and document["layer"] == 1
        assert document["settings"]["polling_rate"] == 250

        # Exporting an unsaved pair is an honest 404, not a fake document.
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(base + "/api/export?profile=3&layer=3")
        assert exc_info.value.code == 404

        # Import round trip: the exported file is accepted, staged, zero writes.
        fake_hid.capture.clear()
        status, result = _post_json(base, "/api/import", document)
        assert status == 200 and result["ok"] is True
        assert fake_hid.capture == []

        # Invalid import: 400 with the validation message, nothing staged.
        status, result = _post_json(base, "/api/import", {"keymap": {"0": 0x00}})
        assert status == 400 and result["ok"] is False
        assert "not an assignable key" in result["message"]
        assert fake_hid.capture == []
    finally:
        server.shutdown()
        server.server_close()
