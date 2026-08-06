from __future__ import annotations

import json
from pathlib import Path

import pytest

from myextension.canonical_json import sha256_json
from myextension.evidence_coverage import evaluate_coverage
from myextension.feature_extractor import FEATURE_NAMES, extract_features


RESOURCE_PATH = (
    Path(__file__).parents[1]
    / "resources"
    / "signal_dictionary"
    / "pilot-v1.json"
)
EXPECTED_FEATURE_NAMES = {
    "valid_observation_duration_ms",
    "edit_event_count",
    "delete_event_count",
    "paste_event_count",
    "run_count",
    "failed_run_count",
    "active_idle_count",
    "active_idle_total_duration_ms",
    "page_away_duration_ms",
    "failure_edit_success_chain_count",
    "error_type_change_count",
}


@pytest.fixture
def signal_dictionary() -> dict[str, object]:
    value = json.loads(RESOURCE_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def event(
    sequence: int,
    segment_type: str,
    started_ms: int,
    ended_ms: int,
    **overrides: object,
) -> dict[str, object]:
    def timestamp(milliseconds: int) -> str:
        seconds, remainder = divmod(milliseconds, 1000)
        return f"2026-07-28T09:00:{seconds:02d}.{remainder:03d}+08:00"

    value: dict[str, object] = {
        "event_id": (
            "60000000-0000-4000-8000-000000000001"
            f":{sequence}"
        ),
        "session_seq": sequence,
        "segment_type": segment_type,
        "started_at": timestamp(started_ms),
        "ended_at": timestamp(ended_ms),
        "duration_ms": ended_ms - started_ms,
        "notebook_id": "synthetic-notebook",
        "notebook_path": "synthetic.ipynb",
        "cell_id": "cell-1",
        "cell_index": 0,
    }
    value.update(overrides)
    return value


def synthetic_recovery_sequence() -> list[dict[str, object]]:
    return [
        event(1, "code_writing", 0, 1000),
        event(
            2,
            "code_execution",
            1000,
            4000,
            execution_result="failure",
            error_type="NameError",
        ),
        event(3, "code_deletion", 4000, 4500),
        event(
            4,
            "code_execution",
            4500,
            5000,
            execution_result="success",
        ),
        event(5, "idle", 5000, 8000),
        event(6, "code_writing", 8000, 9000),
    ]


def dimension(
    minimum_observation: dict[str, int] | None = None,
) -> dict[str, object]:
    config: dict[str, object] = {"mode": "llm_evidence"}
    if minimum_observation is not None:
        config["minimum_observation"] = minimum_observation
    return {
        "code": "DEBUG_CHAIN",
        "analysis_config": config,
    }


def test_extracts_exact_pilot_feature_set_from_sorted_events(
    signal_dictionary: dict[str, object],
) -> None:
    events = list(reversed(synthetic_recovery_sequence()))

    features = extract_features(events, signal_dictionary)

    assert set(FEATURE_NAMES) == EXPECTED_FEATURE_NAMES
    assert set(features) == EXPECTED_FEATURE_NAMES
    assert features == {
        "valid_observation_duration_ms": 5500,
        "edit_event_count": 3,
        "delete_event_count": 1,
        "paste_event_count": 0,
        "run_count": 2,
        "failed_run_count": 1,
        "active_idle_count": 1,
        "active_idle_total_duration_ms": 3000,
        "page_away_duration_ms": 0,
        "failure_edit_success_chain_count": 1,
        "error_type_change_count": 0,
    }


def test_valid_observation_subtracts_page_away_and_execution_intervals(
    signal_dictionary: dict[str, object],
) -> None:
    events = [
        event(1, "idle", 0, 5000),
        event(2, "page_away", 1000, 4000),
        event(
            3,
            "code_execution",
            4500,
            5500,
            execution_result="success",
        ),
    ]

    features = extract_features(events, signal_dictionary)

    assert features["valid_observation_duration_ms"] == 1500
    assert features["page_away_duration_ms"] == 3000
    assert features["active_idle_count"] == 0
    assert features["active_idle_total_duration_ms"] == 0


def test_active_idle_threshold_is_inclusive(
    signal_dictionary: dict[str, object],
) -> None:
    features = extract_features(
        [
            event(1, "idle", 0, 1999),
            event(2, "idle", 2000, 4000),
        ],
        signal_dictionary,
    )

    assert features["active_idle_count"] == 1
    assert features["active_idle_total_duration_ms"] == 2000


def test_paste_count_uses_explicit_and_writing_paste_signals(
    signal_dictionary: dict[str, object],
) -> None:
    features = extract_features(
        [
            event(1, "code_paste", 50, 50),
            event(2, "code_writing", 0, 100, had_paste=True),
            event(
                3,
                "code_writing",
                100,
                200,
                had_paste=True,
                cell_id="cell-2",
                cell_index=1,
            ),
            event(4, "code_writing", 200, 300, had_paste=False),
        ],
        signal_dictionary,
    )

    assert features["edit_event_count"] == 4
    assert features["paste_event_count"] == 2


def test_recovery_chain_matches_document_and_cell_aliases(
    signal_dictionary: dict[str, object],
) -> None:
    events = [
        event(
            1,
            "code_execution",
            0,
            10,
            execution_result="failure",
            error_type="NameError",
        ),
        event(
            2,
            "code_writing",
            10,
            20,
            notebook_id=None,
            cell_id=None,
        ),
        event(
            3,
            "code_execution",
            20,
            30,
            execution_result="success",
            notebook_id=None,
            cell_id=None,
        ),
    ]

    features = extract_features(events, signal_dictionary)

    assert features["failure_edit_success_chain_count"] == 1


def test_recovery_chain_does_not_cross_document_or_cell(
    signal_dictionary: dict[str, object],
) -> None:
    events = [
        event(
            1,
            "code_execution",
            0,
            10,
            execution_result="failure",
            error_type="NameError",
        ),
        event(2, "code_writing", 10, 20, cell_id="cell-2", cell_index=1),
        event(
            3,
            "code_execution",
            20,
            30,
            execution_result="success",
            cell_id="cell-2",
            cell_index=1,
        ),
        event(
            4,
            "code_writing",
            30,
            40,
            notebook_id="other-notebook",
            notebook_path="other.ipynb",
        ),
        event(
            5,
            "code_execution",
            40,
            50,
            execution_result="success",
            notebook_id="other-notebook",
            notebook_path="other.ipynb",
        ),
    ]

    features = extract_features(events, signal_dictionary)

    assert features["failure_edit_success_chain_count"] == 0


@pytest.mark.parametrize(
    "conflicting_context",
    [
        {"notebook_id": "other-notebook"},
        {"cell_id": "other-cell"},
    ],
)
def test_recovery_chain_rejects_conflicting_strong_identifiers(
    signal_dictionary: dict[str, object],
    conflicting_context: dict[str, object],
) -> None:
    events = [
        event(
            1,
            "code_execution",
            0,
            10,
            execution_result="failure",
            error_type="NameError",
        ),
        event(2, "code_writing", 10, 20, **conflicting_context),
        event(
            3,
            "code_execution",
            20,
            30,
            execution_result="success",
            **conflicting_context,
        ),
    ]

    features = extract_features(events, signal_dictionary)

    assert features["failure_edit_success_chain_count"] == 0


def test_each_success_closes_at_most_the_latest_recovery_chain(
    signal_dictionary: dict[str, object],
) -> None:
    events = [
        event(
            1,
            "code_execution",
            0,
            10,
            execution_result="failure",
            error_type="NameError",
        ),
        event(2, "code_writing", 10, 20),
        event(
            3,
            "code_execution",
            20,
            30,
            execution_result="failure",
            error_type="TypeError",
        ),
        event(4, "code_writing", 30, 40),
        event(
            5,
            "code_execution",
            40,
            50,
            execution_result="success",
        ),
        event(
            6,
            "code_execution",
            50,
            60,
            execution_result="success",
        ),
    ]

    features = extract_features(events, signal_dictionary)

    assert features["failure_edit_success_chain_count"] == 1


def test_python_file_is_a_stable_document_level_recovery_unit(
    signal_dictionary: dict[str, object],
) -> None:
    common = {
        "document_type": "python_file",
        "file_path": "synthetic.py",
        "notebook_id": None,
        "notebook_path": None,
        "cell_id": None,
        "cell_index": None,
    }
    events = [
        event(
            1,
            "code_execution",
            0,
            10,
            **common,
            execution_result="failure",
            error_type="NameError",
        ),
        event(2, "code_writing", 10, 20, **common),
        event(
            3,
            "code_execution",
            20,
            30,
            **common,
            execution_result="success",
        ),
    ]

    assert (
        extract_features(events, signal_dictionary)[
            "failure_edit_success_chain_count"
        ]
        == 1
    )


def test_error_type_changes_are_per_unit_and_success_resets_sequence(
    signal_dictionary: dict[str, object],
) -> None:
    events = [
        event(
            1,
            "code_execution",
            0,
            10,
            execution_result="failure",
            error_type="NameError",
        ),
        event(
            2,
            "code_execution",
            10,
            20,
            execution_result="failure",
            error_type="NameError",
        ),
        event(
            3,
            "code_execution",
            20,
            30,
            execution_result="failure",
            error_type="SyntaxError",
        ),
        event(
            4,
            "code_execution",
            30,
            40,
            execution_result="failure",
            error_type="TypeError",
            cell_id="cell-2",
            cell_index=1,
        ),
        event(
            5,
            "code_execution",
            40,
            50,
            execution_result="success",
        ),
        event(
            6,
            "code_execution",
            50,
            60,
            execution_result="failure",
            error_type="TypeError",
        ),
    ]

    features = extract_features(events, signal_dictionary)

    assert features["error_type_change_count"] == 1


@pytest.mark.parametrize(
    "conflicting_context",
    [
        {"notebook_id": "other-notebook"},
        {"cell_id": "other-cell"},
    ],
)
def test_error_change_rejects_conflicting_strong_identifiers(
    signal_dictionary: dict[str, object],
    conflicting_context: dict[str, object],
) -> None:
    events = [
        event(
            1,
            "code_execution",
            0,
            10,
            execution_result="failure",
            error_type="NameError",
        ),
        event(
            2,
            "code_execution",
            10,
            20,
            execution_result="failure",
            error_type="SyntaxError",
            **conflicting_context,
        ),
    ]

    features = extract_features(events, signal_dictionary)

    assert features["error_type_change_count"] == 0


@pytest.mark.parametrize(
    "conflicting_context",
    [
        {"notebook_id": "other-notebook"},
        {"cell_id": "other-cell"},
    ],
)
def test_paste_dedup_rejects_conflicting_strong_identifiers(
    signal_dictionary: dict[str, object],
    conflicting_context: dict[str, object],
) -> None:
    events = [
        event(1, "code_paste", 50, 50),
        event(
            2,
            "code_writing",
            0,
            100,
            had_paste=True,
            **conflicting_context,
        ),
    ]

    features = extract_features(events, signal_dictionary)

    assert features["paste_event_count"] == 2


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.__setitem__("started_at", "not-a-time"),
        lambda value: value.__setitem__(
            "started_at", "2026-07-28T09:00:00"
        ),
        lambda value: value.__setitem__(
            "ended_at", "2026-07-28T08:59:59+08:00"
        ),
        lambda value: value.__setitem__("duration_ms", True),
    ],
)
def test_invalid_time_values_are_explicitly_not_computable(
    signal_dictionary: dict[str, object],
    mutation,
) -> None:
    events = synthetic_recovery_sequence()
    mutation(events[0])

    features = extract_features(events, signal_dictionary)

    assert features["valid_observation_duration_ms"] is None
    assert features["active_idle_count"] is None
    assert features["active_idle_total_duration_ms"] is None
    assert features["page_away_duration_ms"] is None
    assert features["edit_event_count"] == 3


