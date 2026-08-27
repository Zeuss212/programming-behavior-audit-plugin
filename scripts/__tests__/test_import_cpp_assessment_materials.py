from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from scripts import import_cpp_assessment_materials as importer

ROOT = Path(__file__).resolve().parents[2]
MATERIALS = ROOT / "deploy" / "classroom" / "local-demo" / "materials"
SEQUENCE_CONFIG = MATERIALS / "sequence-list" / "import-config.json"
LINKED_CONFIG = MATERIALS / "linked-list" / "import-config.json"

SEQUENCE_LIVE_PREFLIGHT = {
    "status": "blocked",
    "accepted_editable_symbols": [
        "SeqArray::SeqArray",
        "SeqArray::~SeqArray",
        "SeqArray::insertElement",
        "SeqArray::deletemin",
        "SeqArray::deleteSame",
        "SeqArray::deleteSome",
        "SeqArray::print",
    ],
    "diagnostics": [
        {
            "line": 72,
            "column": 16,
            "code": "compiler_error",
            "message": "use of undeclared identifier 'value'",
        }
    ],
}
LINKED_LIVE_PREFLIGHT = {
    "status": "ready",
    "accepted_editable_symbols": ["MList::insertToTail", "MList::Reverse"],
    "diagnostics": [],
}


