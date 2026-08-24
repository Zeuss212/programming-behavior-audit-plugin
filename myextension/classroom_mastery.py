"""Local, non-executable mastery checks for classroom-plan evidence rules."""

from __future__ import annotations

import ast
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_RULE_KINDS = frozenset(
    {
        "successful_execution",
        "dict_literal_assignment",
        "dict_key_value_pairs",
        "dict_subscript_access",
        "dict_get_with_default",
        "print_call",
        "input_call",
    }
)

_GAPS = {
    "successful_execution": "未观察到代码成功运行。",
    "dict_literal_assignment": "未观察到字典创建或初始化。",
    "dict_key_value_pairs": "未观察到包含至少两项的字典键值对。",
    "dict_subscript_access": "未观察到对已创建字典的方括号访问。",
    "dict_get_with_default": "未观察到字典 get() 的默认值处理。",
    "print_call": "未观察到 print() 结果输出。",
    "input_call": "未观察到 input() 输入处理。",
}
_MAX_EVIDENCE_REFS = 10


@dataclass(frozen=True)
class _EvidenceFeatures:
    successful_execution: bool
    dict_literal_assignment: bool
    dict_key_value_pairs: bool
    dict_subscript_access: bool
    dict_get_with_default: bool
    print_call: bool
    input_call: bool
    evidence_complete: bool
    supporting_sequences: Mapping[str, frozenset[int]]
    has_sequence_data: bool

    def supports(self, requirement: str) -> bool:
        return bool(getattr(self, requirement))


def evaluate_knowledge_points(
    profile: Mapping[str, object],
    detail: Mapping[str, object],
    evidence_refs: Sequence[str],
) -> list[dict[str, object]]:
    """Return teacher-safe mastery rows without returning local source text."""

    features = _extract_features(detail)
    references = list(dict.fromkeys(
        item for item in evidence_refs if isinstance(item, str) and item
    ))
    if not references:
        references = ["session#missing-evidence"]
    raw_points = profile.get("knowledge_points")
    if not isinstance(raw_points, list):
        return []

    rows: list[dict[str, object]] = []
    for index, raw_point in enumerate(raw_points, start=1):
        point = raw_point if isinstance(raw_point, Mapping) else {}
        point_id = point.get("id")
        name = point.get("name")
        rule = _rule_from(point.get("automatic_evaluation"))
        row_references = _supporting_references(rule, features, references)
        rows.append(
            _row_for(
                knowledge_point_id=point_id if isinstance(point_id, str) else f"KP_{index}",
                name=name if isinstance(name, str) and name else f"知识点 {index}",
                rule=rule,
                features=features,
                evidence_refs=row_references,
            )
        )
    return rows


def _rule_from(value: object) -> tuple[str, tuple[str, ...]] | None:
    if not isinstance(value, Mapping) or value.get("mode") != "all":
        return None
    summary = value.get("summary")
    requirements = value.get("requirements")
    if not isinstance(summary, str) or not summary.strip() or not isinstance(requirements, list):
        return None

    kinds: list[str] = []
    for requirement in requirements:
        if not isinstance(requirement, Mapping):
            return None
        kind = requirement.get("kind")
        if not isinstance(kind, str) or kind not in _RULE_KINDS or kind in kinds:
            return None
        kinds.append(kind)
    return (summary.strip(), tuple(kinds)) if kinds else None


