from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


GLORIOUS_VENDOR_ID = "320f"
GMMK_PRO_PRODUCT_ID = "5044"


@dataclass(slots=True)
class HidDevice:
    hidraw: str
    name: str
    vendor_id: str
    product_id: str
    physical_id: str
    interface_id: str


def _parse_interface_identifiers(uevent_path: Path) -> tuple[str, str]:
    parts = uevent_path.resolve().parts
    physical_id = "unknown-device"
    interface_id = "unknown-interface"

    for part in parts:
        if re.fullmatch(r"\d+-\d+(?:\.\d+)*", part):
            physical_id = part
        elif re.fullmatch(r"\d+-\d+(?:\.\d+)*:\d+\.\d+", part):
            physical_id, interface_id = part.split(":", 1)

    return physical_id, interface_id


def detect_gmmk_pro() -> list[HidDevice]:
    devices: list[HidDevice] = []
    for uevent_path in sorted(Path("/sys/class/hidraw").glob("hidraw*/device/uevent")):
        data = {}
        for line in uevent_path.read_text(encoding="utf-8").splitlines():
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key] = value

        hid_id = data.get("HID_ID", "")
        hid_name = data.get("HID_NAME", "")
        if not hid_id or "GMMK PRO" not in hid_name.upper():
            continue

        parts = hid_id.split(":")
        if len(parts) != 3:
            continue

        _, vendor_id, product_id = parts
        vendor_id = vendor_id[-4:].lower()
        product_id = product_id[-4:].lower()
        if vendor_id != GLORIOUS_VENDOR_ID or product_id != GMMK_PRO_PRODUCT_ID:
            continue

        physical_id, interface_id = _parse_interface_identifiers(uevent_path)
        devices.append(
            HidDevice(
                hidraw=uevent_path.parts[-3],
                name=hid_name,
                vendor_id=vendor_id,
                product_id=product_id,
                physical_id=physical_id,
                interface_id=interface_id,
            )
        )
    return devices
