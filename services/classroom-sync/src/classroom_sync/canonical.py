"""Canonical JSON hashing shared by plan publication and audit records."""

from __future__ import annotations

import json
import math
import unicodedata
from collections.abc import Mapping
from hashlib import sha256
from typing import Any


def canonical_json_bytes(value: object) -> bytes:
    """Encode JSON with stable key order and NFC-normalized strings."""

    return json.dumps(
        _normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_json(value: object) -> str:
    """Return the lowercase SHA-256 digest of canonical JSON bytes."""

    return sha256(canonical_json_bytes(value)).hexdigest()


def _normalize(value: object) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        raw_keys: dict[str, str] = {}
        for raw_key, raw_value in value.items():
            if not isinstance(raw_key, str):
                raise TypeError("Canonical JSON object keys must be strings.")
            key = unicodedata.normalize("NFC", raw_key)
            if key in raw_keys and raw_keys[key] != raw_key:
                raise ValueError("Canonical JSON object keys collide after normalization.")
            raw_keys[key] = raw_key
            normalized[key] = _normalize(raw_value)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Canonical JSON does not permit non-finite floats.")
    if value is None or isinstance(value, bool | int | float):
        return value
    raise TypeError(f"Unsupported canonical JSON value: {type(value).__name__}")