@pytest.fixture
def sequence_bundle(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setattr(
        importer,
        "_preflight_source",
        lambda _source, _symbols: deepcopy(SEQUENCE_LIVE_PREFLIGHT),
    )
    return importer.import_material_bundle(SEQUENCE_CONFIG)


@pytest.fixture
def linked_bundle(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setattr(
        importer,
        "_preflight_source",
        lambda _source, _symbols: deepcopy(LINKED_LIVE_PREFLIGHT),
    )
    return importer.import_material_bundle(LINKED_CONFIG)


def _by_id(items: list[dict[str, object]], item_id: str) -> dict[str, object]:
    return next(item for item in items if item["id"] == item_id)


def _copy_config_fixture(
    tmp_path: Path, config_path: Path
) -> tuple[Path, dict[str, object]]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for key in ("source", "tests"):
        source = config_path.parent / config[key]["path"]
        destination = tmp_path / config[key]["path"]
        destination.write_bytes(source.read_bytes())
    copied_config = tmp_path / "import-config.json"
    copied_config.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return copied_config, config


def _write_config(path: Path, config: dict[str, object]) -> None:
    path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def test_importer_parses_exactly_two_teacher_tests_per_exercise(
    sequence_bundle: dict[str, object], linked_bundle: dict[str, object]
) -> None:
    sequence_tests = sequence_bundle["assessment_tests"]
    linked_tests = linked_bundle["assessment_tests"]
    assert [(test["id"], test["kind"]) for test in sequence_tests] == [
        ("TEST_SEQU0001", "stdin_stdout"),
        ("TEST_SEQU0002", "stdin_stdout"),
    ]
    assert [(test["id"], test["kind"]) for test in linked_tests] == [
        ("TEST_LINK0001", "stdin_stdout"),
        ("TEST_LINK0002", "stdin_stdout"),
    ]
    assert [test["input"] for test in sequence_tests] == [
        "10 \n5 3  2  1  9  8  4    2   7  5  \n5\n2 5\n",
        "13\n1 3 5 3 4 5 6 3 2 4 2 4 2\n3\n1 6\n",
    ]
    assert [test["expected_stdout"] for test in sequence_tests] == [
        (
            "顺序表数据为:5 3 2 1 9 8 4 2 7 5 \n"
            "删除最小值后为:5 3 2 5 9 8 4 2 7 \n"
            "最小值:1\n"
            "删除相同值后为:3 2 9 8 4 2 7 \n"
            "删除指定范围数值后为:2 9 8 2 7\n"
        ),
        (
            "顺序表数据为:1 3 5 3 4 5 6 3 2 4 2 4 2 \n"
            "删除最小值后为:2 3 5 3 4 5 6 3 2 4 2 4 \n"
            "最小值:1\n"
            "删除相同值后为:2 5 4 5 6 2 4 2 4 \n"
            "删除指定范围数值后为:6\n"
        ),
    ]
    assert [test["input"] for test in linked_tests] == [
        "6\n1 2 3 4 5 6\n",
        "7\n12 3 14 89 12 54 123\n",
    ]
    assert [test["expected_stdout"] for test in linked_tests] == [
        "倒置前为：1 2 3 4 5 6 \n倒置后为：6 5 4 3 2 1\n",
        "倒置前为：12 3 14 89 12 54 123 \n倒置后为：123 54 12 89 14 3 12\n",
    ]
    assert all(test["source"] == "teacher" for test in sequence_tests + linked_tests)


def test_sequence_non_utf8_source_is_candidate_only_and_original_is_unchanged(
    sequence_bundle: dict[str, object], tmp_path: Path
) -> None:
    source_path = MATERIALS / "sequence-list" / "顺序表操作练习01.cpp"
    original = source_path.read_bytes()
    expected_candidate_hash = hashlib.sha256(
        original.decode("gb18030").encode("utf-8")
    ).hexdigest()

    starter = sequence_bundle["starter_source"]
    assert starter["detected_encoding"] == "gb18030"
    assert starter["utf8_candidate_sha256"] == expected_candidate_hash
    assert starter["utf8_confirmed"] is False
    assert source_path.read_bytes() == original
    assert not (tmp_path / "canonical.cpp").exists()


def test_sequence_preflight_reports_protected_value_error_without_rewriting_source(
    sequence_bundle: dict[str, object],
) -> None:
    source_path = MATERIALS / "sequence-list" / "顺序表操作练习01.cpp"
    original_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    preflight = sequence_bundle["starter_source"]["preflight"]

    assert preflight["status"] == "blocked"
    assert any(
        diagnostic["line"] == 72
        and diagnostic["column"] == 0
        and diagnostic["code"] == "undeclared_identifier"
        and "value" in diagnostic["message"]
        for diagnostic in preflight["diagnostics"]
    )
    assert any(
        issue["code"] == "starter_source_protected_compile_error"
        for issue in sequence_bundle["issues"]
    )
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == original_hash


def test_linked_preflight_accepts_configured_missing_student_implementations(
    linked_bundle: dict[str, object],
) -> None:
    preflight = linked_bundle["starter_source"]["preflight"]
    assert preflight["status"] == "ready"
    assert preflight["diagnostics"] == []
    assert preflight["accepted_editable_symbols"] == [
        "MList::insertToTail",
        "MList::Reverse",
    ]


def test_linked_requirements_expose_real_responsibility_mismatches(
    linked_bundle: dict[str, object],
) -> None:
    requirements = linked_bundle["requirements"]
    assert (
        _by_id(requirements, "REQ_LINK_TAIL_INSERT")["student_responsibility"] is True
    )
    assert _by_id(requirements, "REQ_LINK_REVERSE")["student_responsibility"] is True
    assert _by_id(requirements, "REQ_LINK_TRAVERSAL")["student_responsibility"] is False
    assert _by_id(requirements, "REQ_LINK_DELETE")["student_responsibility"] is False

    issues = linked_bundle["issues"]
    assert any(
        issue["code"] == "teacher_dimension_not_student_responsibility"
        and issue["requirement_id"] == "REQ_LINK_TRAVERSAL"
        for issue in issues
    )
    assert any(
        issue["code"] == "teacher_dimension_outside_task"
        and issue["requirement_id"] == "REQ_LINK_DELETE"
        for issue in issues
    )
    assert any(
        issue["code"] == "required_student_dimension_missing"
        and issue["requirement_id"] == "REQ_LINK_REVERSE"
        for issue in issues
    )


def test_sequence_space_release_requires_unavailable_detector(
    sequence_bundle: dict[str, object],
) -> None:
    requirement = _by_id(sequence_bundle["requirements"], "REQ_SEQ_SPACE_RELEASE")
    detector = _by_id(sequence_bundle["detector_profiles"], "address_undefined_leak_v1")
    assert requirement["detector_profile_ids"] == ["address_undefined_leak_v1"]
    assert detector["available"] is False
    assert any(
        issue["code"] == "detector_profile_unavailable"
        and issue["requirement_id"] == "REQ_SEQ_SPACE_RELEASE"
        for issue in sequence_bundle["issues"]
    )


def test_importer_warns_about_boundary_gaps_without_generating_tests(
    sequence_bundle: dict[str, object], linked_bundle: dict[str, object]
) -> None:
    for bundle in (sequence_bundle, linked_bundle):
        assert any(
            issue["code"] == "boundary_coverage_incomplete"
            and issue["severity"] == "warning"
            for issue in bundle["issues"]
        )
        assert len(bundle["assessment_tests"]) == 2
        assert all(test["source"] == "teacher" for test in bundle["assessment_tests"])


def test_importer_rejects_hash_drift(tmp_path: Path) -> None:
    config_path, config = _copy_config_fixture(tmp_path, LINKED_CONFIG)
    source_path = tmp_path / config["source"]["path"]
    source_path.write_bytes(source_path.read_bytes() + b"\n")

    with pytest.raises(importer.MaterialImportError, match="hash drift"):
        importer.import_material_bundle(config_path)


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="symlinks unavailable")
def test_importer_rejects_symlinked_input(tmp_path: Path) -> None:
    config_path, config = _copy_config_fixture(tmp_path, LINKED_CONFIG)
    source_path = tmp_path / config["source"]["path"]
    real_source = tmp_path / "real.cpp"
    source_path.rename(real_source)
    source_path.symlink_to(real_source)

    with pytest.raises(importer.MaterialImportError, match="symlink"):
        importer.import_material_bundle(config_path)


def test_importer_rejects_input_reached_through_symlinked_parent(
    tmp_path: Path,
) -> None:
    real_directory = tmp_path / "real"
    real_directory.mkdir()
    config_path, _config = _copy_config_fixture(real_directory, LINKED_CONFIG)
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(real_directory, target_is_directory=True)

    with pytest.raises(importer.MaterialImportError, match="symlink"):
        importer.import_material_bundle(linked_directory / config_path.name)


def test_importer_rejects_input_over_256_kib(tmp_path: Path) -> None:
    config_path, config = _copy_config_fixture(tmp_path, LINKED_CONFIG)
    source_path = tmp_path / config["source"]["path"]
    source_path.write_bytes(b"x" * (256 * 1024 + 1))

    with pytest.raises(importer.MaterialImportError, match="256 KiB"):
        importer.import_material_bundle(config_path)


@pytest.mark.parametrize(
    "unsafe_path", ["../source.cpp", "/tmp/source.cpp", "sub/source.cpp"]
)
def test_importer_rejects_path_traversal(tmp_path: Path, unsafe_path: str) -> None:
    config_path, config = _copy_config_fixture(tmp_path, LINKED_CONFIG)
    config["source"]["path"] = unsafe_path
    _write_config(config_path, config)

    with pytest.raises(importer.MaterialImportError, match="safe basename"):
        importer.import_material_bundle(config_path)


def test_importer_rejects_unexpected_source_encoding(tmp_path: Path) -> None:
    config_path, config = _copy_config_fixture(tmp_path, SEQUENCE_CONFIG)
    config["source"]["encoding"] = "utf-8"
    _write_config(config_path, config)

    with pytest.raises(importer.MaterialImportError, match="encoding"):
        importer.import_material_bundle(config_path)


@pytest.mark.parametrize("retained_blocks", [1, 3])
def test_importer_rejects_more_or_fewer_than_two_tests(
    tmp_path: Path, retained_blocks: int
) -> None:
    config_path, config = _copy_config_fixture(tmp_path, LINKED_CONFIG)
    tests_path = tmp_path / config["tests"]["path"]
    text = tests_path.read_text(encoding="utf-8")
    first, second = text.split("测试用例2：", maxsplit=1)
    if retained_blocks == 1:
        text = first + "3. 考查的点（评价的维度）：\n链表的插入操作\n"
    else:
        text = (
            first
            + "测试用例2："
            + second.replace(
                "3. 考查的点（评价的维度）：",
                "测试用例3：\n输入：\n1\n9\n期望输出：\n倒置前为：9 \n倒置后为：9\n\n3. 考查的点（评价的维度）：",
            )
        )
    tests_path.write_text(text, encoding="utf-8")
    config["tests"]["sha256"] = hashlib.sha256(tests_path.read_bytes()).hexdigest()
    _write_config(config_path, config)

    with pytest.raises(importer.MaterialImportError, match="exactly two"):
        importer.import_material_bundle(config_path)


def test_importer_rejects_unrecognized_txt_heading(tmp_path: Path) -> None:
    config_path, config = _copy_config_fixture(tmp_path, LINKED_CONFIG)
    tests_path = tmp_path / config["tests"]["path"]
    text = tests_path.read_text(encoding="utf-8").replace("期望输出：", "标准输出：", 1)
    tests_path.write_text(text, encoding="utf-8")
    config["tests"]["sha256"] = hashlib.sha256(tests_path.read_bytes()).hexdigest()
    _write_config(config_path, config)

    with pytest.raises(importer.MaterialImportError, match="heading"):
        importer.import_material_bundle(config_path)


def test_importer_rejects_section_heading_drift(tmp_path: Path) -> None:
    config_path, config = _copy_config_fixture(tmp_path, LINKED_CONFIG)
    tests_path = tmp_path / config["tests"]["path"]
    text = tests_path.read_text(encoding="utf-8").replace(
        "2. 测试用例 （能够为每个测试用例指定分值）",
        "2. 测试数据",
        1,
    )
    tests_path.write_text(text, encoding="utf-8")
    config["tests"]["sha256"] = hashlib.sha256(tests_path.read_bytes()).hexdigest()
    _write_config(config_path, config)

    with pytest.raises(importer.MaterialImportError, match="heading"):
        importer.import_material_bundle(config_path)


def test_importer_rejects_moved_test_section_heading(tmp_path: Path) -> None:
    config_path, config = _copy_config_fixture(tmp_path, LINKED_CONFIG)
    tests_path = tmp_path / config["tests"]["path"]
    text = tests_path.read_text(encoding="utf-8")
    section = "2. 测试用例 （能够为每个测试用例指定分值）"
    text = section + "\n" + text.replace(section, "", 1)
    tests_path.write_text(text, encoding="utf-8")
    config["tests"]["sha256"] = hashlib.sha256(tests_path.read_bytes()).hexdigest()
    _write_config(config_path, config)

    with pytest.raises(importer.MaterialImportError, match="heading"):
        importer.import_material_bundle(config_path)


def test_importer_rejects_unknown_heading_inside_expected_output(
    tmp_path: Path,
) -> None:
    config_path, config = _copy_config_fixture(tmp_path, LINKED_CONFIG)
    tests_path = tmp_path / config["tests"]["path"]
    text = tests_path.read_text(encoding="utf-8").replace(
        "倒置前为：1 2 3 4 5 6 ",
        "备注：\n倒置前为：1 2 3 4 5 6 ",
        1,
    )
    tests_path.write_text(text, encoding="utf-8")
    config["tests"]["sha256"] = hashlib.sha256(tests_path.read_bytes()).hexdigest()
    _write_config(config_path, config)

    with pytest.raises(importer.MaterialImportError, match="heading"):
        importer.import_material_bundle(config_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("artifact_id", {"unexpected": "object"}),
        ("source_encoding", {"unexpected": "object"}),
    ],
)
def test_importer_rejects_invalid_output_facing_config_values(
    tmp_path: Path, field: str, value: object
) -> None:
    config_path, config = _copy_config_fixture(tmp_path, LINKED_CONFIG)
    if field == "artifact_id":
        config["source"][field] = value
    elif field == "source_encoding":
        config["source"]["encoding"] = value
    else:
        config[field] = value
    _write_config(config_path, config)

    with pytest.raises(importer.MaterialImportError, match=field):
        importer.import_material_bundle(config_path)


