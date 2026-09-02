"""Strict codecs for FinColab classroom student-binding metadata."""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import NoReturn

from classroom_sync.errors import RosterConflictError

MAX_DESCRIPTION_LENGTH = 4096
MAX_PAYLOAD_LENGTH = 2048
MAX_FIELD_LENGTH = 256

_BINDING_TAG_FAMILY = "FINCOLAB_STUDENT_BINDING_"
_V1_FIRST_LINE_PATTERN = re.compile(
    r"\[FINCOLAB_PARENT_PROJECT_ID:([^\]\r\n]+)\]"
    r"\[FINCOLAB_STUDENT_BINDING_V1:([A-Za-z0-9_-]+)\]"
)
_UNKNOWN_VERSION_FIRST_LINE_PATTERN = re.compile(
    r"\[FINCOLAB_PARENT_PROJECT_ID:[^\]\r\n]+\]"
    r"\[FINCOLAB_STUDENT_BINDING_([A-Za-z0-9_]+):[A-Za-z0-9_-]+\]"
)
_PAYLOAD_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class StudentBindingV1:
    space_id: str
    parent_algorithm_id: str
    student_id: str
    student_username: str


def encode_student_binding_v1(binding: StudentBindingV1) -> str:
    """Encode one binding as unpadded URL-safe Base64 of canonical UTF-8 JSON."""

    try:
        raw_json = _canonical_json(binding).encode("utf-8")
    except UnicodeEncodeError as error:
        _raise_malformed(error)
    return base64.urlsafe_b64encode(raw_json).rstrip(b"=").decode("ascii")


def parse_student_binding_description(description: str) -> StudentBindingV1 | None:
    """Parse an exact V1 first-line marker, or return ``None`` for unmarked legacy text."""

    if not isinstance(description, str) or len(description) > MAX_DESCRIPTION_LENGTH:
        _raise_malformed()
    if _BINDING_TAG_FAMILY not in description:
        return None
    first_line = description.split("\n", 1)[0]
    if description.count(_BINDING_TAG_FAMILY) != 1:
        _raise_malformed()
    v1_match = _V1_FIRST_LINE_PATTERN.fullmatch(first_line)
    if v1_match is None:
        unknown = _UNKNOWN_VERSION_FIRST_LINE_PATTERN.fullmatch(first_line)
        if unknown is not None and unknown.group(1) != "V1":
            raise RosterConflictError("student_binding_marker_unknown_version")
        _raise_malformed()
    payload = v1_match.group(2)
    if len(payload) > MAX_PAYLOAD_LENGTH or _PAYLOAD_PATTERN.fullmatch(payload) is None:
        _raise_malformed()
    binding = _decode_binding_payload(payload)
    if encode_student_binding_v1(binding) != payload:
        _raise_malformed()
    return binding


def safe_legacy_key(username: str) -> str:
    """Match JavaScript's ASCII-safe replacement over UTF-16 code units exactly."""

    code_units = username.encode("utf-16-le", "surrogatepass")
    safe_units: list[str] = []
    for offset in range(0, len(code_units), 2):
        code_unit = int.from_bytes(code_units[offset: offset + 2], byteorder="little")
        if 48 <= code_unit <= 57 or 65 <= code_unit <= 90 or 97 <= code_unit <= 122 or code_unit in {45, 95}:
            safe_units.append(chr(code_unit))
        else:
            safe_units.append("-")
    return "".join(safe_units)


def parse_legacy_child_name(name: str, prefix: str) -> str | None:
    """Return a legacy safe key only for the exact configured child-name grammar."""

    match = re.fullmatch(re.escape(prefix) + r"-([A-Za-z0-9_-]*)-([0-9a-z]{4})", name)
    return None if match is None else match.group(1)


def _decode_binding_payload(payload: str) -> StudentBindingV1:
    try:
        raw_json = base64.urlsafe_b64decode((payload + "=" * (-len(payload) % 4)).encode("ascii"))
        data = json.loads(raw_json.decode("utf-8"), object_pairs_hook=_object_without_duplicate_keys)
    except (UnicodeDecodeError, binascii.Error, json.JSONDecodeError, ValueError) as error:
        _raise_malformed(error)
    if not isinstance(data, dict) or set(data) != {
        "parent_algorithm_id", "space_id", "student_id", "student_username"
    } or not all(isinstance(value, str) for value in data.values()):
        _raise_malformed()
    return StudentBindingV1(
        space_id=data["space_id"],
        parent_algorithm_id=data["parent_algorithm_id"],
        student_id=data["student_id"],
        student_username=data["student_username"],
    )


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    data: dict[str, object] = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("duplicate JSON key")
        data[key] = value
    return data


def _canonical_json(binding: StudentBindingV1) -> str:
    fields = {
        "parent_algorithm_id": binding.parent_algorithm_id,
        "space_id": binding.space_id,
        "student_id": binding.student_id,
        "student_username": binding.student_username,
    }
    if any(not isinstance(value, str) or not value or len(value) > MAX_FIELD_LENGTH for value in fields.values()):
        _raise_malformed()
    try:
        return json.dumps(fields, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as error:
        _raise_malformed(error)


def _raise_malformed(error: Exception | None = None) -> NoReturn:
    if error is None:
        raise RosterConflictError("student_binding_marker_malformed")
    raise RosterConflictError("student_binding_marker_malformed") from error
