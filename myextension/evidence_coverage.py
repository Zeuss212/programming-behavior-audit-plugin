"""Deterministic evidence-coverage gate applied before AI analysis."""

from __future__ import annotations

import math
from collections.abc import Mapping


_SUPPORTED_MINIMUM_SIGNALS = {
    "valid_observation_duration_ms",
    "edit_event_count",
    "run_count",
}
_OPPORTUNITY_SIGNALS = {
    "DEBUG_CHAIN": "failed_run_count",
    "REPEATED_RUN_FAILURES": "run_count",
    "PAUSE_WITHOUT_VALIDATION": "active_idle_count",
    "REPEATED_EDITING": "edit_event_count",
}


def _result(
    *,
    status: str,
    missing: list[str],
    opportunities: int,
    reason_code: str,
    reason: str,
) -> dict[str, object]:
    return {
        "status": status,
        "missing_required_signals": missing,
        "observation_opportunities": opportunities,
        "reason_code": reason_code,
        "reason": reason,
    }


def _valid_number(value: object, *, count: bool) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not math.isfinite(value) or value < 0:
        return False
    if count and not isinstance(value, int):
        return False
    return True


def _insufficient_reason(unmet: set[str]) -> str:
    if "valid_observation_duration_ms" in unmet:
        return "有效观察时长不足"
    if "edit_event_count" in unmet:
        return "编辑事件数量不足"
    if "run_count" in unmet:
        return "运行次数不足"
    return "最低观察要求不足"


def evaluate_coverage(
    dimension: Mapping[str, object],
    features: Mapping[str, object],
) -> dict[str, object]:
    """Evaluate the frozen Pilot minimum-observation contract.

    The keys present in ``minimum_observation`` are the dimension's required
    signals. Threshold comparison is inclusive.
    """

    config = dimension.get("analysis_config")
    if not isinstance(config, Mapping):
        return _result(
            status="not_computable",
            missing=["minimum_observation"],
            opportunities=0,
            reason_code="required_signal_not_computable",
            reason="所需信号缺失或无效",
        )
    minimum = config.get("minimum_observation")
    if not isinstance(minimum, Mapping) or not minimum:
        return _result(
            status="not_computable",
            missing=["minimum_observation"],
            opportunities=0,
            reason_code="required_signal_not_computable",
            reason="所需信号缺失或无效",
        )

    required = sorted(
        key for key in minimum if isinstance(key, str)
    )
    unsupported = sorted(
        key for key in required if key not in _SUPPORTED_MINIMUM_SIGNALS
    )
    if len(required) != len(minimum):
        unsupported.append("minimum_observation")
    if unsupported:
        return _result(
            status="not_computable",
            missing=sorted(set(unsupported)),
            opportunities=0,
            reason_code="required_signal_not_computable",
            reason="所需信号缺失或无效",
        )
    invalid: list[str] = []
    for key in required:
        count_signal = key.endswith("_count")
        if not _valid_number(
            minimum.get(key),
            count=count_signal,
        ) or not _valid_number(
            features.get(key),
            count=count_signal,
        ):
            invalid.append(key)
    if invalid:
        return _result(
            status="not_computable",
            missing=sorted(set(invalid)),
            opportunities=0,
            reason_code="required_signal_not_computable",
            reason="所需信号缺失或无效",
        )

    unmet = {
        key
        for key in required
        if features[key] < minimum[key]  # type: ignore[operator]
    }
    if unmet:
        return _result(
            status="insufficient_evidence",
            missing=[],
            opportunities=0,
            reason_code="minimum_observation_not_met",
            reason=_insufficient_reason(unmet),
        )

    opportunity_signal = _OPPORTUNITY_SIGNALS.get(dimension.get("code"))
    opportunity_value = (
        features.get(opportunity_signal)
        if opportunity_signal is not None
        else None
    )
    opportunities = (
        int(opportunity_value)
        if _valid_number(opportunity_value, count=True)
        else 0
    )
    return _result(
        status="sufficient_for_analysis",
        missing=[],
        opportunities=opportunities,
        reason_code="minimum_observation_met",
        reason="已达到最低观察要求",
    )