def test_importer_rejects_unknown_numbered_heading_inside_output(
    tmp_path: Path,
) -> None:
    config_path, config = _copy_config_fixture(tmp_path, LINKED_CONFIG)
    tests_path = tmp_path / config["tests"]["path"]
    text = tests_path.read_text(encoding="utf-8").replace(
        "倒置前为：1 2 3 4 5 6 ",
        "4. 评分说明\n倒置前为：1 2 3 4 5 6 ",
        1,
    )
    tests_path.write_text(text, encoding="utf-8")
    config["tests"]["sha256"] = hashlib.sha256(tests_path.read_bytes()).hexdigest()
    _write_config(config_path, config)

    with pytest.raises(importer.MaterialImportError, match="heading"):
        importer.import_material_bundle(config_path)


def test_importer_rejects_compact_numbered_heading_inside_output(
    tmp_path: Path,
) -> None:
    config_path, config = _copy_config_fixture(tmp_path, LINKED_CONFIG)
    tests_path = tmp_path / config["tests"]["path"]
    text = tests_path.read_text(encoding="utf-8").replace(
        "倒置前为：1 2 3 4 5 6 ",
        "4.评分说明\n倒置前为：1 2 3 4 5 6 ",
        1,
    )
    tests_path.write_text(text, encoding="utf-8")
    config["tests"]["sha256"] = hashlib.sha256(tests_path.read_bytes()).hexdigest()
    _write_config(config_path, config)

    with pytest.raises(importer.MaterialImportError, match="heading"):
        importer.import_material_bundle(config_path)


