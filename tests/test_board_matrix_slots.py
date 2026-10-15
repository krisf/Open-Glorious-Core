"""G10/G17 board-matrix slot alignment — headless slot-table regressions.

The GUI grid binds every visual keycap to a matrix slot. G10 rebuilt that
mapping as per-row slot LITERALS transcribed from remap/KEYMAP_MAP.md. G11
claimed an EMPIRICAL override (CapsLock at slot 58); G17 RETRACTS it: the G13
targeted cell sweep wrote F13 (0x68) to 58 and F14 (0x69) to 60, and a Caps
Lock press emitted `00 00 69` twice on the OS listener with nothing from 58,
so the vendor Buttoninfo_Default is right — CapsLock is slot 60 (0x39, type
0x01) and slot 58 is a dead matrix spare among the row-4 head cells 55-59.
This module parses the shipped table's JSON block verbatim (no node, no DOM,
no device) and pins the G17 facts: CapsLock cap → slot 60; the head cells
55-59 (58 included) bind NO cap and are hidden; byte facts (FACTORY[60]==0x39
vendor-correct, FACTORY[55..59]==0xFF).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from open_glorious_core import app as app_module
from open_glorious_core.backend import KEYMAP_SLOT_LABELS

ROOT = Path(__file__).resolve().parents[1]
SLOTS_JS = ROOT / "src" / "open_glorious_core" / "static" / "keymap-slots.js"
FACTORY = (ROOT / "src" / "open_glorious_core" / "data" / "default_keymap.bin").read_bytes()

START = "// [KEYMAP-SLOT-TABLE-START]"
END = "// [KEYMAP-SLOT-TABLE-END]"

# Transcribed BY HAND from remap/KEYMAP_MAP.md lines 46-194 (the 120-slot row
# tables) — deliberately independent of keymap-slots.js: a drift between the
# shipped table and the map must fail HERE first.
EXPECTED_ROWS: list[tuple[list[int], list[str | None]]] = [
    # Row 1 (KEYMAP_MAP.md 46-69): Esc F1-F12 PrtSc, slot 14 = RAW 0x02 artifact.
    (list(range(0, 15)),
     ["Esc", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10",
      "F11", "F12", "PrtSc", None]),
    # Row 2 (71-94): ` 1..0 - = Backspace Delete(R1).
    (list(range(20, 35)),
     ["Grave", "1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "Minus",
      "Equal", "Backspace", "Delete"]),
    # Row 3 (96-119): Tab Q..P [ ] \ PgUp(R2); 55-59 are TRAILING 0xFF spares.
    (list(range(40, 55)),
     ["Tab", "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P", "LBracket",
      "RBracket", "Backslash", "PageUp"]),
    # Row 4 (121-144 + G17 SWEEP-VERDICT note): Caps Lock IS slot 60 — the
    # vendor table's own CapsLock cell, confirmed by the G13 sweep; the head
    # cells 55-59 (dead spare 58 included) head NO visual row.
    (list(range(60, 75)),
     ["Caps Lock", "A", "S", "D", "F", "G", "H", "J", "K", "L", "Semicolon",
      "Quote", None, "Enter", "PageDown"]),
    # Row 5 (146-169): LShift Z..M , . / RShift, 92 artifact, Up, End(R4).
    (list(range(80, 95)),
     ["Left Shift", "Z", "X", "C", "V", "B", "N", "M", "Comma", "Period",
      "Slash", "Right Shift", None, "Up", "End"]),
    # Row 6 (171-194): LCtrl LWin LAlt ·· Space ·· RAlt Fn RCtrl · ← ↓ →.
    ([100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114],
     ["Left Ctrl", "Left Win", "Left Alt", None, None, "Space", None, None,
      "Right Alt", "Fn", "Right Ctrl", None, "Left", "Down", "Right"]),
]

# G9 NON_KEYCAP_SLOTS ∪ the hidden row-4 head cells (KEYMAP_MAP.md 115-119 +
# SWEEP-VERDICT note: 55-59, dead spare 58 included; G17 retracts G11's
# caps@58 and un-hides 60 as the real CapsLock cell).
HEAD_OF_BLOCK_SPARES = frozenset({55, 56, 57, 58, 59})
G9_NON_KEYCAP = frozenset({14, 72, 92, 103, 104, 106, 107, 111})
EXPECTED_NON_KEYCAP = G9_NON_KEYCAP | HEAD_OF_BLOCK_SPARES


@pytest.fixture(scope="module")
def table() -> dict:
    text = SLOTS_JS.read_text(encoding="utf-8")
    start = text.index(START)
    end = text.index(END)
    assert start < end, "slot-table markers out of order in keymap-slots.js"
    block = text[start:end]
    payload = block.split("var KEYMAP_SLOT_TABLE = ", 1)[1].rsplit(";", 1)[0]
    return json.loads(payload)


def _row_maps(table: dict) -> list[tuple[list[int], list]]:
    return [(row["slots"], row["keys"]) for row in table["rows"]]


def _slot_names(table: dict) -> dict[int, str | None]:
    names: dict[int, str | None] = {}
    for slots, keys in _row_maps(table):
        names.update(zip(slots, keys))
    return names


def _visual_slots(table: dict) -> list[int]:
    slots: list[int] = []
    for row_slots, _ in _row_maps(table):
        slots.extend(row_slots)
    return slots


def _clickable(table: dict) -> set[int]:
    non = set(table["non_keycap_slots"])
    return {slot for slot in _visual_slots(table) if slot not in non}


# --- structure -------------------------------------------------------------

def test_row_tables_are_the_map_tables(table: dict) -> None:
    assert _row_maps(table) == EXPECTED_ROWS


def test_row_4_visual_block_starts_at_caps_slot_60(table: dict) -> None:
    row4 = table["rows"][3]["slots"]
    assert row4[0] == 60, "the row-4 block must START at the CapsLock slot (G17)"
    visual = set(_visual_slots(table))
    assert not visual & HEAD_OF_BLOCK_SPARES, (
        "head cells 55-59 (dead spare 58 included) belong to NO "
        "visual row (KEYMAP_MAP.md 115-119 + SWEEP-VERDICT note)"
    )


def test_no_two_caps_share_a_slot(table: dict) -> None:
    slots = _visual_slots(table)
    assert len(slots) == 90 and len(set(slots)) == 90


def test_clickable_caps_are_exactly_the_82_physical_keycaps(table: dict) -> None:
    clickable = _clickable(table)
    expected = {
        slot
        for slots, keys in EXPECTED_ROWS
        for slot, key in zip(slots, keys)
        if key is not None
    }
    assert clickable == expected
    assert len(clickable) == 82, "KEYMAP_MAP.md: 82 physical keys"
    # The plan's absence rule: G9 NON_KEYCAP ∪ {55..59} never clickable.
    assert not clickable & EXPECTED_NON_KEYCAP
    # Byte cross-check: every clickable cap has a real factory code (G17: no
    # exceptions — the G11 "CapsLock@58 with factory 0xFF" pair is retracted;
    # caps sit at 60 where the vendor table itself has 0x39).
    for slot in clickable:
        assert FACTORY[slot] != 0xFF, slot
    # Keycap-less VISUAL slots (row-6 spacers) are factory 0xFF; 14/72/92 are
    # the raw-byte artifacts (non-FF bytes, no keycap — KEYMAP_MAP.md 32-42).
    for slot in (103, 104, 106, 107, 111):
        assert slot in _visual_slots(table) and FACTORY[slot] == 0xFF
    for slot in (14, 72, 92):
        assert slot in _visual_slots(table) and FACTORY[slot] != 0xFF


# --- triple-confirmed slots (G10_UI_TARGET_PLAN.md "PROVEN slot facts") ------

def test_caps_lock_cap_resolves_to_slot_60_and_58_is_a_hidden_spare(table: dict) -> None:
    names = _slot_names(table)
    caps = {name: slot for slot, name in names.items() if name is not None}
    assert caps["Caps Lock"] == 60, (
        "G17 (G13 sweep): the CapsLock cell is slot 60 — the vendor "
        "Buttoninfo_Default's own cell, sweep-confirmed on the WB5092 firmware"
    )
    assert 58 not in names, "slot 58 bound to a cap — the retracted G11 claim"
    assert 58 in table["non_keycap_slots"]
    # Byte facts: the vendor dump has CapsLock's code at 60 and 0xFF at 58 —
    # the sweep confirms exactly that pair.
    assert FACTORY[58] == 0xFF and FACTORY[60] == 0x39


@pytest.mark.parametrize(
    ("slot", "name"),
    [
        (13, "PrtSc"),      # user physically verified, G6 demo
        (105, "Space"),
        (109, "Fn"),
        (112, "Left"),
        (113, "Down"),
        (114, "Right"),
        (100, "Left Ctrl"),
        (101, "Left Win"),
        (102, "Left Alt"),
        (108, "Right Alt"),
        (110, "Right Ctrl"),
    ],
)
def test_triple_confirmed_slots(table: dict, slot: int, name: str) -> None:
    assert _slot_names(table)[slot] == name


# --- cross-language consistency ---------------------------------------------

def test_table_names_equal_backend_slot_labels(table: dict) -> None:
    """The JS cap table and backend KEYMAP_SLOT_LABELS are two transcriptions
    of the same KEYMAP_MAP.md tables; a drift means one of them is wrong."""
    names = {slot: key for slot, key in _slot_names(table).items() if key is not None}
    assert names == dict(KEYMAP_SLOT_LABELS)


def test_app_module_non_keycap_slots_extended(table: dict) -> None:
    assert app_module.NON_KEYCAP_SLOTS == EXPECTED_NON_KEYCAP
    assert app_module.NON_KEYCAP_SLOTS >= G9_NON_KEYCAP  # G9 set only grew
    assert set(table["non_keycap_slots"]) == set(app_module.NON_KEYCAP_SLOTS)
    # Byte evidence for the hidden head cells (55-59 all factory 0xFF).
    for slot in HEAD_OF_BLOCK_SPARES:
        assert FACTORY[slot] == 0xFF, slot
    assert FACTORY[60] == 0x39  # visible CapsLock cap, vendor-correct (G17)


def test_non_keycap_marks_match_the_table(table: dict) -> None:
    non = set(table["non_keycap_slots"])
    for slot, key in _slot_names(table).items():
        if slot in G9_NON_KEYCAP:
            assert key is None, f"visual slot {slot} must carry no key name"
    # 55-59 are in the exclusion set and in no visual row at all.
    assert non - set(_visual_slots(table)) == HEAD_OF_BLOCK_SPARES
