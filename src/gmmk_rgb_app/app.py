from __future__ import annotations

import json
import sys
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication

from .backend import BackendError, create_backend
from .device import HidDevice, detect_gmmk_pro

STATIC_DIR = Path(__file__).with_name("static")
ICON_PATH = STATIC_DIR / "assets" / "glorious-core.ico"
HOST = "127.0.0.1"


def _group_devices(devices: list[HidDevice]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for device in devices:
        key = (device.name, device.vendor_id, device.product_id, device.physical_id)
        if key not in grouped:
            grouped[key] = {
                "name": device.name,
                "vendor_id": device.vendor_id,
                "product_id": device.product_id,
                "physical_id": device.physical_id,
                "hidraws": [],
                "interfaces": [],
            }
        grouped[key]["hidraws"].append(device.hidraw)
        grouped[key]["interfaces"].append(device.interface_id)

    for entry in grouped.values():
        entry["hidraws"] = sorted(entry["hidraws"])
        entry["interfaces"] = sorted(entry["interfaces"])

    return list(grouped.values())


class GmmkWebController:
    def __init__(self) -> None:
        self.backend = create_backend()

    def get_state(self) -> dict[str, Any]:
        devices = detect_gmmk_pro()
        grouped_devices = _group_devices(devices)
        if not devices:
            return {
                "ok": False,
                "message": "No GMMK Pro detected in /sys/class/hidraw.",
                "backend": self.backend.name,
                "devices": [],
                "state": None,
            }

        if not self.backend.is_available():
            return {
                "ok": False,
                "message": self.backend.install_hint(),
                "backend": self.backend.name,
                "devices": grouped_devices,
                "state": None,
            }

        try:
            state = self.backend.read_state()
        except BackendError as exc:
            return {
                "ok": False,
                "message": str(exc),
                "backend": self.backend.name,
                "devices": grouped_devices,
                "state": None,
            }

        selected = grouped_devices[0]
        hidraws = ", ".join(selected["hidraws"])
        return {
            "ok": True,
            "message": f"Connected to {selected['name']} across {hidraws} using {self.backend.name}.",
            "backend": self.backend.name,
            "devices": grouped_devices,
            "state": {
                "brightness": state.brightness,
                "effect": state.effect,
                "speed": state.speed,
                "hue": state.hue,
                "saturation": state.saturation,
                "red": state.red,
                "green": state.green,
                "blue": state.blue,
            },
            "effects": getattr(self.backend, "EFFECT_LABELS", {}),
            "effect_color_groups": getattr(self.backend, "EFFECT_COLOR_GROUPS", {}),
            "colors": list(self.backend.COLOR_NAMES),
        }

    def run_action(self, action: str, value: Any = None) -> dict[str, Any]:
        try:
            if action == "refresh":
                return self.get_state()
            if action == "set_brightness":
                message = self.backend.set_brightness(int(value), True)
            elif action == "set_effect":
                message = self.backend.set_effect(int(value), True)
            elif action == "set_speed":
                message = self.backend.set_speed(int(value), True)
            elif action == "set_hue":
                message = self.backend.set_hue(int(value), True)
            elif action == "set_saturation":
                message = self.backend.set_saturation(int(value), True)
            elif action == "set_color":
                message = self.backend.set_color_name(str(value), True)
            elif action == "set_custom_color":
                if not isinstance(value, dict):
                    return {"ok": False, "message": "Custom color payload must be an object."}
                message = self.backend.set_rgb_color(
                    int(value.get("red", 0)),
                    int(value.get("green", 0)),
                    int(value.get("blue", 0)),
                    True,
                    str(value.get("group")) if value.get("group") is not None else None,
                )
            elif action == "save":
                message = self.backend.save()
            elif action == "device_indication":
                message = self.backend.device_indication()
            else:
                return {"ok": False, "message": f"Unknown action: {action}"}
        except BackendError as exc:
            return {"ok": False, "message": str(exc)}

        state = self.get_state()
        state["message"] = message
        return state


class AppRequestHandler(SimpleHTTPRequestHandler):
    controller: GmmkWebController

    def __init__(self, *args, directory: str | None = None, controller: GmmkWebController | None = None, **kwargs) -> None:
        self.controller = controller or GmmkWebController()
        super().__init__(*args, directory=directory, **kwargs)

    def do_GET(self) -> None:
        if self.path == "/api/state":
            self._send_json(self.controller.get_state())
            return
        if self.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        if self.path != "/api/action":
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown endpoint")
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length)
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except json.JSONDecodeError:
            self._send_json({"ok": False, "message": "Invalid JSON payload."}, status=HTTPStatus.BAD_REQUEST)
            return

        result = self.controller.run_action(payload.get("action", ""), payload.get("value"))
        self._send_json(result, status=HTTPStatus.OK if result.get("ok", False) else HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args) -> None:
        return

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    controller = GmmkWebController()

    def handler(*args, **kwargs):
        return AppRequestHandler(*args, directory=str(STATIC_DIR), controller=controller, **kwargs)

    server = ThreadingHTTPServer((HOST, 0), handler)
    host, port = server.server_address
    url = f"http://{host}:{port}"
    server_thread = threading.Thread(target=server.serve_forever, name="gmmk-desktop-ui-server", daemon=True)

    print(f"GMMK Pro desktop app serving UI at {url}")
    print(f"Edit UI files in: {STATIC_DIR}")
    server_thread.start()

    qt_app = QApplication.instance() or QApplication(sys.argv)
    if ICON_PATH.exists():
        qt_app.setWindowIcon(QIcon(str(ICON_PATH)))

    view = QWebEngineView()
    view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
    if ICON_PATH.exists():
        view.setWindowIcon(QIcon(str(ICON_PATH)))
    view.setWindowTitle("Open Glorious Core")
    view.resize(1240, 900)
    view.setMinimumSize(960, 720)
    view.load(QUrl(url))
    view.show()

    try:
        qt_app.exec()
    except KeyboardInterrupt:
        print()
        print("Shutting down GMMK Pro desktop UI...")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
