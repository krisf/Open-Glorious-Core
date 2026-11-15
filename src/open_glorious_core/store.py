"""G18 commit 3 — local per-device configuration store (parity persistence).

One JSON document under XDG data home:

    $XDG_DATA_HOME/open-glorious-core/store.json
    (default ``~/.local/share/open-glorious-core/store.json``)

Schema (validated on EVERY load; strict — anything unexpected makes the
document "corrupt"):

    {"version": 1,
     "devices": {
       <serial>: {                      # the device physical_id (USB serial)
         "profiles": {
           <profile 1..3>: {
             "layers": {
               <layer 1..3>: {
                 "settings": {brightness, speed, effect, color_name,
                              polling_rate, input_latency_ms, side_lights},
                 "keymap":   {<slot 0..119>: <code 0..255>}   # partial OK
               }}}}}}}

Why this exists (plan P4): the WB5092 GET config mirror is blank by quirk —
the device cannot tell us what it currently holds. The values WE last wrote
are therefore persisted locally and shown as the honest fallback ("Device
mirror is blank — showing your last saved configuration."), and they are
what /api/export serves and /api/import validates into.

Writes are atomic (tmp + ``os.replace`` — same discipline as lastgood.py:
a crash mid-write can never leave a half-written store). Reads NEVER crash:
a missing or corrupt file yields an empty document.

Zero device I/O, zero third-party deps, no imports from sibling modules
(the constants are restated on purpose so the store stays import-free).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

XDG_DATA_HOME_ENV = "XDG_DATA_HOME"
APP_DIR_NAME = "open-glorious-core"
STORE_FILE_NAME = "store.json"
SCHEMA_VERSION = 1

# Restated from backend/keycodes on purpose (import-free module rule).
KEYMAP_SLOTS = 120
PROFILE_RANGE = range(1, 4)
POLLING_RATES = (125, 250, 500, 1000)
INPUT_LATENCY_MS = (2, 8, 16)

SETTING_KEYS = frozenset(
    {
        "brightness",
        "speed",
        "effect",
        "color_name",
        "polling_rate",
        "input_latency_ms",
        "side_lights",
    }
)


class StoreError(ValueError):
    """Raised on a payload that must never be persisted to the store."""


def data_root(root: Path | str | None = None) -> Path:
    """Data root: explicit root > $XDG_DATA_HOME > ~/.local/share."""
    if root is not None:
        return Path(root).expanduser()
    env = os.environ.get(XDG_DATA_HOME_ENV)
    if env:
        return Path(env).expanduser()
    return Path.home() / ".local" / "share"


def store_path(root: Path | str | None = None) -> Path:
    return data_root(root) / APP_DIR_NAME / STORE_FILE_NAME


def empty_document() -> dict[str, Any]:
    return {"version": SCHEMA_VERSION, "devices": {}}


def _plain_int(value: Any, low: int, high: int, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise StoreError(f"{field} must be an int {low}..{high}, got {value!r}")
    return int(value)


def validate_settings(value: Any) -> dict[str, Any]:
    """Validate a settings block; raises StoreError on anything invalid."""
    if not isinstance(value, dict):
        raise StoreError("settings must be an object")
    unknown = set(value) - SETTING_KEYS
    if unknown:
        raise StoreError(f"settings has unknown keys: {sorted(unknown)}")
    out: dict[str, Any] = {}
    if "brightness" in value:
        out["brightness"] = _plain_int(value["brightness"], 0, 100, "brightness")
    if "speed" in value:
        out["speed"] = _plain_int(value["speed"], 0, 100, "speed")
    if "effect" in value:
        out["effect"] = _plain_int(value["effect"], 0, 255, "effect")
    if "color_name" in value:
        name = value["color_name"]
        if not isinstance(name, str) or not 1 <= len(name) <= 40:
            raise StoreError(f"color_name must be a short string, got {name!r}")
        out["color_name"] = name
    if "polling_rate" in value:
        rate = value["polling_rate"]
        if not isinstance(rate, int) or isinstance(rate, bool) or rate not in POLLING_RATES:
            raise StoreError(f"polling_rate must be one of {list(POLLING_RATES)}, got {rate!r}")
        out["polling_rate"] = int(rate)
    if "input_latency_ms" in value:
        latency = value["input_latency_ms"]
        if not isinstance(latency, int) or isinstance(latency, bool) or latency not in INPUT_LATENCY_MS:
            raise StoreError(f"input_latency_ms must be one of {list(INPUT_LATENCY_MS)}, got {latency!r}")
        out["input_latency_ms"] = int(latency)
    if "side_lights" in value:
        lights = value["side_lights"]
        if not isinstance(lights, bool):
            raise StoreError(f"side_lights must be a boolean, got {lights!r}")
        out["side_lights"] = lights
    return out


def validate_keymap(value: Any) -> dict[str, int]:
    """Validate a ``{slot: code}`` map (partial OK — missing slots keep the
    factory value at display time). Slots are ints 0..119, codes bytes."""
    if not isinstance(value, dict):
        raise StoreError("keymap must be an object of slot -> code")
    out: dict[str, int] = {}
    for slot_key, code in value.items():
        if not isinstance(slot_key, str) or not slot_key.isdigit():
            raise StoreError(f"keymap slot keys must be integers 0..{KEYMAP_SLOTS - 1}, got {slot_key!r}")
        slot = _plain_int(int(slot_key), 0, KEYMAP_SLOTS - 1, "keymap slot")
        out[str(slot)] = _plain_int(code, 0, 0xFF, f"keymap code for slot {slot}")
    return out


def keymap_from_codes(codes: bytes | list[int]) -> dict[str, int]:
    """Full-matrix helper for the apply hooks: 120 codes -> {slot: code}."""
    raw = list(codes)
    if len(raw) != KEYMAP_SLOTS:
        raise StoreError(f"codes must carry exactly {KEYMAP_SLOTS} entries")
    return validate_keymap({str(index): code for index, code in enumerate(raw)})


def _validated_layer_block(block: Any, where: str) -> dict[str, Any]:
    if not isinstance(block, dict):
        raise StoreError(f"{where} must be an object")
    unknown = set(block) - {"settings", "keymap"}
    if unknown:
        raise StoreError(f"{where} has unknown keys: {sorted(unknown)}")
    out: dict[str, Any] = {}
    if "settings" in block:
        out["settings"] = validate_settings(block["settings"])
    if "keymap" in block:
        out["keymap"] = validate_keymap(block["keymap"])
    return out


def _validated_document(raw: Any) -> dict[str, Any]:
    """Full-document validation; raises StoreError on any deviation."""
    if not isinstance(raw, dict):
        raise StoreError("store document must be an object")
    if raw.get("version") != SCHEMA_VERSION:
        raise StoreError(f"unsupported store version: {raw.get('version')!r}")
    devices = raw.get("devices")
    if not isinstance(devices, dict):
        raise StoreError("devices must be an object")
    out_devices: dict[str, Any] = {}
    for serial, device in devices.items():
        if not isinstance(serial, str) or not serial or not isinstance(device, dict):
            raise StoreError("device entries need a non-empty serial key and an object value")
        if set(device) - {"profiles"}:
            raise StoreError(f"device {serial!r} has unknown keys: {sorted(set(device) - {'profiles'})}")
        profiles = device.get("profiles")
        if not isinstance(profiles, dict):
            raise StoreError(f"device {serial!r} needs a profiles object")
        out_profiles: dict[str, Any] = {}
        for profile_key, profile in profiles.items():
            if not isinstance(profile_key, str) or not profile_key.isdigit():
                raise StoreError(f"profile keys must be integers 1..3, got {profile_key!r}")
            _plain_int(int(profile_key), PROFILE_RANGE.start, PROFILE_RANGE.stop - 1, "profile")
            if not isinstance(profile, dict):
                raise StoreError("profile entry must be an object")
            if set(profile) - {"layers"}:
                raise StoreError(f"profile {profile_key} has unknown keys: {sorted(set(profile) - {'layers'})}")
            layers = profile.get("layers")
            if not isinstance(layers, dict):
                raise StoreError(f"profile {profile_key} needs a layers object")
            out_layers: dict[str, Any] = {}
            for layer_key, layer in layers.items():
                if not isinstance(layer_key, str) or not layer_key.isdigit():
                    raise StoreError(f"layer keys must be integers 1..3, got {layer_key!r}")
                _plain_int(int(layer_key), PROFILE_RANGE.start, PROFILE_RANGE.stop - 1, "layer")
                out_layers[layer_key] = _validated_layer_block(
                    layer, f"layer {profile_key}/{layer_key}"
                )
            out_profiles[profile_key] = {"layers": out_layers}
        out_devices[serial] = {"profiles": out_profiles}
    return {"version": SCHEMA_VERSION, "devices": out_devices}


def load(root: Path | str | None = None) -> dict[str, Any]:
    """Read + validate the store; NEVER raises — missing/corrupt -> empty."""
    path = store_path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return empty_document()
    try:
        return _validated_document(raw)
    except StoreError:
        return empty_document()


def save(document: dict[str, Any], root: Path | str | None = None) -> Path:
    """Atomically persist a (re-validated) document: tmp + os.replace."""
    validated = _validated_document(document)
    path = store_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(validated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def _layer_block(
    document: dict[str, Any], serial: str, profile: int, layer: int
) -> dict[str, Any]:
    device = document["devices"].setdefault(serial, {"profiles": {}})
    prof = device["profiles"].setdefault(str(profile), {"layers": {}})
    return prof["layers"].setdefault(str(layer), {})


def get_layer(
    document: dict[str, Any], serial: str, profile: int, layer: int
) -> dict[str, Any] | None:
    """The stored block for (serial, profile, layer); None when absent."""
    block = (
        document.get("devices", {})
        .get(serial, {})
        .get("profiles", {})
        .get(str(profile), {})
        .get("layers", {})
        .get(str(layer))
    )
    return block if isinstance(block, dict) and block else None


def record(
    serial: str,
    profile: int,
    layer: int,
    *,
    keymap: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    root: Path | str | None = None,
) -> dict[str, Any]:
    """Merge keymap/settings into the stored block and atomically persist.

    Returns the stored block AFTER the merge. Settings merge key-by-key (a
    brightness write never drops the saved polling rate); the keymap merges
    slot-by-slot. Serial/profile/layer come from the caller and are trusted
    only as structural keys (profile/layer validated to 1..3).
    """
    _plain_int(profile, PROFILE_RANGE.start, PROFILE_RANGE.stop - 1, "profile")
    _plain_int(layer, PROFILE_RANGE.start, PROFILE_RANGE.stop - 1, "layer")
    if not isinstance(serial, str) or not serial:
        raise StoreError("serial must be a non-empty string")
    document = load(root)
    block = _layer_block(document, serial, profile, layer)
    if settings is not None:
        merged = dict(block.get("settings", {}))
        merged.update(validate_settings(settings))
        block["settings"] = merged
    if keymap is not None:
        merged_map = dict(block.get("keymap", {}))
        merged_map.update(validate_keymap(keymap))
        block["keymap"] = merged_map
    save(document, root)
    return dict(block)


def export_layer(
    serial: str, profile: int, layer: int, root: Path | str | None = None
) -> dict[str, Any] | None:
    """The stored layer as a standalone export document; None when nothing
    is saved for this pair."""
    block = get_layer(load(root), serial, profile, layer)
    if block is None:
        return None
    return {
        "version": SCHEMA_VERSION,
        "device_serial": serial,
        "profile": profile,
        "layer": layer,
        "settings": dict(block.get("settings", {})),
        "keymap": dict(block.get("keymap", {})),
    }
