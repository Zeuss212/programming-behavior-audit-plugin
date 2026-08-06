"""Per-dimension validation for untrusted model analysis responses."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class DimensionValidationBatch:
    valid_by_code: dict[str, dict[str, object]]
    errors_by_code: dict[str, str]
    unexpected_codes: tuple[str, ...]


def _dimension_index(
    profile: Mapping[str, object],
) -> tuple[dict[str, Mapping[str, object]], dict[str, str]]:
    raw = profile.get("dimensions")
    if not isinstance(raw, list):
        return {}, {"__profile__": "profile_dimensions_missing"}
    indexed: dict[str, Mapping[str, object]] = {}
    errors: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            errors["__profile__"] = "profile_dimension_invalid"
            continue
        code = item.get("code")
        if not isinstance(code, str) or not code:
            errors["__profile__"] = "profile_dimension_code_missing"
            continue
        if code in indexed:
            errors[code] = "duplicate_profile_dimension"
            indexed.pop(code, None)
            continue
        indexed[code] = item
    return indexed, errors


def _row_error(
    dimension: Mapping[str, object],
    event_ids: set[str],
    row: Mapping[str, object],
) -> str | None:
    evidence_status = row.get("evidence_status")
    if evidence_status not in {"observed", "not_observed"}:
        return "invalid_evidence_status"

    level_code = row.get("level_code")
    levels = dimension.get("levels")
    allowed_levels = (
        {
            item.get("code")
            for item in levels
            if isinstance(item, Mapping)
            and isinstance(item.get("code"), str)
        }
        if isinstance(levels, list)
        else set()
    )
    if evidence_status == "not_observed" and level_code is not None:
        return "not_observed_level_must_be_null"
    if evidence_status == "observed" and level_code not in allowed_levels:
        return "unknown_level_code"

    confidence = row.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        return "invalid_confidence"

    explanation = row.get("explanation")
    if not isinstance(explanation, str) or len(explanation) > 500:
        return "invalid_explanation"

    criteria = dimension.get("evidence_criteria")
    criterion_by_id = (
        {
            item.get("id"): item
            for item in criteria
            if isinstance(item, Mapping)
            and isinstance(item.get("id"), str)
        }
        if isinstance(criteria, list)
        else {}
    )
    claims = row.get("evidence_claims")
    if not isinstance(claims, list):
        return "invalid_evidence_claims"
    if evidence_status == "observed" and not claims:
        return "observed_requires_evidence"
    normalized_claims: list[dict[str, object]] = []
    for claim in claims:
        if not isinstance(claim, Mapping):
            return "invalid_evidence_claim"
        if set(claim) != {
            "event_id",
            "criterion_id",
            "direction",
            "claim",
        }:
            return "invalid_evidence_claim"
        event_id = claim.get("event_id")
        criterion_id = claim.get("criterion_id")
        direction = claim.get("direction")
        text = claim.get("claim")
        if not isinstance(event_id, str) or event_id not in event_ids:
            return "unknown_evidence_event"
        criterion = criterion_by_id.get(criterion_id)
        if not isinstance(criterion, Mapping):
            return "unknown_criterion"
        if (
            direction not in {"support", "exclude"}
            or direction != criterion.get("direction")
        ):
            return "criterion_direction_mismatch"
        if not isinstance(text, str) or not text.strip():
            return "invalid_evidence_claim"
        normalized_claims.append(dict(claim))
    return None


def validate_dimension_response(
    profile: Mapping[str, object],
    event_ids: set[str],
    payload: Mapping[str, object],
) -> DimensionValidationBatch:
    """Validate expected dimensions independently and preserve valid rows."""

    dimensions, errors = _dimension_index(profile)
    valid: dict[str, dict[str, object]] = {}
    unexpected: set[str] = set()
    seen: set[str] = set()
    raw_rows = payload.get("dimensions")
    if not isinstance(raw_rows, list):
        raw_rows = []

    for raw_row in raw_rows:
        if not isinstance(raw_row, Mapping):
            errors["__response__"] = "dimension_row_invalid"
            continue
        code = raw_row.get("dimension_code")
        if not isinstance(code, str) or not code:
            errors["__response__"] = "dimension_code_missing"
            continue
        if code not in dimensions:
            unexpected.add(code)
            continue
        if code in seen:
            errors[code] = "duplicate_response_dimension"
            valid.pop(code, None)
            continue
        seen.add(code)
        if code in errors:
            continue
        reason = _row_error(dimensions[code], event_ids, raw_row)
        if reason is None:
            valid[code] = dict(raw_row)
        else:
            errors[code] = reason

    for code in dimensions:
        if code not in valid and code not in errors:
            errors[code] = "missing_dimension"
    return DimensionValidationBatch(
        valid_by_code=valid,
        errors_by_code=errors,
        unexpected_codes=tuple(sorted(unexpected)),
    )
