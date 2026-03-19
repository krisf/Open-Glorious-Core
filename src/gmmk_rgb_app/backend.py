from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import cast
from pathlib import Path

from .device import GMMK_PRO_PRODUCT_ID, GLORIOUS_VENDOR_ID


class BackendError(RuntimeError):
    """Raised when the keyboard backend cannot complete a request."""


@dataclass(slots=True)
class KeyboardState:
    brightness: int | None = None
    effect: int | None = None
    speed: int | None = None
    hue: int | None = None
    saturation: int | None = None
    red: int | None = None
    green: int | None = None
    blue: int | None = None


class BaseBackend:
    COLOR_NAMES = ("red", "yellow", "green", "cyan", "blue", "purple", "white")
    EFFECT_COLOR_GROUPS: dict[int, list[str]] = {}
    name = "backend"

    def is_available(self) -> bool:
        return True

    def install_hint(self) -> str:
        return ""

    def read_state(self) -> KeyboardState:
        raise NotImplementedError

    def set_brightness(self, value: int, save: bool) -> str:
        raise NotImplementedError

    def set_effect(self, value: int, save: bool) -> str:
        raise NotImplementedError

    def set_speed(self, value: int, save: bool) -> str:
        raise NotImplementedError

    def set_hue(self, value: int, save: bool) -> str:
        raise NotImplementedError

    def set_saturation(self, value: int, save: bool) -> str:
        raise NotImplementedError

    def set_color_name(self, name: str, save: bool) -> str:
        raise NotImplementedError

    def set_rgb_color(self, red: int, green: int, blue: int, save: bool, group: str | None = None) -> str:
        raise NotImplementedError

    def device_indication(self) -> str:
        raise NotImplementedError

    def save(self) -> str:
        raise NotImplementedError