def test_split_idle_fragments_are_qualified_independently(
    signal_dictionary: dict[str, object],
) -> None:
    features = extract_features(
        [
            event(1, "idle", 0, 5999),
            event(2, "page_away", 1999, 3999),
        ],
        signal_dictionary,
    )

    assert features["active_idle_count"] == 1
    assert features["active_idle_total_duration_ms"] == 2000


def test_subthreshold_idle_fragments_are_not_summed_into_an_episode(
    signal_dictionary: dict[str, object],
) -> None:
    features = extract_features(
        [
            event(1, "idle", 0, 5000),
            event(2, "page_away", 1000, 4000),
        ],
        signal_dictionary,
    )

    assert features["active_idle_count"] == 0
    assert features["active_idle_total_duration_ms"] == 0


def test_overlapping_idle_records_form_one_continuous_episode(
    signal_dictionary: dict[str, object],
) -> None:
    features = extract_features(
        [
            event(1, "idle", 0, 2000),
            event(2, "idle", 1000, 3000),
        ],
        signal_dictionary,
    )

    assert features["active_idle_count"] == 1
    assert features["active_idle_total_duration_ms"] == 3000


def test_adjacent_idle_records_form_one_continuous_episode(
    signal_dictionary: dict[str, object],
) -> None:
    features = extract_features(
        [
            event(1, "idle", 0, 2000),
            event(2, "idle", 2000, 4000),
        ],
        signal_dictionary,
    )

    assert features["active_idle_count"] == 1
    assert features["active_idle_total_duration_ms"] == 4000


