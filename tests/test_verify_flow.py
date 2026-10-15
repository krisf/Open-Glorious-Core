"""G12 items 2-4 — verify-armed matcher + 60 s auto-revert (controller + HTTP).

The self-heal contract (G12_AUTOVERIFY_PLAN.md, byte-for-byte banners):

* ``POST /api/verify-remap {slot, expect:[codes…]}`` arms the matcher and a
  server-side timer (default 60 s, injected here); banner
  "press your remapped key once (60 s)".
* ``POST /api/keyevent {code, key}`` feeds one keydown; the matcher maps the
  EXPECTED HID codes to KeyboardEvent.code variants (0x59 → Numpad1 AND End —
  the NumLock class of the capslock incident). A match:
  "VERIFIED — <key> now emits <name>", cancels the timer, marks the last-good
  snapshot verified.
* Timeout with NO input: the last-good matrix is written back (cmd-0x01 bind
  + cmd-0x03 full matrix, the proven G11 recovery path) — ONLY from a
  VERIFIED state. A seed state (no confirmed press yet) is NEVER auto-written;
  the banner says so honestly (item 4). Every outcome lands an app-log line.

Device-free: the FakeBackend from the controller suite (it asserts every call
runs under the controller IO lock) + conftest's tmp config dir.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from open_glorious_core import app as app_module
from open_glorious_core import lastgood
from open_glorious_core.backend import BackendError
from open_glorious_core.app import (
    VERIFY_ARM_BANNER,
    VERIFY_REVERT_BANNER,
    VERIFY_SEED_NO_REVERT_BANNER,
    key_event_variants,
)
from tests.test_controller_keymap import FakeBackend, make_controller

ROOT = Path(__file__).resolve().parents[1]
FACTORY = (ROOT / "src" / "open_glorious_core" / "data" / "default_keymap.bin").read_bytes()

FAST_TIMEOUT = 0.05
SLEEP_PAST_TIMEOUT = 0.5


def applied_vector() -> list[int]:
    codes = bytearray(FACTORY)
    codes[58] = 0x59  # the plan's pin: caps cell remapped to Keypad 1
    return list(codes)


def make_verified_pair() -> list[int]:
    """Seed → record one apply → operator confirms → revertable state."""
    applied = applied_vector()
    lastgood.record_apply(1, 1, applied, FACTORY)
    confirmed = lastgood.confirm(1, 1)
    assert confirmed is not None and confirmed["verified"] is True
    return applied


@pytest.fixture()
def armed(monkeypatch: pytest.MonkeyPatch):
    """Controller with a fast injectable verify timer + teardown cancel."""
    ctrl, backend = make_controller(monkeypatch, FakeBackend())
    ctrl._verify_timeout = FAST_TIMEOUT
    yield ctrl, backend
    with ctrl._verify_lock:
        pending = ctrl._verify_armed
    if pending is not None and pending.timer is not None:
        pending.timer.cancel()


# ---------------------------------------------------------------------------
# Matcher: HID code -> KeyboardEvent.code variants (explicit table)
# ---------------------------------------------------------------------------


def test_variant_table_pins_the_plan_classes() -> None:
    # The capslock-incident class: keypad cells emit their navigation codes
    # with NumLock OFF — BOTH variants must match.
    assert key_event_variants(0x59) == ("Numpad1", "End")
    assert key_event_variants(0x5F) == ("Numpad7", "Home")
    assert key_event_variants(0x62) == ("Numpad0", "Insert")
    assert "F13" in key_event_variants(0x68)
    assert key_event_variants(0x04) == ("KeyA",)
    assert key_event_variants(0x39) == ("CapsLock",)
    assert key_event_variants(0xE0) == ("ControlLeft",)
    # Keys the browser cannot observe: no variants -> never match -> the
    # self-heal path decides (never a false VERIFIED).
    assert key_event_variants(0xFF) == ()
    assert key_event_variants(0xA0) == ()
    assert key_event_variants(0x02) == ()
    assert key_event_variants(0x1122) == ()  # unmapped usage: empty, no crash


# ---------------------------------------------------------------------------
# Arm
# ---------------------------------------------------------------------------


def test_arm_seeds_banners_and_logs(armed) -> None:
    ctrl, _ = armed
    result = ctrl.arm_verify(58, [0x59])
    assert result["ok"] is True
    assert result["message"] == "press your remapped key once (60 s)"
    view = result["verify"]
    assert view["armed"] is True and view["state"] == "armed"
    assert view["slot"] == 58 and view["expect"] == [0x59]
    # Item 4 groundwork: arming seeds the last-good file (factory vector,
    # honestly flagged unverified) for this pair.
    state = lastgood.load(1, 1)
    assert state is not None
    assert state["seed"] is True and state["verified"] is False
    assert state["codes"] == list(FACTORY)
    assert any("verify-armed" in line for line in view["log"])


def test_arm_validation(armed) -> None:
    ctrl, _ = armed
    assert ctrl.arm_verify(999, [0x59])["ok"] is False
    assert ctrl.arm_verify(-1, [0x59])["ok"] is False
    assert ctrl.arm_verify("x", [0x59])["ok"] is False
    assert ctrl.arm_verify(58, [])["ok"] is False
    assert ctrl.arm_verify(58, "Numpad1")["ok"] is False
    assert ctrl.arm_verify(58, [0x100])["ok"] is False
    assert ctrl.verify_view()["armed"] is False


# ---------------------------------------------------------------------------
# Match (the one keystroke)
# ---------------------------------------------------------------------------


def test_match_numlock_off_variant_verifies(armed) -> None:
    ctrl, _ = armed
    lastgood.record_apply(1, 1, applied_vector(), FACTORY)
    ctrl.arm_verify(58, [0x59])
    verdict = ctrl.record_key_event("End", "End")
    assert verdict["ok"] is True and verdict["recorded"] is True
    assert verdict["message"] == "VERIFIED — End now emits Keypad 1 (0x59)"
    assert verdict["verify"]["state"] == "verified"
    # The snapshot is now operator-confirmed: applied codes became the
    # revertable last-good.
    state = lastgood.load(1, 1)
    assert state is not None
    assert state["verified"] is True and state["seed"] is False
    assert state["codes"] == applied_vector()
    assert any("verify-verified" in line for line in verdict["verify"]["log"])


def test_match_numlock_on_variant_verifies(armed) -> None:
    ctrl, _ = armed
    ctrl.arm_verify(58, [0x59])
    verdict = ctrl.record_key_event("Numpad1", "1")
    assert verdict["recorded"] is True
    assert verdict["message"] == "VERIFIED — 1 now emits Keypad 1 (0x59)"


def test_match_f13_and_letter(armed) -> None:
    ctrl, _ = armed
    ctrl.arm_verify(13, [0x68])
    assert ctrl.record_key_event("F13", "F13")["recorded"] is True
    ctrl.arm_verify(13, [0x04])
    assert ctrl.record_key_event("KeyA", "a")["recorded"] is True


def test_unrelated_keydown_never_verifies(armed) -> None:
    ctrl, _ = armed
    ctrl.arm_verify(58, [0x59])
    verdict = ctrl.record_key_event("KeyA", "a")
    assert verdict["recorded"] is False
    assert ctrl.verify_view()["armed"] is True, "still armed — the clock runs"


def test_keyevent_without_arm_is_dropped(armed) -> None:
    ctrl, _ = armed
    before = list(ctrl.app_log)
    verdict = ctrl.record_key_event("KeyA", "a")
    assert verdict["ok"] is True and verdict["recorded"] is False
    # No storage beyond the armed pending (plan item 2): nothing logged.
    assert ctrl.app_log == before
    assert ctrl.record_key_event(None)["ok"] is False
    assert ctrl.record_key_event("")["ok"] is False


def test_match_cancels_the_revert_timer(armed) -> None:
    ctrl, backend = armed
    make_verified_pair()
    ctrl.arm_verify(58, [0x59])
    assert ctrl.record_key_event("End", "End")["recorded"] is True
    time.sleep(SLEEP_PAST_TIMEOUT)
    assert backend.calls_named("set_keymap_codes") == []
    assert backend.calls_named("change_profile") == []


# ---------------------------------------------------------------------------
# Timeout: the self-heal
# ---------------------------------------------------------------------------


def test_timeout_on_seed_state_writes_nothing(armed) -> None:
    """Item 4: the seed is only the unchanged factory table — the timer must
    NOT auto-write; the banner says so honestly."""
    ctrl, backend = armed
    result = ctrl.arm_verify(58, [0x59])
    assert result["ok"] is True
    time.sleep(SLEEP_PAST_TIMEOUT)
    view = ctrl.verify_view()
    assert view["armed"] is False
    assert view["state"] == "no-input-no-revert"
    assert view["banner"] == VERIFY_SEED_NO_REVERT_BANNER
    assert "unchanged factory table" in view["banner"]
    # ZERO device calls: the seed-no-revert rule is absolute.
    assert backend.calls_named("set_keymap_codes") == []
    assert backend.calls_named("change_profile") == []
    assert any("NOT auto-reverted" in line for line in view["log"])


def test_timeout_on_verified_state_reverts_proven_path(armed) -> None:
    ctrl, backend = armed
    codes = make_verified_pair()
    ctrl.arm_verify(58, [0x68])  # a later F13 write, operator stays away
    time.sleep(SLEEP_PAST_TIMEOUT)
    view = ctrl.verify_view()
    assert view["state"] == "reverted"
    assert view["banner"] == "no input seen — auto-reverted to last good config"
    # The proven recovery path, in order: cmd-0x01 bind + ONE cmd-0x03
    # full-matrix write of the last-good codes (G11 recovery recipe).
    writes = [c for c in backend.calls if c[0] in ("change_profile", "set_keymap_codes")]
    assert [(c[0], c[1], c[2]) for c in writes] == [
        ("change_profile", 1, 1),
        ("set_keymap_codes", 1, 1),
    ]
    assert writes[1][3] == bytes(codes)
    assert len(backend.calls_named("set_keymap_codes")) == 1, "exactly one write"
    assert any("auto-reverted" in line for line in view["log"])


def test_revert_survives_bind_backend_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """The WB blank mirror can never verify a bind (G8): the bind is
    best-effort, the healing matrix write must proceed."""

    class BindBlindBackend(FakeBackend):
        def change_profile(self, profile: int, layer: int) -> str:
            self._note("change_profile", profile, layer)
            raise BackendError(
                "Switch profile failed: read-back cannot verify on this board."
            )

    ctrl, backend = make_controller(monkeypatch, BindBlindBackend())
    ctrl._verify_timeout = FAST_TIMEOUT
    codes = make_verified_pair()
    ctrl.arm_verify(58, [0x68])
    time.sleep(SLEEP_PAST_TIMEOUT)
    view = ctrl.verify_view()
    assert view["state"] == "reverted"
    assert view["banner"] == VERIFY_REVERT_BANNER
    assert backend.calls_named("change_profile")
    writes = backend.calls_named("set_keymap_codes")
    assert len(writes) == 1 and writes[0][3] == bytes(codes)
    assert any("bind unverified" in line for line in view["log"])


def test_revert_reports_failure_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, backend = make_controller(monkeypatch, FakeBackend())
    ctrl._verify_timeout = FAST_TIMEOUT
    make_verified_pair()

    def exploding_write(profile: int, layer: int, codes: bytes) -> str:
        backend._note("set_keymap_codes", profile, layer, bytes(codes))
        raise BackendError("open failed: device vanished")

    monkeypatch.setattr(backend, "set_keymap_codes", exploding_write)
    ctrl.arm_verify(58, [0x68])
    time.sleep(SLEEP_PAST_TIMEOUT)
    view = ctrl.verify_view()
    assert view["state"] == "revert-failed"
    assert view["banner"].startswith("no input seen — AUTO-REVERT FAILED:")
    assert "device vanished" in view["banner"]
    assert any("auto-revert FAILED" in line for line in view["log"])


def test_rearm_replaces_the_armed_expectation(armed) -> None:
    ctrl, backend = armed
    ctrl.arm_verify(58, [0x59])
    ctrl.arm_verify(13, [0x68])
    view = ctrl.verify_view()
    assert view["slot"] == 13 and view["expect"] == [0x68]
    time.sleep(SLEEP_PAST_TIMEOUT)
    # The FIRST timer was cancelled at re-arm: exactly one revert, armed slot
    # 13's timeout, and nothing from the old arming.
    assert ctrl.verify_view()["state"] == "no-input-no-revert"
    assert backend.calls_named("set_keymap_codes") == []


# ---------------------------------------------------------------------------
# keystate carries the verdict; HTTP endpoint wiring
# ---------------------------------------------------------------------------


def test_keystate_embeds_verify_state(armed) -> None:
    ctrl, _ = armed
    ctrl.arm_verify(58, [0x59])
    payload = ctrl.get_keystate()
    assert payload["verify"]["armed"] is True
    assert payload["verify"]["banner"] == VERIFY_ARM_BANNER
    verdict = ctrl.record_key_event("End", "End")
    assert verdict["recorded"] is True
    assert ctrl.get_keystate()["verify"]["banner"] == (
        "VERIFIED — End now emits Keypad 1 (0x59)"
    )


def _serve(ctrl):
    def handler(*args, **kwargs):
        return AppHandler(*args, directory=str(app_module.STATIC_DIR), controller=ctrl, **kwargs)

    AppHandler = app_module.AppRequestHandler
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _post_json(base: str, path: str, payload: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            # send_error() answers unknown endpoints with its HTML page.
            return exc.code, {}


def test_http_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    ctrl, _ = make_controller(monkeypatch, FakeBackend())
    ctrl._verify_timeout = FAST_TIMEOUT
    server, base = _serve(ctrl)
    try:
        status, payload = _post_json(base, "/api/verify-remap", {"slot": 58, "expect": [0x59]})
        assert status == 200 and payload["ok"] is True
        assert payload["message"] == VERIFY_ARM_BANNER

        status, payload = _post_json(base, "/api/keyevent", {"code": "KeyA", "key": "a"})
        assert status == 200 and payload["recorded"] is False

        status, payload = _post_json(base, "/api/keyevent", {"code": "End", "key": "End"})
        assert status == 200 and payload["recorded"] is True
        assert payload["message"] == "VERIFIED — End now emits Keypad 1 (0x59)"

        status, payload = _post_json(base, "/api/keyevent", {})
        assert status == 400 and payload["ok"] is False

        status, payload = _post_json(base, "/api/verify-remap", {"slot": 58})
        assert status == 400 and payload["ok"] is False

        status, _ = _post_json(base, "/api/nope", {})
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()
