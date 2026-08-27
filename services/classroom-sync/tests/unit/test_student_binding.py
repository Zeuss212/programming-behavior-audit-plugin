"""Contract tests for strict FinColab student-binding metadata."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from classroom_sync.auth.student_binding import (
    StudentBindingV1,
    encode_student_binding_v1,
    parse_legacy_child_name,
    parse_student_binding_description,
    safe_legacy_key,
)
from classroom_sync.errors import RosterConflictError


GOLDEN_PATH = (
    Path(__file__).resolve().parents[4]
    / "contracts/classroom/v1/fincolab-student-binding-v1.golden.json"
)
GOLDEN = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
PARENT_MARKER = "[FINCOLAB_PARENT_PROJECT_ID:parent-1]"


def binding_description(payload: str) -> str:
    return f"{PARENT_MARKER}[FINCOLAB_STUDENT_BINDING_V1:{payload}]\nCreated for review"


def encoded_json(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@pytest.mark.parametrize("vector", GOLDEN["canonical"])
def test_canonical_vectors_round_trip_byte_for_byte(vector: dict[str, object]) -> None:
    """Changing JSON ordering, Unicode encoding, or Base64url breaks this contract."""

    binding = StudentBindingV1(**vector["binding"])

    assert encode_student_binding_v1(binding) == vector["payload"]
    assert parse_student_binding_description(binding_description(vector["payload"])) == binding


def test_canonical_vector_exercises_urlsafe_dash_and_underscore_alphabet() -> None:
    """A codec using standard Base64 cannot satisfy this Unicode golden vector."""

    payload = GOLDEN["canonical"][1]["payload"]

    assert "-" in payload
    assert "_" in payload


@pytest.mark.parametrize("payload", GOLDEN["rejected_payloads"].values())
def test_rejects_noncanonical_or_invalid_payloads(payload: str) -> None:
    """Any malformed, lossy, or noncanonical payload must fail closed."""

    with pytest.raises(RosterConflictError, match="student_binding_marker_malformed"):
        parse_student_binding_description(binding_description(payload))


@pytest.mark.parametrize(
    "binding",
    [
        {
            "parent_algorithm_id": "parent-1",
            "space_id": "space-1",
            "student_id": "student-1",
            "student_username": "x" * 257,
        },
        {
            "parent_algorithm_id": "parent-1",
            "space_id": "space-1",
            "student_id": "",
            "student_username": "student-a",
        },
    ],
)
def test_rejects_invalid_decoded_field_bounds(binding: dict[str, str]) -> None:
    """Removing field validation would permit unbounded or empty roster identities."""

    with pytest.raises(RosterConflictError, match="student_binding_marker_malformed"):
        parse_student_binding_description(binding_description(encoded_json(binding)))


def test_rejects_oversized_payload_and_description() -> None:
    """Removing transport bounds would permit unbounded metadata to reach JSON parsing."""

    with pytest.raises(RosterConflictError, match="student_binding_marker_malformed"):
        parse_student_binding_description(binding_description("a" * 2049))
    with pytest.raises(RosterConflictError, match="student_binding_marker_malformed"):
        parse_student_binding_description(binding_description(GOLDEN["canonical"][0]["payload"]) + "x" * 4096)


def test_no_binding_marker_is_the_only_case_that_returns_none() -> None:
    """Treating malformed binding metadata as absent would incorrectly enable legacy fallback."""

    assert parse_student_binding_description("[FINCOLAB_PARENT_PROJECT_ID:parent-1]\nLegacy child") is None


@pytest.mark.parametrize(
    ("description", "code"),
    [
        (
            f"{PARENT_MARKER}[FINCOLAB_STUDENT_BINDING_V2:abc]",
            "student_binding_marker_unknown_version",
        ),
        (
            f"{PARENT_MARKER}prefix[FINCOLAB_STUDENT_BINDING_V1:abc]",
            "student_binding_marker_malformed",
        ),
        (
            f"[FINCOLAB_STUDENT_BINDING_V1:{GOLDEN['canonical'][0]['payload']}]",
            "student_binding_marker_malformed",
        ),
        (
            f"{PARENT_MARKER}[FINCOLAB_STUDENT_BINDING_V1:{GOLDEN['canonical'][0]['payload']}] trailing",
            "student_binding_marker_malformed",
        ),
    ],
)
def test_marker_family_never_falls_back_when_first_line_is_not_the_exact_pair(
    description: str, code: str
) -> None:
    """A malformed or future marker is distinct from an unmarked legacy description."""

    with pytest.raises(RosterConflictError, match=code):
        parse_student_binding_description(description)


def test_safe_legacy_key_matches_javascript_utf16_code_unit_replacement() -> None:
    """Iterating Python Unicode scalars would turn one astral character into one dash."""

    assert safe_legacy_key("A😀B") == "A--B"
    assert safe_legacy_key("A\ud800B") == "A-B"
    assert safe_legacy_key("学生") == "--"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("exp-student-1-a1b2", "student-1"),
        ("exp-student-1-A1B2", None),
        ("exp-student-1-abc", None),
        ("EXP-student-1-a1b2", None),
        ("qa.v1-student_1-z9y0", "student_1"),
    ],
)
def test_legacy_child_name_requires_literal_prefix_and_lowercase_base36_suffix(
    name: str, expected: str | None
) -> None:
    """Relaxing prefix escaping or suffix casing would bind the wrong legacy child."""

    prefix = "qa.v1" if name.startswith("qa.v1") else "exp"

    assert parse_legacy_child_name(name, prefix) == expected
