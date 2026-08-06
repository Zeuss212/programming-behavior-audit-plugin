"""Canonical JSON encoding and private atomic JSON writes."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def normalize_json_value(value: object) -> Any:
    """Return a recursively NFC-normalized JSON value."""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        originals: dict[str, str] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Canonical JSON object keys must be strings.")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in originals and originals[normalized_key] != key:
                raise ValueError("JSON object keys collide after NFC normalization.")
            originals[normalized_key] = key
            normalized[normalized_key] = normalize_json_value(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [normalize_json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Canonical JSON does not permit non-finite floats.")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise TypeError(f"Unsupported canonical JSON value: {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    """Encode a value as compact, sorted, recursively NFC-normalized JSON."""
    normalized = normalize_json_value(value)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: object) -> str:
    """Return the lowercase SHA-256 digest of canonical JSON."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def atomic_write_json(path: Path, value: object) -> None:
    """Atomically replace *path* with private canonical JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            os.fchmod(temporary.fileno(), 0o600)
            temporary.write(canonical_json_bytes(value))
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass
