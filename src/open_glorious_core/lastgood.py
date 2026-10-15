"""G12 — last-good keymap snapshot store (self-healing revert insurance).

One JSON file per (profile, layer) pair under the app config dir:

    ~/.config/open-glorious-core/last-good-<profile>-<layer>.json

Schema (validated on load):

    {"profile": int, "layer": int,
     "codes": [120 ints],        # the revertable matrix
     "verified": bool,           # an operator confirmed a post-write press
     "seed": bool,               # codes are the packaged factory vector
     "pending": [120 ints] | None}  # last applied, awaiting confirmation

Semantics (G12 plan items 1 + 4, byte-for-byte):

* Every successful ``apply-keymap`` persists the applied matrix. When no
  verified state exists yet the file is (re)seeded from the packaged factory
  vector — ``seed: true, verified: false`` — and the applied matrix rides in
  ``pending``; the verified ``codes`` (the revertable last-good) are NEVER
  overwritten by an unconfirmed write.
* A confirmed verify press (``confirm()``) promotes ``pending`` into
  ``codes`` and sets ``verified: true, seed: false``.
* The armed auto-revert writes ``codes`` back ONLY when ``verified`` is true.
  A seed state is "unchanged factory" — safe to revert to in principle, but
  the timer must not auto-write before the operator has confirmed any press
  for this profile pair (app.py flags that honestly in the banner).

Writes are atomic: tmp file + ``os.replace`` (a crash mid-write can never
leave a half-written last-good). Reads of a missing/corrupt file return
``None`` — the caller reseeds; a corrupt file is never trusted for a write.

The config dir is ``$OGC_CONFIG_DIR`` when set (tests redirect it into
tmp_path), else ``~/.config/open-glorious-core``. Zero device I/O here.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_DIR_ENV = "OGC_CONFIG_DIR"
CONFIG_DIR_NAME = "open-glorious-core"
KEYMAP_SLOTS = 120  # restated from backend to keep this module import-free


class LastGoodError(ValueError):
    """Raised on a payload that must never be persisted as last-good."""


def config_dir(root: Path | str | None = None) -> Path:
    """Config dir: explicit root > $OGC_CONFIG_DIR > ~/.config/open-glorious-core."""
    if root is not None:
        return Path(root).expanduser()
    env = os.environ.get(CONFIG_DIR_ENV)
    if env:
        return Path(env).expanduser()
    return Path.home() / ".config" / CONFIG_DIR_NAME


def path_for(profile: int, layer: int, root: Path | str | None = None) -> Path:
    return config_dir(root) / f"last-good-{profile}-{layer}.json"


def _validate_matrix(codes: Any, field: str) -> list[int]:
    if not isinstance(codes, list) or len(codes) != KEYMAP_SLOTS:
        raise LastGoodError(f"{field} must be a list of {KEYMAP_SLOTS} bytes")
    values = []
    for value in codes:
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFF:
            raise LastGoodError(f"{field} entries must be ints 0..255")
        values.append(int(value))
    return values


def _validated(payload: dict, profile: int, layer: int) -> dict:
    codes = _validate_matrix(payload.get("codes"), "codes")
    pending = payload.get("pending")
    pending_codes = (
        None if pending is None else _validate_matrix(pending, "pending")
    )
    return {
        "profile": profile,
        "layer": layer,
        "codes": codes,
        "verified": bool(payload.get("verified")),
        "seed": bool(payload.get("seed")),
        "pending": pending_codes,
    }


def load(profile: int, layer: int, root: Path | str | None = None) -> dict | None:
    """Read + validate the pair's snapshot; None when absent or corrupt."""
    path = path_for(profile, layer, root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return _validated(raw, profile, layer)
    except LastGoodError:
        return None


def save(
    profile: int,
    layer: int,
    codes: list[int] | bytes,
    *,
    verified: bool = False,
    seed: bool = False,
    pending: list[int] | None = None,
    root: Path | str | None = None,
) -> Path:
    """Atomically persist the snapshot (tmp + os.replace)."""
    payload = _validated(
        {
            "codes": list(codes),
            "verified": bool(verified),
            "seed": bool(seed),
            "pending": list(pending) if pending is not None else None,
        },
        profile,
        layer,
    )
    path = path_for(profile, layer, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def ensure_seed(
    profile: int,
    layer: int,
    factory: bytes | list[int],
    root: Path | str | None = None,
) -> dict:
    """Create the factory seed (verified: false, seed: true) when absent."""
    existing = load(profile, layer, root)
    if existing is not None:
        return existing
    save(profile, layer, factory, verified=False, seed=True, root=root)
    state = load(profile, layer, root)
    assert state is not None  # just written
    return state


def record_apply(
    profile: int,
    layer: int,
    applied: bytes | list[int],
    factory: bytes | list[int],
    root: Path | str | None = None,
) -> dict:
    """Persist a successfully applied matrix.

    The applied codes go to ``pending`` — the revertable ``codes`` change only
    on ``confirm()`` (an unconfirmed write must never become the revert target,
    G12 item 4). A missing file is first seeded from the factory vector.
    """
    state = ensure_seed(profile, layer, factory, root)
    save(
        profile,
        layer,
        state["codes"],
        verified=state["verified"],
        seed=state["seed"],
        pending=list(applied),
        root=root,
    )
    result = load(profile, layer, root)
    assert result is not None
    return result


def confirm(profile: int, layer: int, root: Path | str | None = None) -> dict | None:
    """Operator confirmed a post-write press: promote pending -> verified codes."""
    state = load(profile, layer, root)
    if state is None:
        return None
    codes = state["pending"] if state["pending"] is not None else state["codes"]
    save(profile, layer, codes, verified=True, seed=False, pending=None, root=root)
    result = load(profile, layer, root)
    assert result is not None
    return result