def _row_for(
    *,
    knowledge_point_id: str,
    name: str,
    rule: tuple[str, tuple[str, ...]] | None,
    features: _EvidenceFeatures,
    evidence_refs: list[str],
) -> dict[str, object]:
    if rule is None:
        return _row(
            knowledge_point_id,
            name,
            "review_required",
            evidence_refs,
            "该知识点未配置可执行的自动判定规则。",
            "系统不能据此安全地自动确认掌握情况。",
            "请由教师补充复核结论，或重新生成包含自动判定规则的方案。",
        )
    if not features.evidence_complete:
        return _row(
            knowledge_point_id,
            name,
            "review_required",
            evidence_refs,
            "本地代码或运行证据不完整，未进行自动结论。",
            "缺少可解析代码或可用运行记录。",
            "请检查本地监控连接后重新提交，或由教师复核。",
        )

    summary, requirements = rule
    missing = [requirement for requirement in requirements if not features.supports(requirement)]
    if not missing:
        return _row(
            knowledge_point_id,
            name,
            "mastered",
            evidence_refs,
            f"本地代码结构与成功运行记录满足自动判定条件：{summary}",
            "未发现当前规则要求的缺失证据。",
            "可结合课堂追问进行抽查，教师复核可覆盖此自动结论。",
        )
    if not features.successful_execution:
        return _row(
            knowledge_point_id,
            name,
            "not_demonstrated",
            evidence_refs,
            "未观察到满足本知识点规则的成功运行。",
            "；".join(_GAPS[requirement] for requirement in missing),
            "请先完成并成功运行相关代码，再提交本节简报。",
        )
    return _row(
        knowledge_point_id,
        name,
        "partial",
        evidence_refs,
        f"已成功运行，但仅满足 {len(requirements) - len(missing)}/{len(requirements)} 项自动判定条件。",
        "；".join(_GAPS[requirement] for requirement in missing),
        "请根据缺失条件补充代码并再次运行，或由教师结合过程证据复核。",
    )


def _row(
    knowledge_point_id: str,
    name: str,
    status: str,
    evidence_refs: list[str],
    demonstrated: str,
    gap: str,
    teacher_suggestion: str,
) -> dict[str, object]:
    return {
        "knowledge_point_id": knowledge_point_id,
        "name": name,
        "status": status,
        "evidence_refs": evidence_refs,
        "demonstrated": demonstrated,
        "gap": gap,
        "teacher_suggestion": teacher_suggestion,
    }


def _supporting_references(
    rule: tuple[str, tuple[str, ...]] | None,
    features: _EvidenceFeatures,
    references: list[str],
) -> list[str]:
    if rule is None or not features.evidence_complete or not features.has_sequence_data:
        return references[:_MAX_EVIDENCE_REFS]
    supported_sequences = {
        sequence
        for requirement in rule[1]
        for sequence in features.supporting_sequences.get(requirement, frozenset())
    }
    selected = [
        reference
        for reference in references
        if _reference_sequence(reference) in supported_sequences
    ][:_MAX_EVIDENCE_REFS]
    return selected or ["session#missing-evidence"]


def _reference_sequence(reference: str) -> int | None:
    _separator, marker, raw_sequence = reference.rpartition("#event-")
    if not marker or not raw_sequence.isdigit():
        return None
    return int(raw_sequence)