class QmkHidBackend(BaseBackend):
    name = "qmk_hid"

    def __init__(self) -> None:
        self.binary = shutil.which("qmk_hid")

    def is_available(self) -> bool:
        return self.binary is not None

    def install_hint(self) -> str:
        return (
            "Install qmk_hid first. On Arch, the easiest route is usually to build "
            "FrameworkComputer/qmk_hid from source or use a package helper if you "
            "prefer the AUR."
        )

    def read_state(self) -> KeyboardState:
        return KeyboardState(
            brightness=self._read_value("--rgb-brightness"),
            effect=self._read_value("--rgb-effect"),
            speed=self._read_value("--rgb-effect-speed"),
            hue=self._read_value("--rgb-hue"),
            saturation=self._read_value("--rgb-saturation"),
        )

    def set_brightness(self, value: int, save: bool) -> str:
        return self._run_via("--rgb-brightness", str(value), *self._save_args(save))

    def set_effect(self, value: int, save: bool) -> str:
        return self._run_via("--rgb-effect", str(value), *self._save_args(save))

    def set_speed(self, value: int, save: bool) -> str:
        return self._run_via("--rgb-effect-speed", str(value), *self._save_args(save))

    def set_hue(self, value: int, save: bool) -> str:
        return self._run_via("--rgb-hue", str(value), *self._save_args(save))

    def set_saturation(self, value: int, save: bool) -> str:
        return self._run_via("--rgb-saturation", str(value), *self._save_args(save))

    def set_color_name(self, name: str, save: bool) -> str:
        if name not in self.COLOR_NAMES:
            raise BackendError(f"Unsupported color preset: {name}")
        return self._run_via("--rgb-color", name, *self._save_args(save))

    def set_rgb_color(self, red: int, green: int, blue: int, save: bool, group: str | None = None) -> str:
        raise BackendError("Custom RGB values are not mapped for the qmk_hid fallback yet.")

    def device_indication(self) -> str:
        return self._run_via("--device-indication")

    def save(self) -> str:
        return self._run_via("--save")

    def _read_value(self, option: str) -> int | None:
        output = self._run_via(option)
        numbers = [int(match) for match in re.findall(r"\b\d+\b", output)]
        return numbers[-1] if numbers else None

    def _save_args(self, save: bool) -> tuple[str, ...]:
        return ("--save",) if save else ()

    def _run_via(self, *args: str) -> str:
        if not self.binary:
            raise BackendError(self.install_hint())

        cmd = [
            self.binary,
            "--vid",
            GLORIOUS_VENDOR_ID,
            "--pid",
            GMMK_PRO_PRODUCT_ID,
            "via",
            *args,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip() or "Unknown qmk_hid error"
            raise BackendError(details)
        return result.stdout.strip() or "Done."


class StockGmmkProBackend(BaseBackend):
    name = "stock-hidraw-gmmk"
    REPORT_ID = 7
    REPORT_LENGTH = 256
    SOLID_EFFECT = 7
    EFFECT_COLOR_GROUPS = {
        6: ["Primary"],
        7: ["Primary"],
    }
    MULTICOLOR_EFFECTS = {1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18}
    EFFECT_LABELS = {
        0: "LED Off",
        1: "Glorious Mode",
        2: "Wave 1",
        3: "Wave 2",
        4: "Spiraling Wave",
        5: "Acid Mode",
        6: "Breathing",
        7: "Normally On",
        8: "Ripple Graff",
        9: "Pass Without Trace",
        10: "Fast Run Without Trace",
        11: "Matrix 2",
        12: "Matrix 3",
        13: "Rainbow",
        14: "Heartbeat Sensor",
        15: "Digital Times",
        16: "Kamehemeha",
        17: "Pingpong",
        18: "Surmount",
    }
    COLOR_MAP = {
        "red": (255, 0, 0),
        "yellow": (255, 255, 0),
        "green": (0, 255, 0),
        "cyan": (0, 255, 255),
        "blue": (0, 0, 255),
        "purple": (128, 0, 255),
        "white": (255, 255, 255),
    }

    def __init__(self) -> None:
        self.hidraw = self._detect_stock_interface()
        self.state = KeyboardState(brightness=100, effect=7, speed=50, red=255, green=255, blue=255)
        self.color_name = "white"
        self.color_rgb = (255, 255, 255)
        self.profile = 1
        self.layer = 1
        if self.hidraw is not None:
            self._sync_active_profile_layer()

    def is_available(self) -> bool:
        return self.hidraw is not None

    def install_hint(self) -> str:
        return (
            "Stock firmware support needs access to the GMMK Pro hidraw feature-report interface. "
            "Make sure the udev rule is loaded and you can read/write the matching /dev/hidraw node."
        )

    def read_state(self) -> KeyboardState:
        if not self.hidraw:
            raise BackendError(self.install_hint())
        self._sync_active_profile_layer()
        return KeyboardState(
            brightness=self.state.brightness,
            effect=self.state.effect,
            speed=self.state.speed,
            hue=None,
            saturation=None,
            red=self.color_rgb[0],
            green=self.color_rgb[1],
            blue=self.color_rgb[2],
        )

    def set_brightness(self, value: int, save: bool) -> str:
        self.state.brightness = max(0, min(100, int(value)))
        self._apply_current_state()
        return f"Applied stock RGB brightness {self.state.brightness} on {self.hidraw.name}."

    def set_effect(self, value: int, save: bool) -> str:
        effect = int(value)
        if effect not in self.EFFECT_LABELS:
            raise BackendError("Stock firmware currently supports effect IDs 0-18 for the GMMK Pro.")
        self.state.effect = effect
        self._apply_current_state()
        return f"Applied stock RGB effect {effect} ({self.EFFECT_LABELS[effect]}) on {self.hidraw.name}."

    def set_speed(self, value: int, save: bool) -> str:
        self.state.speed = max(0, min(100, int(value)))
        self._apply_current_state()
        return f"Applied stock RGB speed {self.state.speed} on {self.hidraw.name}."

    def set_hue(self, value: int, save: bool) -> str:
        raise BackendError("Hue control is not mapped yet for stock firmware. Use the color preset buttons for now.")

    def set_saturation(self, value: int, save: bool) -> str:
        raise BackendError("Saturation control is not mapped yet for stock firmware. Use the color preset buttons for now.")

    def set_color_name(self, name: str, save: bool) -> str:
        if name not in self.COLOR_MAP:
            raise BackendError(f"Unsupported color preset: {name}")
        self.color_name = name
        self.color_rgb = self.COLOR_MAP[name]
        self.state.red, self.state.green, self.state.blue = self.color_rgb
        if not self._effect_supports_color():
            self.state.effect = self.SOLID_EFFECT
        self._apply_current_state()
        return f"Applied stock RGB color {name} on {self.hidraw.name}."

    def set_rgb_color(self, red: int, green: int, blue: int, save: bool, group: str | None = None) -> str:
        self.color_name = "custom"
        self.color_rgb = (
            max(0, min(255, int(red))),
            max(0, min(255, int(green))),
            max(0, min(255, int(blue))),
        )
        self.state.red, self.state.green, self.state.blue = self.color_rgb
        if not self._effect_supports_color():
            self.state.effect = self.SOLID_EFFECT
        self._apply_current_state()
        return f"Applied custom RGB color #{self.color_rgb[0]:02X}{self.color_rgb[1]:02X}{self.color_rgb[2]:02X} on {self.hidraw.name}."

    def device_indication(self) -> str:
        raise BackendError("Device indication is not mapped yet for stock firmware.")

    def save(self) -> str:
        self._apply_current_state()
        return "Re-sent the current stock RGB state to the keyboard."

    def _effect_supports_color(self, effect: int | None = None) -> bool:
        active_effect = self.state.effect if effect is None else effect
        return active_effect in self.EFFECT_COLOR_GROUPS

    def _apply_current_state(self) -> None:
        if not self.hidraw:
            raise BackendError(self.install_hint())
        self._sync_active_profile_layer()
        properties = bytearray(self.REPORT_LENGTH)
        effect = bytearray(self.REPORT_LENGTH)
        profile_update = bytearray(self.REPORT_LENGTH)
        self._prepare_properties_buffer(properties)
        self._prepare_effect_buffer(effect)
        self._prepare_profile_update_buffer(profile_update)
        self._send_feature_report(properties)
        self._send_feature_report(effect)
        self._send_feature_report(profile_update)

    def _prepare_properties_buffer(self, report: bytearray) -> None:
        report[0] = self.REPORT_ID
        report[1] = 7
        report[2] = self.profile
        report[3] = self.layer
        report[4] = self.state.effect
        report[5] = 0
        report[6] = 1
        report[7] = 0
        report[8] = round(self.state.brightness * 20 / 100)
        report[10] = 16

    def _prepare_effect_buffer(self, report: bytearray) -> None:
        red, green, blue = self.color_rgb
        is_multicolor = self.state.effect in self.MULTICOLOR_EFFECTS
        report[0] = self.REPORT_ID
        report[1] = 2
        report[2] = self.state.effect
        report[8] = max(0, min(100, 100 - self.state.speed))
        report[9] = round(self.state.brightness * 20 / 100)
        report[10] = 1 if is_multicolor else 0
        report[12] = 1 if is_multicolor else 0
        report[15] = red
        report[16] = green
        report[17] = blue

    def _prepare_profile_update_buffer(self, report: bytearray) -> None:
        report[0] = self.REPORT_ID
        report[1] = 1
        report[2] = self.profile
        report[3] = self.layer

    def _sync_active_profile_layer(self) -> None:
        response = self._exchange_feature_report(self._make_request(129))
        parsed = self._parse_profile_layer_response(response)
        if parsed is not None:
            self.profile, self.layer = parsed

    def _make_request(self, request_id: int) -> bytearray:
        report = bytearray(self.REPORT_LENGTH)
        report[0] = self.REPORT_ID
        report[1] = request_id
        report[2] = self.profile
        report[3] = self.layer
        return report

    def _parse_profile_layer_response(self, response: bytearray) -> tuple[int, int] | None:
        candidates = [
            (response[1], response[2]),
            (response[2], response[3]),
            (response[8], response[9]),
        ]
        for profile, layer in candidates:
            if 1 <= profile <= 3 and 1 <= layer <= 3:
                return profile, layer
        return None

    def _exchange_feature_report(self, report: bytearray) -> bytearray:
        self._send_feature_report(report)
        read_buf = bytearray(self.REPORT_LENGTH)
        read_buf[0] = self.REPORT_ID
        try:
            fd = os.open(self.hidraw, os.O_RDWR)
        except PermissionError as exc:
            raise BackendError(
                f"Permission denied opening {self.hidraw}. Reload the udev rule and replug the keyboard."
            ) from exc
        try:
            fcntl.ioctl(fd, _hid_iocgfeature(len(read_buf)), read_buf, True)
        except OSError as exc:
            raise BackendError(f"Feature report read failed on {self.hidraw.name}: {exc}") from exc
        finally:
            os.close(fd)
        return read_buf

    def _send_feature_report(self, report: bytearray) -> None:
        try:
            fd = os.open(self.hidraw, os.O_RDWR)
        except PermissionError as exc:
            raise BackendError(
                f"Permission denied opening {self.hidraw}. Reload the udev rule and replug the keyboard."
            ) from exc
        try:
            fcntl.ioctl(fd, _hid_iocsfeature(len(report)), report, True)
        except OSError as exc:
            raise BackendError(f"Feature report failed on {self.hidraw.name}: {exc}") from exc
        finally:
            os.close(fd)

    def _detect_stock_interface(self) -> Path | None:
        for hidraw_sys in sorted(Path('/sys/class/hidraw').glob('hidraw*')):
            uevent = hidraw_sys / 'device' / 'uevent'
            descriptor = hidraw_sys / 'device' / 'report_descriptor'
            if not uevent.exists() or not descriptor.exists():
                continue
            metadata = uevent.read_text(encoding='utf-8', errors='ignore')
            if '0000320F' not in metadata or '00005044' not in metadata:
                continue
            data = descriptor.read_bytes()
            if data.startswith(bytes.fromhex('06 01 ff 09 01 a1 01 85 07')):
                return Path('/dev') / hidraw_sys.name
        return None


def _ioc(direction: int, type_: int, nr: int, size: int) -> int:
    return (direction << 30) | (type_ << 8) | (nr << 0) | (size << 16)


def _hid_iocsfeature(length: int) -> int:
    return _ioc(3, ord('H'), 0x06, length)


def _hid_iocgfeature(length: int) -> int:
    return _ioc(3, ord('H'), 0x07, length)


def create_backend() -> BaseBackend:
    stock = StockGmmkProBackend()
    if stock.is_available():
        return stock
    return QmkHidBackend()