def test_adjacent_subthreshold_idle_records_qualify_after_union(
    signal_dictionary: dict[str, object],
) -> None:
    features = extract_features(
        [
            event(1, "idle", 0, 1500),
            event(2, "idle", 1500, 3000),
        ],
        signal_dictionary,
    )

    assert features["active_idle_count"] == 1
    assert features["active_idle_total_duration_ms"] == 3000


def test_exclusion_splits_the_merged_idle_union_before_thresholding(
    signal_dictionary: dict[str, object],
) -> None:
    features = extract_features(
        [
            event(1, "idle", 0, 1500),
            event(2, "idle", 1500, 5000),
            event(3, "page_away", 1000, 3000),
        ],
        signal_dictionary,
    )

    assert features["active_idle_count"] == 1
    assert features["active_idle_total_duration_ms"] == 2000


def test_timestamp_duration_mismatch_makes_time_features_missing(
    signal_dictionary: dict[str, object],
) -> None:
    idle = event(1, "idle", 0, 5000)
    idle["duration_ms"] = 1

    features = extract_features([idle], signal_dictionary)

    assert features["valid_observation_duration_ms"] is None
    assert features["active_idle_count"] is None
    assert features["active_idle_total_duration_ms"] is None
    assert features["page_away_duration_ms"] is None