def _extract_features(detail: Mapping[str, object]) -> _EvidenceFeatures:
    events = detail.get("behavior_events")
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        return _empty_features()
    event_rows = [event for event in events if isinstance(event, Mapping)]
    if len(event_rows) != len(events):
        return _empty_features()
    tree_rows: list[tuple[ast.Module, int | None]] = []
    has_sequence_data = any(_event_sequence(event) is not None for event in event_rows)
    for event in event_rows:
        source = event.get("cell_source")
        if not isinstance(source, str) or not source.strip():
            continue
        try:
            tree_rows.append((ast.parse(source), _event_sequence(event)))
        except SyntaxError:
            continue
    if not tree_rows:
        return _empty_features()

    supporting: dict[str, set[int]] = {kind: set() for kind in _RULE_KINDS}
    dictionary_names: set[str] = set()
    dictionary_literal_assignment = False
    dictionary_with_pairs = False
    for tree, sequence in tree_rows:
        for node in ast.walk(tree):
            value: ast.expr | None = None
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                value = node.value
                targets = node.targets
            elif isinstance(node, ast.AnnAssign):
                value = node.value
                targets = [node.target]
            if value is None or not _is_dictionary_constructor(value):
                continue
            names = {target.id for target in targets if isinstance(target, ast.Name)}
            if names:
                dictionary_literal_assignment = True
                dictionary_names.update(names)
                _record_support(supporting, "dict_literal_assignment", sequence)
            if _has_two_or_more_pairs(value):
                dictionary_with_pairs = True
                _record_support(supporting, "dict_key_value_pairs", sequence)

    subscript_access = False
    get_with_default = False
    print_call = False
    input_call = False
    assigned_keys: dict[str, set[str]] = {name: set() for name in dictionary_names}
    assigned_key_sequences: dict[str, set[int]] = {
        name: set() for name in dictionary_names
    }
    for tree, sequence in tree_rows:
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and _is_known_dictionary_name(
                node.value, dictionary_names
            ):
                subscript_access = True
                _record_support(supporting, "dict_subscript_access", sequence)
            for target in _assignment_targets(node):
                if isinstance(target, ast.Subscript) and _is_known_dictionary_name(
                    target.value, dictionary_names
                ):
                    assigned_keys[target.value.id].add(
                        ast.dump(target.slice, include_attributes=False)
                    )
                    if sequence is not None:
                        assigned_key_sequences[target.value.id].add(sequence)
                    if len(assigned_keys[target.value.id]) >= 2:
                        dictionary_with_pairs = True
                        supporting["dict_key_value_pairs"].update(
                            assigned_key_sequences[target.value.id]
                        )
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id == "print":
                        print_call = True
                        _record_support(supporting, "print_call", sequence)
                    if node.func.id == "input":
                        input_call = True
                        _record_support(supporting, "input_call", sequence)
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get"
                    and _is_known_dictionary_name(node.func.value, dictionary_names)
                    and len(node.args) >= 2
                ):
                    get_with_default = True
                    _record_support(supporting, "dict_get_with_default", sequence)

    successful_execution = False
    for event in event_rows:
        if (
            event.get("segment_type") == "code_execution"
            and event.get("execution_result") == "success"
            and not event.get("error_type")
            and not event.get("error_message")
        ):
            successful_execution = True
            _record_support(
                supporting,
                "successful_execution",
                _event_sequence(event),
            )
    return _EvidenceFeatures(
        successful_execution=successful_execution,
        dict_literal_assignment=dictionary_literal_assignment,
        dict_key_value_pairs=(
            dictionary_with_pairs or any(len(keys) >= 2 for keys in assigned_keys.values())
        ),
        dict_subscript_access=subscript_access,
        dict_get_with_default=get_with_default,
        print_call=print_call,
        input_call=input_call,
        evidence_complete=True,
        supporting_sequences={
            kind: frozenset(sequences) for kind, sequences in supporting.items()
        },
        has_sequence_data=has_sequence_data,
    )


def _empty_features() -> _EvidenceFeatures:
    return _EvidenceFeatures(
        successful_execution=False,
        dict_literal_assignment=False,
        dict_key_value_pairs=False,
        dict_subscript_access=False,
        dict_get_with_default=False,
        print_call=False,
        input_call=False,
        evidence_complete=False,
        supporting_sequences={kind: frozenset() for kind in _RULE_KINDS},
        has_sequence_data=False,
    )


def _event_sequence(event: Mapping[str, object]) -> int | None:
    sequence = event.get("session_seq")
    return sequence if isinstance(sequence, int) and not isinstance(sequence, bool) else None


def _record_support(
    supporting: dict[str, set[int]],
    requirement: str,
    sequence: int | None,
) -> None:
    if sequence is not None:
        supporting[requirement].add(sequence)


def _is_dictionary_constructor(value: ast.expr) -> bool:
    return isinstance(value, ast.Dict) or (
        isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "dict"
    )


def _has_two_or_more_pairs(value: ast.expr) -> bool:
    if isinstance(value, ast.Dict):
        return len(value.keys) >= 2
    if _is_dictionary_constructor(value):
        if len(value.keywords) >= 2:
            return True
        return len(value.args) == 1 and _is_pair_collection(value.args[0])
    return False


def _is_pair_collection(value: ast.expr) -> bool:
    if not isinstance(value, (ast.List, ast.Tuple, ast.Set)) or len(value.elts) < 2:
        return False
    return all(
        isinstance(item, (ast.List, ast.Tuple)) and len(item.elts) == 2
        for item in value.elts
    )


def _assignment_targets(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, ast.Assign):
        return node.targets
    if isinstance(node, ast.AnnAssign):
        return [node.target]
    return []


def _is_known_dictionary_name(value: ast.expr, dictionary_names: set[str]) -> bool:
    return isinstance(value, ast.Name) and value.id in dictionary_names
