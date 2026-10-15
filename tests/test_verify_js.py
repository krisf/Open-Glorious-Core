"""G12 — static analysis pins for the verify front end (app.js / index.html).

The GUI side of the self-heal contract (the full behavioural replay lives in
tests/test_e2e_gui_remap_flow.py):

* a keydown logger forwards EVERY keydown as {code, key} to /api/keyevent;
* after a SUCCESSFUL write the first dirty slot is armed at
  /api/verify-remap — with the target snapshotted BEFORE fetchKeystate()
  resets the baseline;
* the verdict banner (keymap-verify-banner) is painted from the verify state
  embedded in every keystate/endpoint payload.

Structural pins + the node syntax gate (``node --check``) on the touched JS.
No browser, no Qt, no device.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "open_glorious_core" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
INDEX_HTML = (STATIC / "index.html").read_text(encoding="utf-8")
STYLES_CSS = (STATIC / "styles.css").read_text(encoding="utf-8")
PINNED_NODE = "/home/ubuntu/.hermes/tools/node-26.7.0-linux-x64/bin/node"


def _node() -> str:
    for candidate in (os.environ.get("OGC_NODE"), shutil.which("node"), PINNED_NODE):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    pytest.skip("no node binary available (set $OGC_NODE)")


def test_touched_js_passes_node_syntax_gate() -> None:
    for js in (STATIC / "app.js", STATIC / "keymap-slots.js"):
        result = subprocess.run(
            [_node(), "--check", str(js)], capture_output=True, text=True, timeout=60
        )
        assert result.returncode == 0, f"{js.name}: {result.stderr}"


def test_keydown_logger_posts_to_the_keyevent_endpoint() -> None:
    assert 'document.addEventListener("keydown"' in APP_JS
    assert '"/api/keyevent"' in APP_JS
    # Item 2: POSTs {code, key} only — nothing is stored client-side, and a
    # transport failure must stay invisible to the UI.
    assert "JSON.stringify({ code, key })" in APP_JS
    logger = APP_JS.split('document.addEventListener("keydown"', 1)[1]
    assert "postKeyEvent(" in logger
    assert "catch" in APP_JS.split("async function postKeyEvent", 1)[1][:1200]


def test_verify_arms_after_a_successful_write_with_pre_reset_expectation() -> None:
    write_region = APP_JS.split("async function writeKeymapBuffer", 1)[1].split(
        "\n}\n", 1
    )[0]
    # The dirty slot + expected code are snapshotted BEFORE postAction: the
    # post-write fetchKeystate() overwrites the baseline and buffer.
    assert "firstDirtyKeymapSlot()" in write_region
    assert write_region.index("firstDirtyKeymapSlot()") < write_region.index("postAction(")
    assert '"/api/verify-remap"' in APP_JS
    assert "postVerifyRemap(" in write_region
    # Arming only happens on the OK path, after the state refresh.
    assert write_region.index("fetchKeystate()") < write_region.index("postVerifyRemap(")


def test_banner_element_exists_and_is_painted_from_verify_state() -> None:
    assert 'id="keymap-verify-banner"' in INDEX_HTML
    assert 'class="keymap-verify-banner is-hidden"' in INDEX_HTML
    assert 'document.getElementById("keymap-verify-banner")' in APP_JS
    applier = APP_JS.split("function applyVerifyState", 1)[1].split("\n}\n", 1)[0]
    assert "toggleHidden(km.verifyBanner" in applier
    # Every keystate repaint syncs the banner (the timer verdict arrives via
    # the scheduled refetch, not a push channel).
    apply_region = APP_JS.split("function applyKeystate", 1)[1].split("\n}\n", 1)[0]
    assert "applyVerifyState(payload.verify)" in apply_region
    assert ".keymap-verify-banner" in STYLES_CSS


def test_bind_first_write_banner_is_painted_after_every_successful_write() -> None:
    # G16 item 3: after a SUCCESSFUL apply-keymap / reset-keymap the drawer
    # banner (keymap-banner) must show the server's sentence — which
    # profile/layer was written + the operator's profile-cycle hint. The UI
    # only PAINTS payload.keymap_write_banner, and only AFTER fetchKeystate()
    # (which resets the banner to its default text).
    painter = APP_JS.split("function paintKeymapWriteBanner", 1)[1].split("\n}\n", 1)[0]
    assert "payload.keymap_write_banner" in painter
    assert "setText(km.banner" in painter
    assert "toggleHidden(km.banner, false)" in painter
    for name in ("writeKeymapBuffer", "resetKeymapActiveProfile"):
        region = APP_JS.split(f"async function {name}", 1)[1].split("\n}\n", 1)[0]
        assert "paintKeymapWriteBanner(payload)" in region, name
        assert region.index("fetchKeystate()") < region.index(
            "paintKeymapWriteBanner("
        ), f"{name}: the paint must survive the keystate repaint"


def test_server_is_the_only_judge() -> None:
    # The front end never computes a verdict: no client-side matcher table,
    # no verdict template, no client-side 60 s timer (that timer is
    # server-side; the UI only schedules a banner refetch).
    assert "now emits" not in APP_JS
    assert "auto-reverted" not in APP_JS
    assert "press your remapped key once" not in APP_JS
    # G16: the bind-write banner sentence (profile-cycle hint) is server
    # text (app.KEYMAP_BIND_WRITE_BANNER) — the client only paints it.
    assert "FN+CTRL" not in APP_JS
    json.dumps({"slot": 58, "expect": [0x59]})  # payload shape sanity