def test_compiler_is_allowlisted_bounded_and_diagnostics_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(
        importer.shutil,
        "which",
        lambda name: f"/tool/{name}" if name == "clang++" else None,
    )

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        probe_path = argv[-1]
        return subprocess.CompletedProcess(
            argv,
            1,
            "",
            f"{probe_path}:72:16: error: use of undeclared identifier 'value' "
            "(see /private/host/include/header.hpp)\n",
        )

    monkeypatch.setattr(importer.subprocess, "run", fake_run)
    bundle = importer.import_material_bundle(SEQUENCE_CONFIG)

    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv[:6] == [
        "/tool/clang++",
        "-std=c++17",
        "-fsyntax-only",
        "-Wall",
        "-Wextra",
        "-Wpedantic",
    ]
    assert len(argv) == 7
    assert Path(argv[-1]).name == "probe.cpp"
    assert kwargs["timeout"] == 10
    assert kwargs["shell"] is False
    diagnostic = bundle["starter_source"]["preflight"]["diagnostics"][0]
    assert diagnostic == {
        "line": 72,
        "column": 0,
        "code": "undeclared_identifier",
        "message": "protected main references undeclared identifier value",
    }
    raw_diagnostic = importer._sanitize_diagnostics(
        "/private/host/probe.cpp:72:16: error: use of undeclared identifier "
        "'value' (see /private/host/include/header.hpp)\n"
    )[0]
    assert raw_diagnostic["message"] == (
        "use of undeclared identifier 'value' (see <path>)"
    )
    assert "/tool/clang++" not in json.dumps(bundle, ensure_ascii=False)
    assert "/private/" not in json.dumps(bundle, ensure_ascii=False)