def test_equivalent_timezone_offsets_have_exact_duration(
    signal_dictionary: dict[str, object],
) -> None:
    idle = event(1, "idle", 0, 2000)
    idle["started_at"] = "2026-07-28T09:00:00.000+08:00"
    idle["ended_at"] = "2026-07-28T01:00:02.000+00:00"

    features = extract_features([idle], signal_dictionary)

    assert features["valid_observation_duration_ms"] == 2000
    assert features["active_idle_count"] == 1


def test_duration_validation_accepts_two_millisecond_collector_drift(
    signal_dictionary: dict[str, object],
) -> None:
    idle = event(1, "idle", 0, 2000)
    idle["duration_ms"] = 1998

    features = extract_features([idle], signal_dictionary)

    assert features["valid_observation_duration_ms"] == 2000
    assert features["active_idle_count"] == 1
    assert features["active_idle_total_duration_ms"] == 2000


def test_duration_validation_rejects_three_millisecond_contradiction(
    signal_dictionary: dict[str, object],
) -> None:
    idle = event(1, "idle", 0, 2000)
    idle["duration_ms"] = 1997

    features = extract_features([idle], signal_dictionary)

    assert features["valid_observation_duration_ms"] is None
    assert features["active_idle_count"] is None


@pytest.mark.parametrize(
    "sequences",
    [
        [1, 1],
        [1, 3],
        [0, 1],
        [True, 2],
    ],
)
def test_invalid_session_sequence_makes_every_feature_explicitly_missing(
    signal_dictionary: dict[str, object],
    sequences: list[object],
) -> None:
    events = [
        event(1, "code_writing", 0, 10),
        event(2, "idle", 10, 2010),
    ]
    for value, sequence in zip(events, sequences):
        value["session_seq"] = sequence

    assert extract_features(events, signal_dictionary) == {
        name: None for name in FEATURE_NAMES
    }


