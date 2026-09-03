from __future__ import annotations

import pytest

from classroom_sync.services.assessment_scoring import (
    AssessmentDimension,
    AssessmentDimensionJudgement,
    build_assessment_score,
)


def dimensions() -> list[AssessmentDimension]:
    return [
        AssessmentDimension(
            id="knowledge_mastery",
            name="知识点掌握",
            description="对本实验知识点的理解与运用。",
            weight_bps=3000,
            order=1,
        ),
        AssessmentDimension(
            id="debugging_ability",
            name="调试能力",
            description="识别、修正并验证错误。",
            weight_bps=2500,
            order=2,
        ),
        AssessmentDimension(
            id="test_verification",
            name="测试验证",
            description="主动运行和复测。",
            weight_bps=2000,
            order=3,
        ),
        AssessmentDimension(
            id="requirement_alignment",
            name="题意理解",
            description="实现符合题目和输入输出要求。",
            weight_bps=1500,
            order=4,
        ),
        AssessmentDimension(
            id="coding_fundamentals",
            name="编码基础能力",
            description="语法、结构、健壮性和可读性。",
            weight_bps=1000,
            order=5,
        ),
    ]


def judgement(
    dimension_id: str,
    score: int,
    *,
    evidence_level: str = "sufficient",
) -> AssessmentDimensionJudgement:
    return AssessmentDimensionJudgement.model_validate(
        {
            "dimension_id": dimension_id,
            "score": score,
            "evidence_level": evidence_level,
            "confidence": 0.8,
            "reason": "根据本次可观测的编程过程给出量化判断。",
            "evidence_event_ids": ["chunk-1#event-1"],
        }
    )


def test_build_assessment_score_copies_weights_and_rounds_total_half_up() -> None:
    result = build_assessment_score(
        dimensions(),
        [
            judgement("knowledge_mastery", 82),
            judgement("debugging_ability", 70),
            judgement("test_verification", 65),
            judgement("requirement_alignment", 88),
            judgement("coding_fundamentals", 76),
        ],
        allowed_event_ids={"chunk-1#event-1"},
    )

    assert result.overall_score == 75.9
    assert [row.weight_bps for row in result.dimensions] == [3000, 2500, 2000, 1500, 1000]
    assert [row.weighted_score for row in result.dimensions] == [24.6, 17.5, 13.0, 13.2, 7.6]


@pytest.mark.parametrize(
    ("level", "score"),
    [("partial", 80), ("insufficient", 60)],
)
def test_judgement_rejects_score_above_evidence_level_cap(level: str, score: int) -> None:
    with pytest.raises(ValueError, match="evidence level"):
        judgement("knowledge_mastery", score, evidence_level=level)


def test_build_assessment_score_requires_exactly_one_judgement_per_dimension() -> None:
    with pytest.raises(ValueError, match="dimensions do not match"):
        build_assessment_score(
            dimensions(),
            [judgement("knowledge_mastery", 82)],
            allowed_event_ids={"chunk-1#event-1"},
        )


def test_build_assessment_score_rejects_unknown_evidence_reference() -> None:
    rows = [judgement(dimension.id, 70) for dimension in dimensions()]

    with pytest.raises(ValueError, match="unknown evidence event"):
        build_assessment_score(dimensions(), rows, allowed_event_ids=set())


def test_insufficient_evidence_still_requires_a_numeric_score_without_an_event() -> None:
    rows = [
        judgement(dimension.id, 50, evidence_level="insufficient").model_copy(
            update={"evidence_event_ids": []}
        )
        for dimension in dimensions()
    ]

    result = build_assessment_score(dimensions(), rows, allowed_event_ids=set())

    assert result.overall_score == 50.0
    assert all(row.score == 50 for row in result.dimensions)