def test_clang_and_gpp_diagnostics_seal_to_same_semantic_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def import_with(compiler_name: str, diagnostic_message: str) -> dict[str, object]:
        monkeypatch.setattr(
            importer.shutil,
            "which",
            lambda name: f"/tool/{compiler_name}" if name == compiler_name else None,
        )

        def fake_run(
            argv: list[str], **_kwargs: object
        ) -> subprocess.CompletedProcess[str]:
            probe_path = argv[-1]
            return subprocess.CompletedProcess(
                argv,
                1,
                "",
                f"{probe_path}:72:16: error: {diagnostic_message}\n",
            )

        monkeypatch.setattr(importer.subprocess, "run", fake_run)
        return importer.import_material_bundle(SEQUENCE_CONFIG)

    clang_bundle = import_with("clang++", "use of undeclared identifier 'value'")
    gpp_bundle = import_with("g++", "‘value’ was not declared in this scope")
    expected_preflight = {
        "status": "blocked",
        "accepted_editable_symbols": [
            "SeqArray::SeqArray",
            "SeqArray::~SeqArray",
            "SeqArray::insertElement",
            "SeqArray::deletemin",
            "SeqArray::deleteSame",
            "SeqArray::deleteSome",
            "SeqArray::print",
        ],
        "diagnostics": [
            {
                "line": 72,
                "column": 0,
                "code": "undeclared_identifier",
                "message": "protected main references undeclared identifier value",
            }
        ],
    }

    assert clang_bundle["starter_source"]["preflight"] == expected_preflight
    assert gpp_bundle["starter_source"]["preflight"] == expected_preflight
    assert clang_bundle["bundle_hash"] == gpp_bundle["bundle_hash"]


def test_real_compiler_preflight_integrates_with_both_approved_probes() -> None:
    if importer._discover_compiler() is None:
        pytest.skip("no allowlisted compiler installed")

    sequence_bundle = importer.import_material_bundle(SEQUENCE_CONFIG)
    linked_bundle = importer.import_material_bundle(LINKED_CONFIG)

    assert sequence_bundle["starter_source"]["preflight"] == {
        "status": "blocked",
        "accepted_editable_symbols": SEQUENCE_LIVE_PREFLIGHT[
            "accepted_editable_symbols"
        ],
        "diagnostics": [
            {
                "line": 72,
                "column": 0,
                "code": "undeclared_identifier",
                "message": "protected main references undeclared identifier value",
            }
        ],
    }
    assert linked_bundle["starter_source"]["preflight"] == LINKED_LIVE_PREFLIGHT


