"""Strict AI assessment judgements and deterministic weighted totals."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EvidenceLevel = Literal["sufficient", "partial", "insufficient"]
_EVENT_ID_PATTERN = r"^chunk-[1-9][0-9]*#event-[1-9][0-9]*$"


class AssessmentDimension(BaseModel):
    """One trusted dimension copied from a published plan snapshot."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
    name: Annotated[str, Field(min_length=1, max_length=50)]
    description: Annotated[str, Field(max_length=500)]
    weight_bps: Annotated[int, Field(ge=1, le=10_000)]
    order: Annotated[int, Field(ge=1)]


class AssessmentDimensionJudgement(BaseModel):
    """A provider-produced score. It never controls weights or totals."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dimension_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
    score: Annotated[int, Field(ge=0, le=100)]
    evidence_level: EvidenceLevel
    confidence: Annotated[float, Field(ge=0, le=1)]
    reason: Annotated[str, Field(min_length=1, max_length=500)]
    evidence_event_ids: list[Annotated[str, Field(pattern=_EVENT_ID_PATTERN)]] = Field(
        max_length=3
    )

    @field_validator("reason")
    @classmethod
    def validate_safe_reason(cls, value: str) -> str:
        lowered = value.lower()
        if any(marker in lowered for marker in ("```", "http://", "https://", "s3://")):
            raise ValueError("assessment reason contains a forbidden address or code fence")
        if re.search(r"(?:/Users/|/home/|[A-Za-z]:[\\/]|[\r\n])", value):
            raise ValueError("assessment reason contains an absolute path")
        return value

    @model_validator(mode="after")
    def validate_evidence_level(self) -> AssessmentDimensionJudgement:
        if len(self.evidence_event_ids) != len(set(self.evidence_event_ids)):
            raise ValueError("evidence ids must be unique")
        if self.evidence_level == "partial" and self.score > 79:
            raise ValueError("score exceeds partial evidence level cap")
        if self.evidence_level == "insufficient" and self.score > 59:
            raise ValueError("score exceeds insufficient evidence level cap")
        if self.evidence_level != "insufficient" and not self.evidence_event_ids:
            raise ValueError("non-insufficient evidence level requires an evidence event")
        return self


class AssessmentDimensionScore(AssessmentDimensionJudgement):
    """One persisted score enriched only with trusted server values."""

    dimension_name: Annotated[str, Field(min_length=1, max_length=50)]
    weight_bps: Annotated[int, Field(ge=1, le=10_000)]
    weighted_score: Annotated[float, Field(ge=0, le=100)]


class AssessmentScore(BaseModel):
    """Read-only AI score persisted in the student brief."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    scoring_rule_version: Literal["ai-score-v1"] = "ai-score-v1"
    overall_score: Annotated[float, Field(ge=0, le=100)]
    dimensions: list[AssessmentDimensionScore] = Field(min_length=1, max_length=10)


def build_assessment_score(
    dimensions: list[AssessmentDimension],
    judgements: list[AssessmentDimensionJudgement],
    *,
    allowed_event_ids: set[str],
) -> AssessmentScore:
    """Validate provider judgements, copy weights, and calculate one total."""

    ordered_dimensions = sorted(dimensions, key=lambda row: row.order)
    if not ordered_dimensions or sum(row.weight_bps for row in ordered_dimensions) != 10_000:
        raise ValueError("assessment dimension weights must total 10000 basis points")
    dimension_ids = [row.id for row in ordered_dimensions]
    judgement_ids = [row.dimension_id for row in judgements]
    if len(dimension_ids) != len(set(dimension_ids)) or len(judgement_ids) != len(
        set(judgement_ids)
    ):
        raise ValueError("assessment dimensions do not match provider judgements")
    if set(dimension_ids) != set(judgement_ids):
        raise ValueError("assessment dimensions do not match provider judgements")
    if any(
        event_id not in allowed_event_ids
        for judgement in judgements
        for event_id in judgement.evidence_event_ids
    ):
        raise ValueError("provider cited an unknown evidence event")

    judgements_by_id = {row.dimension_id: row for row in judgements}
    rows: list[AssessmentDimensionScore] = []
    total = Decimal(0)
    for dimension in ordered_dimensions:
        judgement = judgements_by_id[dimension.id]
        contribution = (
            Decimal(judgement.score) * Decimal(dimension.weight_bps) / Decimal(10_000)
        )
        total += contribution
        rows.append(
            AssessmentDimensionScore(
                **judgement.model_dump(),
                dimension_name=dimension.name,
                weight_bps=dimension.weight_bps,
                weighted_score=float(
                    contribution.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                ),
            )
        )
    return AssessmentScore(
        overall_score=float(total.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
        dimensions=rows,
    )