def test_missing_context_or_execution_values_do_not_understate_features(
    signal_dictionary: dict[str, object],
) -> None:
    events = [
        event(
            1,
            "code_execution",
            0,
            10,
            execution_result="failure",
            error_type="",
            notebook_id=None,
            notebook_path=None,
            cell_id=None,
            cell_index=None,
        ),
        event(2, "code_writing", 10, 20),
    ]

    features = extract_features(events, signal_dictionary)

    assert features["run_count"] == 1
    assert features["failed_run_count"] == 1
    assert features["failure_edit_success_chain_count"] is None
    assert features["error_type_change_count"] is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.__setitem__("version", "future-v2"),
        lambda value: value.__setitem__("active_idle_threshold_ms", 1999),
        lambda value: value.__setitem__(
            "verification_after_idle_window_ms", 119999
        ),
        lambda value: value["signals"]["run_count"].__setitem__(
            "source_segment_types", ["idle"]
        ),
    ],
)
def test_invalid_dictionary_contract_returns_all_missing(
    signal_dictionary: dict[str, object],
    mutate,
) -> None:
    mutate(signal_dictionary)

    assert extract_features([], signal_dictionary) == {
        name: None for name in FEATURE_NAMES
    }


def test_packaged_dictionary_declares_exact_signal_contract(
    signal_dictionary: dict[str, object],
) -> None:
    assert signal_dictionary["version"] == "pilot-v1"
    assert signal_dictionary["active_idle_threshold_ms"] == 2000
    assert signal_dictionary["verification_after_idle_window_ms"] == 120000
    assert set(signal_dictionary["signals"]) == EXPECTED_FEATURE_NAMES
    assert len(sha256_json(signal_dictionary)) == 64
    for definition in signal_dictionary["signals"].values():
        assert set(definition) == {
            "unit",
            "scope",
            "missing_value_meaning",
            "source_segment_types",
        }


def test_coverage_uses_minimum_observation_keys_as_required_signals(
    signal_dictionary: dict[str, object],
) -> None:
    features = extract_features(
        synthetic_recovery_sequence(),
        signal_dictionary,
    )
    target = dimension({"edit_event_count": 1, "run_count": 1})

    result = evaluate_coverage(target, features)

    assert result == {
        "status": "sufficient_for_analysis",
        "missing_required_signals": [],
        "observation_opportunities": 1,
        "reason_code": "minimum_observation_met",
        "reason": "已达到最低观察要求",
    }


def test_coverage_thresholds_are_inclusive() -> None:
    result = evaluate_coverage(
        dimension(
            {
                "valid_observation_duration_ms": 10_000,
                "edit_event_count": 3,
                "run_count": 2,
            }
        ),
        {
            "valid_observation_duration_ms": 10_000,
            "edit_event_count": 3,
            "run_count": 2,
        },
    )

    assert result["status"] == "sufficient_for_analysis"