def test_compiler_diagnostic_overflow_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importer.shutil, "which", lambda _name: "/tool/clang++")

    def noisy_run(
        argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        probe_path = argv[-1]
        diagnostic = f"{probe_path}:1:1: error: noisy\n"
        stderr = diagnostic * 10_000
        return subprocess.CompletedProcess(argv, 1, "", stderr)

    monkeypatch.setattr(importer.subprocess, "run", noisy_run)
    bundle = importer.import_material_bundle(SEQUENCE_CONFIG)

    assert bundle["starter_source"]["preflight"]["status"] == "unavailable"
    assert bundle["starter_source"]["preflight"]["diagnostics"] == []
    assert any(
        issue["code"] == "material_preflight_unavailable" for issue in bundle["issues"]
    )


def test_compiler_preexec_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importer.shutil, "which", lambda _name: "/tool/clang++")

    def failed_run(*_args: object, **_kwargs: object) -> None:
        raise subprocess.SubprocessError("preexec failed")

    monkeypatch.setattr(importer.subprocess, "run", failed_run)
    bundle = importer.import_material_bundle(LINKED_CONFIG)

    assert bundle["starter_source"]["preflight"]["status"] == "unavailable"
    assert any(
        issue["code"] == "material_preflight_unavailable" for issue in bundle["issues"]
    )


def test_no_compiler_is_blocking_and_never_marks_source_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    def no_compiler(name: str) -> None:
        requested.append(name)

    monkeypatch.setattr(importer.shutil, "which", no_compiler)
    bundle = importer.import_material_bundle(LINKED_CONFIG)

    assert requested == ["clang++", "g++"]
    assert bundle["starter_source"]["preflight"]["status"] == "unavailable"
    assert any(
        issue["code"] == "material_preflight_unavailable"
        and issue["severity"] == "blocking"
        for issue in bundle["issues"]
    )


def test_importer_rejects_teacher_controlled_compiler_flags(tmp_path: Path) -> None:
    config_path, config = _copy_config_fixture(tmp_path, LINKED_CONFIG)
    config["compiler_flags"] = ["-DANSWER=1"]
    _write_config(config_path, config)

    with pytest.raises(importer.MaterialImportError, match="config keys"):
        importer.import_material_bundle(config_path)


@pytest.mark.parametrize(
    ("config_path", "bundle_path"),
    [
        (SEQUENCE_CONFIG, MATERIALS / "sequence-list" / "bundle.json"),
        (LINKED_CONFIG, MATERIALS / "linked-list" / "bundle.json"),
    ],
)
def test_committed_bundle_is_the_deterministic_cli_projection(
    config_path: Path,
    bundle_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_path = tmp_path / "bundle.json"
    live_preflight = (
        SEQUENCE_LIVE_PREFLIGHT
        if config_path == SEQUENCE_CONFIG
        else LINKED_LIVE_PREFLIGHT
    )
    monkeypatch.setattr(
        importer,
        "_preflight_source",
        lambda _source, _symbols: deepcopy(live_preflight),
    )

    assert importer.main([str(config_path), "--output", str(output_path)]) == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == json.loads(
        bundle_path.read_text(encoding="utf-8")
    )


def test_cli_returns_controlled_error_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path, config = _copy_config_fixture(tmp_path, LINKED_CONFIG)
    config["source"]["encoding"] = {"unexpected": "object"}
    _write_config(config_path, config)

    exit_code = importer.main(
        [str(config_path), "--output", str(tmp_path / "bundle.json")]
    )

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("material import failed: source_encoding")
    assert "Traceback" not in captured.err


def test_cli_rejects_oversized_json_integer_without_traceback_or_path_leak(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_text = LINKED_CONFIG.read_text(encoding="utf-8").replace(
        '"schema_version": 1',
        '"schema_version": ' + ("9" * 5_000),
        1,
    )
    config_path = tmp_path / "oversized-integer.json"
    config_path.write_text(config_text, encoding="utf-8")

    get_digit_limit = getattr(sys, "get_int_max_str_digits", None)
    set_digit_limit = getattr(sys, "set_int_max_str_digits", None)
    previous_digit_limit = get_digit_limit() if get_digit_limit else None
    try:
        if set_digit_limit:
            set_digit_limit(0)
        exit_code = importer.main(
            [str(config_path), "--output", str(tmp_path / "bundle.json")]
        )
    finally:
        if set_digit_limit and previous_digit_limit is not None:
            set_digit_limit(previous_digit_limit)

    assert exit_code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "material import failed: config must be valid UTF-8 JSON\n"
    assert str(config_path) not in captured.err
    assert "Traceback" not in captured.err
