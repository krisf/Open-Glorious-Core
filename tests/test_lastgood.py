"""G12 item 1 — the last-good snapshot store (unit, zero device IO).

Contract (G12_AUTOVERIFY_PLAN.md items 1 + 4, lastgood.py docstring):

* per-(profile, layer) file ``last-good-<p>-<l>.json`` in the config dir
  ($OGC_CONFIG_DIR in this suite — conftest isolates it into tmp_path);
* every applied matrix is PERSISTED, but the applied codes ride in
  ``pending`` — the revertable ``codes`` change ONLY on ``confirm()`` (an
  unconfirmed write must never become its own revert target);
* writes are atomic (tmp + os.replace); corrupt/absent reads are None;
* the seed vector is the packaged factory table, flagged
  ``seed: true, verified: false``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from open_glorious_core import lastgood
from open_glorious_core.lastgood import LastGoodError

ROOT = Path(__file__).resolve().parents[1]
FACTORY = (ROOT / "src" / "open_glorious_core" / "data" / "default_keymap.bin").read_bytes()


def applied_vector() -> list[int]:
    """A plausible applied remap: caps cell (58) → Keypad 1 (0x59)."""
    codes = bytearray(FACTORY)
    codes[58] = 0x59
    return list(codes)


def test_seed_flags_are_honest() -> None:
    state = lastgood.ensure_seed(1, 1, FACTORY)
    assert state["profile"] == 1 and state["layer"] == 1
    assert state["codes"] == list(FACTORY)
    assert state["verified"] is False, "a seed has NEVER been operator-confirmed"
    assert state["seed"] is True
    assert state["pending"] is None


def test_path_per_profile_layer(tmp_path: Path) -> None:
    lastgood.save(2, 3, FACTORY)
    path = lastgood.path_for(2, 3)
    assert path.name == "last-good-2-3.json"
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["codes"] == list(FACTORY)
    # The conftest autouse guard redirects the config dir into this test's
    # tmp_path — nothing is ever written under the real ~/.config.
    assert str(tmp_path) in str(path)


def test_config_dir_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OGC_CONFIG_DIR", str(tmp_path / "from-env"))
    assert lastgood.config_dir() == tmp_path / "from-env"
    assert lastgood.config_dir(tmp_path / "explicit") == tmp_path / "explicit"
    monkeypatch.delenv("OGC_CONFIG_DIR")
    assert lastgood.config_dir() == Path.home() / ".config" / "open-glorious-core"


def test_save_is_atomic_tmp_plus_replace(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str]] = []
    real_replace = lastgood.os.replace

    def spy(src: str, dst: str) -> None:
        seen.append((str(src), str(dst)))
        real_replace(src, dst)

    monkeypatch.setattr(lastgood.os, "replace", spy)
    lastgood.save(1, 1, FACTORY)
    assert len(seen) == 1
    src, dst = seen[0]
    assert src.endswith("last-good-1-1.json.tmp")
    assert src != dst and dst.endswith("last-good-1-1.json")
    assert not Path(src).exists()  # rename consumed the tmp file


def test_record_apply_seeds_then_pends() -> None:
    result = lastgood.record_apply(1, 1, applied_vector(), FACTORY)
    assert result["seed"] is True and result["verified"] is False
    # The unconfirmed write is PERSISTED (item 1) …
    assert result["pending"] == applied_vector()
    # … but it is NOT the revertable state (item 4): the seed stays.
    assert result["codes"] == list(FACTORY)


def test_confirm_promotes_pending_and_clears_seed() -> None:
    lastgood.record_apply(1, 1, applied_vector(), FACTORY)
    confirmed = lastgood.confirm(1, 1)
    assert confirmed is not None
    assert confirmed["codes"] == applied_vector()
    assert confirmed["verified"] is True
    assert confirmed["seed"] is False
    assert confirmed["pending"] is None


def test_record_apply_never_overwrites_verified_codes() -> None:
    lastgood.record_apply(1, 1, applied_vector(), FACTORY)
    lastgood.confirm(1, 1)
    second = bytearray(FACTORY)
    second[58] = 0x68  # a later, unconfirmed F13 write
    result = lastgood.record_apply(1, 1, list(second), FACTORY)
    assert result["codes"] == applied_vector(), (
        "the revert target must stay the last OPERATOR-CONFIRMED matrix"
    )
    assert result["pending"] == list(second)
    assert result["verified"] is True


def test_confirm_without_file_returns_none() -> None:
    assert lastgood.confirm(3, 2) is None


def test_ensure_seed_never_touches_an_existing_file() -> None:
    lastgood.record_apply(1, 1, applied_vector(), FACTORY)
    state = lastgood.ensure_seed(1, 1, bytes(120))  # bogus factory must be ignored
    assert state["codes"] == list(FACTORY)
    assert state["pending"] == applied_vector()


def test_corrupt_file_loads_none_and_reseeds() -> None:
    path = lastgood.path_for(1, 1)
    lastgood.save(1, 1, FACTORY)
    path.write_text("{ not json", encoding="utf-8")
    assert lastgood.load(1, 1) is None
    state = lastgood.ensure_seed(1, 1, FACTORY)
    assert state["seed"] is True


def test_truncated_matrix_loads_none() -> None:
    lastgood.save(1, 1, FACTORY)
    path = lastgood.path_for(1, 1)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["codes"] = payload["codes"][:59]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert lastgood.load(1, 1) is None, "a partial matrix can never be trusted"


def test_invalid_payloads_rejected() -> None:
    with pytest.raises(LastGoodError):
        lastgood.save(1, 1, [0x29] * 119)  # wrong length
    with pytest.raises(LastGoodError):
        lastgood.save(1, 1, [0x1FF] * 120)  # out of byte range
    with pytest.raises(LastGoodError):
        lastgood.save(1, 1, [True] * 120)  # bools are not byte values