def test_coverage_below_duration_minimum_is_insufficient() -> None:
    result = evaluate_coverage(
        dimension({"valid_observation_duration_ms": 300_000}),
        {"valid_observation_duration_ms": 10_000},
    )

    assert result == {
        "status": "insufficient_evidence",
        "missing_required_signals": [],
        "observation_opportunities": 0,
        "reason_code": "minimum_observation_not_met",
        "reason": "有效观察时长不足",
    }


@pytest.mark.parametrize(
    "features",
    [
        {},
        {"edit_event_count": None},
        {"edit_event_count": True},
        {"edit_event_count": -1},
        {"edit_event_count": float("inf")},
    ],
)
def test_coverage_missing_or_invalid_required_signal_is_not_computable(
    features: dict[str, object],
) -> None:
    result = evaluate_coverage(
        dimension({"edit_event_count": 1}),
        features,
    )

    assert result == {
        "status": "not_computable",
        "missing_required_signals": ["edit_event_count"],
        "observation_opportunities": 0,
        "reason_code": "required_signal_not_computable",
        "reason": "所需信号缺失或无效",
    }


def test_coverage_reason_is_deterministic_for_multiple_unmet_minimums() -> None:
    first = evaluate_coverage(
        dimension({"run_count": 2, "edit_event_count": 3}),
        {"run_count": 1, "edit_event_count": 2},
    )
    second = evaluate_coverage(
        dimension({"edit_event_count": 3, "run_count": 2}),
        {"run_count": 1, "edit_event_count": 2},
    )

    assert first == second
    assert first["status"] == "insufficient_evidence"
    assert first["reason"] == "编辑事件数量不足"


@pytest.mark.parametrize(
    ("code", "opportunity_signal", "opportunities"),
    [
        ("DEBUG_CHAIN", "failed_run_count", 2),
        ("REPEATED_RUN_FAILURES", "run_count", 3),
        ("PAUSE_WITHOUT_VALIDATION", "active_idle_count", 4),
        ("REPEATED_EDITING", "edit_event_count", 5),
    ],
)
def test_builtin_dimension_uses_system_owned_opportunity_signal(
    code: str,
    opportunity_signal: str,
    opportunities: int,
) -> None:
    target = dimension({"edit_event_count": 1})
    target["code"] = code

    result = evaluate_coverage(
        target,
        {
            "edit_event_count": 100,
            opportunity_signal: opportunities,
        },
    )

    assert result["status"] == "sufficient_for_analysis"
    assert result["observation_opportunities"] == opportunities


def test_debug_opportunities_do_not_use_abundant_unrelated_counts() -> None:
    result = evaluate_coverage(
        dimension({"edit_event_count": 1, "run_count": 1}),
        {
            "edit_event_count": 100,
            "run_count": 2,
            "failed_run_count": 0,
        },
    )

    assert result["status"] == "sufficient_for_analysis"
    assert result["observation_opportunities"] == 0


def test_custom_dimension_has_conservative_zero_opportunities() -> None:
    target = dimension({"edit_event_count": 1})
    target["code"] = "CUSTOM_1234ABCD"

    result = evaluate_coverage(target, {"edit_event_count": 100})

    assert result["status"] == "sufficient_for_analysis"
    assert result["observation_opportunities"] == 0


@pytest.mark.parametrize(
    ("target", "missing"),
    [
        (dimension(), ["minimum_observation"]),
        (dimension({}), ["minimum_observation"]),
        (
            dimension({"unknown_count": 1}),
            ["unknown_count"],
        ),
    ],
)
def test_absent_empty_or_unsupported_minimum_fails_closed(
    target: dict[str, object],
    missing: list[str],
) -> None:
    result = evaluate_coverage(
        target,
        {"unknown_count": 1},
    )

    assert result == {
        "status": "not_computable",
        "missing_required_signals": missing,
        "observation_opportunities": 0,
        "reason_code": "required_signal_not_computable",
        "reason": "所需信号缺失或无效",
    }
