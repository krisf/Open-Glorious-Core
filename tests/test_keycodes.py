"""G7 Commit 2 — keycodes framework tests.

Every name in keycodes.py traces to the USB HID Usage Tables (page 0x07) or to
a factory code documented in remap/KEYMAP_MAP.md; the shipped factory table
(``src/open_glorious_core/data/default_keymap.bin`` — the fixture ground truth
per that doc) is the assertion anchor: no byte it contains may ever render as
"Reserved" in the GUI.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from open_glorious_core import keycodes
from open_glorious_core.backend import (
    KEYMAP_SLOT_LABELS,
    KEYMAP_SLOTS,
    KEYMAP_UNASSIGNED,
    StockGmmkProBackend,
)
from open_glorious_core.keycodes import (
    HID_CODE_NAMES,
    HID_MODIFIER_CODES,
    TARGET_TYPE_BY_CODE,
    VENDOR_CODE_NAMES,
    all_assignable_codes,
    code_aliases,
    code_group,
    code_to_name,
    target_type,
)

ROOT = Path(__file__).resolve().parents[1]
FACTORY_TABLE = (
    ROOT / "src" / "open_glorious_core" / "data" / "default_keymap.bin"
).read_bytes()
_ALIAS_SHAPE = re.compile(r"[a-z0-9]+")


# ---------------------------------------------------------------------------
# The factory table must always name cleanly (fixture is ground truth)
# ---------------------------------------------------------------------------


def test_every_factory_byte_has_a_non_reserved_name() -> None:
    for code in sorted(set(FACTORY_TABLE)):
        name = code_to_name(code)
        assert not name.startswith("Reserved"), f"factory byte 0x{code:02X} renders Reserved"


def test_factory_byte_count_matches_backend_constant() -> None:
    assert len(FACTORY_TABLE) == KEYMAP_SLOTS


def test_vendor_specials_are_named() -> None:
    # 0xA0 = Fn (row 6), 0x02 = knob/mute raw (row 1 slot 14), documented in
    # remap/KEYMAP_MAP.md; 0x64/0x32 are standard ISO usages present in the
    # ANSI dump at the keycap-less slots 92/72.
    assert "Fn" in code_to_name(0xA0)
    assert "Knob" in code_to_name(0x02)
    assert not code_to_name(0x64).startswith("Reserved")
    assert not code_to_name(0x32).startswith("Reserved")
    assert set(VENDOR_CODE_NAMES) == {0x02, 0xA0}


# ---------------------------------------------------------------------------
# Modifier dict
# ---------------------------------------------------------------------------


def test_modifier_dict_is_exactly_e0_to_e7() -> None:
    assert sorted(HID_MODIFIER_CODES) == list(range(0xE0, 0xE8))


def test_modifier_names_cover_left_right_ctrl_shift_alt_gui() -> None:
    names = sorted(HID_MODIFIER_CODES.values())
    for needle in ("Ctrl", "Shift", "Alt", "GUI"):
        assert sum(needle in name for name in names) == 2, needle
    assert HID_MODIFIER_CODES[0xE0] == "Left Ctrl"
    assert HID_MODIFIER_CODES[0xE7] == "Right GUI"


# ---------------------------------------------------------------------------
# code_to_name
# ---------------------------------------------------------------------------


def test_code_to_name_format() -> None:
    assert code_to_name(0x04) == "A (0x04)"
    assert code_to_name(0x2C) == "Space (0x2C)"
    assert code_to_name(0xE0) == "Left Ctrl (0xE0)"
    assert code_to_name(0xA0) == "Fn (vendor) (0xA0)"


def test_code_to_name_unassigned() -> None:
    assert code_to_name(KEYMAP_UNASSIGNED) == "Unassigned (0xFF)"
    assert code_to_name(0xFF, fall_behaviour="error") == "Unassigned (0xFF)"


def test_code_to_name_reserved_byte_mode() -> None:
    # 0xA1-0xDF are reserved in the USB HID keyboard page: honest byte label.
    assert code_to_name(0xA1, fall_behaviour="byte") == "Reserved 0xA1"
    assert code_to_name(0x00) == "Reserved 0x00"  # "byte" is the default mode


def test_code_to_name_unmapped_raises_in_error_mode() -> None:
    with pytest.raises(KeyError):
        code_to_name(0xA1, fall_behaviour="error")


def test_code_to_name_structurally_invalid_raises_in_both_modes() -> None:
    for code in (0x100, -1, 1000):
        with pytest.raises(KeyError):
            code_to_name(code, fall_behaviour="byte")
        with pytest.raises(KeyError):
            code_to_name(code, fall_behaviour="error")
    with pytest.raises(KeyError):
        code_to_name(True)  # bool is not a keymap byte
    with pytest.raises(KeyError):
        code_to_name("A")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Map hygiene: documented ranges only, no single-letter guessing above 0xDF
# ---------------------------------------------------------------------------


def test_hid_map_stays_inside_documented_range() -> None:
    for code in HID_CODE_NAMES:
        assert 0x04 <= code <= 0xDF
    # Nothing lives in the reserved 0x9A–0xDF band.
    assert not [code for code in HID_CODE_NAMES if 0x9A <= code <= 0xDF]
    # Every documented name is unique-case-insensitive in the UI dropdown.
    names = [name.lower() for name in HID_CODE_NAMES.values()]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------------------
# TARGET_TYPE markers (UI media filter + write-path type support)
# ---------------------------------------------------------------------------


def test_media_target_types_are_consumer() -> None:
    for code in (0x7F, 0x80, 0x81):
        assert target_type(code) == 0x03
        assert code_group(code) == "media"
    assert TARGET_TYPE_BY_CODE[0x02] == 0x03  # vendor knob/mute, factory type


def test_modifier_and_normal_target_types() -> None:
    assert target_type(0xE0) == 0x02
    assert target_type(0x04) == 0x01
    assert target_type(0xFF) == 0x01


# ---------------------------------------------------------------------------
# Groups + catalog
# ---------------------------------------------------------------------------


def test_group_buckets_cover_the_plan_groups() -> None:
    groups = set(code_group(code) for code in all_assignable_codes())
    for expected in ("alpha", "numeric", "fkeys", "modifiers", "nav", "media", "vendor"):
        assert expected in groups


def test_all_assignable_codes_contains_every_named_code() -> None:
    codes = all_assignable_codes()
    assert codes == sorted(set(codes))
    assert set(HID_CODE_NAMES) <= set(codes)
    assert set(HID_MODIFIER_CODES) <= set(codes)
    assert set(VENDOR_CODE_NAMES) <= set(codes)
    assert KEYMAP_UNASSIGNED in codes
    assert 0xA1 not in codes  # reserved stays out of the dropdown


# ---------------------------------------------------------------------------
# G9 FIX C search aliases (bounded, domain-derived, normalized)
# ---------------------------------------------------------------------------


def test_alias_codes_are_all_assignable() -> None:
    assignable = set(all_assignable_codes())
    assert set(keycodes.CODE_ALIASES) <= assignable


def test_aliases_are_bounded_lowercase_alphanumeric() -> None:
    for code, aliases in keycodes.CODE_ALIASES.items():
        assert 1 <= len(aliases) <= 10, f"alias budget blown at 0x{code:02X}"
        assert len(set(aliases)) == len(aliases), f"dupe alias at 0x{code:02X}"
        for alias in aliases:
            assert _ALIAS_SHAPE.fullmatch(alias), alias
            # Plain single digits are NEVER aliased: the digits-row names
            # "1".."0" already prefix-match those queries (G9 plan).
            assert not (alias.isdigit() and len(alias) == 1), alias


def test_keypad_digits_carry_the_obvious_alias_family() -> None:
    for digit in range(1, 10):
        aliases = code_aliases(0x58 + digit)  # 0x59="1" … 0x61="9"
        for alias in (f"num{digit}", f"numpad{digit}", f"kp{digit}", f"p{digit}"):
            assert alias in aliases, f"0x{0x58 + digit:02X} missing {alias}"
    assert {"num0", "numpad0", "kp0", "p0"} <= set(code_aliases(0x62))


def test_code_aliases_api_shape() -> None:
    assert isinstance(code_aliases(0x59), tuple)
    assert code_aliases(0x59) == ("num1", "numpad1", "kp1", "p1")
    assert code_aliases(0x04) == ()  # plain letters need no aliases
    assert "fn" in keycodes.code_to_name(0xA0).split(" ")[0].lower()
    assert "f1" in keycodes.code_to_name(0x3A).lower()  # f1-style stays


# ---------------------------------------------------------------------------
# Import hygiene (no circulars)
# ---------------------------------------------------------------------------


def test_keycodes_imports_nothing_from_project() -> None:
    import inspect

    source = inspect.getsource(keycodes)
    assert "from .backend" not in source
    assert "import backend" not in source
    assert "from open_glorious_core" not in source


def test_backend_slot_labels_resolve_and_keycodes_is_standalone() -> None:
    # Importing both modules in any order works (no cycle) and the slot label
    # map is intact for the UI grid.
    from open_glorious_core import backend as backend_module
    from open_glorious_core import keycodes as keycodes_module

    assert backend_module.KEYMAP_SLOT_LABELS[0] == "Esc"
    assert keycodes_module.code_to_name(FACTORY_TABLE[0]) == "Escape (0x29)"
    assert StockGmmkProBackend is backend_module.StockGmmkProBackend
