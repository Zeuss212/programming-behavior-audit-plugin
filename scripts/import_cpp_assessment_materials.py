"""Import approved C++ teacher fixtures into a sealed, offline material bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import resource
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path, PurePath

IMPORTER_VERSION = "cpp_assessment_material_importer_v1"
MAX_INPUT_BYTES = 256 * 1024
COMPILER_TIMEOUT_SECONDS = 10
MAX_COMPILER_DIAGNOSTIC_BYTES = 64 * 1024
COMPILER_NAMES = ("clang++", "g++")
COMPILER_ARGUMENTS = ("-std=c++17", "-fsyntax-only", "-Wall", "-Wextra", "-Wpedantic")


class MaterialImportError(ValueError):
    """The material cannot be imported without weakening the sealed contract."""


_CONFIG_KEYS = {
    "schema_version",
    "importer_version",
    "space_id",
    "parent_algorithm_id",
    "exercise_profile",
    "title",
    "statement",
    "toolchain_profile",
    "source",
    "tests",
    "expected_requirement_ids",
}
_SOURCE_KEYS = {"path", "sha256", "encoding", "artifact_id", "editable_symbols"}
_TEST_KEYS = {"path", "sha256", "encoding", "parser_profile"}

_EXPECTED_TXT_HEADINGS = {
    "sequence_txt_v1": {
        "section": "2. 测试用例（能够为每个测试用例指定分值）",
        "ordered": [
            "1. 题目描述：",
            "用例：",
            "假如输入为：",
            "则输出为：",
            "2. 测试用例（能够为每个测试用例指定分值）",
            "测试用例1：",
            "输入：",
            "期望输出:",
            "测试用例2：",
            "输入：",
            "期望输出：",
            "3. 考查的点（评价的维度）：",
        ],
    },
    "linked_txt_v1": {
        "section": "2. 测试用例 （能够为每个测试用例指定分值）",
        "ordered": [
            "1. 题目描述：",
            "习题要求：",
            "用例：",
            "假如输入为：",
            "则输出为：",
            "2. 测试用例 （能够为每个测试用例指定分值）",
            "测试用例1：",
            "输入：",
            "期望输出：",
            "测试用例2：",
            "输入：",
            "期望输出：",
            "3. 考查的点（评价的维度）：",
        ],
    },
}


_PROFILE_DEFINITIONS: dict[str, dict[str, object]] = {
    "sequence_list_v1": {
        "artifact_id": "ART_SEQUENCE_LIST_CPP_01",
        "parser_profile": "sequence_txt_v1",
        "editable_symbols": [
            "SeqArray::SeqArray",
            "SeqArray::~SeqArray",
            "SeqArray::insertElement",
            "SeqArray::deletemin",
            "SeqArray::deleteSame",
            "SeqArray::deleteSome",
            "SeqArray::print",
        ],
        "test_ids": ["TEST_SEQU0001", "TEST_SEQU0002"],
        "test_names": ["顺序表常规操作用例一", "顺序表常规操作用例二"],
        "teacher_dimensions": [
            "类的构造函数中指针数据的初始化",
            "类的析构函数中空间的释放",
            "顺序表的查找操作",
            "顺序表的删除操作",
            "顺序表的移动操作",
        ],
        "requirements": [
            {
                "id": "REQ_SEQ_INITIALIZATION",
                "name": "顺序表初始化",
                "source_statement": "类的构造函数中指针数据的初始化",
                "student_responsibility": True,
                "test_ids": ["TEST_SEQU0001", "TEST_SEQU0002"],
                "detector_profile_ids": [],
            },
            {
                "id": "REQ_SEQ_SPACE_RELEASE",
                "name": "顺序表空间释放",
                "source_statement": "类的析构函数中空间的释放",
                "student_responsibility": True,
                "test_ids": [],
                "detector_profile_ids": ["address_undefined_leak_v1"],
            },
            {
                "id": "REQ_SEQ_SEARCH",
                "name": "顺序表查找",
                "source_statement": "顺序表的查找操作",
                "student_responsibility": True,
                "test_ids": ["TEST_SEQU0001", "TEST_SEQU0002"],
                "detector_profile_ids": [],
            },
            {
                "id": "REQ_SEQ_DELETE",
                "name": "顺序表删除",
                "source_statement": "顺序表的删除操作",
                "student_responsibility": True,
                "test_ids": ["TEST_SEQU0001", "TEST_SEQU0002"],
                "detector_profile_ids": [],
            },
            {
                "id": "REQ_SEQ_MOVE",
                "name": "顺序表元素移动",
                "source_statement": "顺序表的移动操作",
                "student_responsibility": True,
                "test_ids": ["TEST_SEQU0001", "TEST_SEQU0002"],
                "detector_profile_ids": [],
            },
        ],
    },
    "linked_list_v1": {
        "artifact_id": "ART_LINKED_LIST_CPP_01",
        "parser_profile": "linked_txt_v1",
        "editable_symbols": ["MList::insertToTail", "MList::Reverse"],
        "test_ids": ["TEST_LINK0001", "TEST_LINK0002"],
        "test_names": ["六元素链表逆置", "七元素链表逆置"],
        "teacher_dimensions": ["链表的插入操作", "链表的遍历操作", "链表的删除操作"],
        "requirements": [
            {
                "id": "REQ_LINK_TAIL_INSERT",
                "name": "链表尾插",
                "source_statement": "倒置前输出与输入顺序一致。",
                "student_responsibility": True,
                "test_ids": ["TEST_LINK0001", "TEST_LINK0002"],
                "detector_profile_ids": [],
            },
            {
                "id": "REQ_LINK_REVERSE",
                "name": "链表逆置",
                "source_statement": "倒置后输出为输入序列的严格逆序。",
                "student_responsibility": True,
                "test_ids": ["TEST_LINK0001", "TEST_LINK0002"],
                "detector_profile_ids": [],
            },
            {
                "id": "REQ_LINK_TRAVERSAL",
                "name": "链表遍历",
                "source_statement": "链表的遍历操作",
                "student_responsibility": False,
                "test_ids": ["TEST_LINK0001", "TEST_LINK0002"],
                "detector_profile_ids": [],
            },
            {
                "id": "REQ_LINK_DELETE",
                "name": "链表删除",
                "source_statement": "链表的删除操作",
                "student_responsibility": False,
                "test_ids": [],
                "detector_profile_ids": [],
            },
        ],
    },
}


_REPLACEMENT_BODIES = {
    "SeqArray::SeqArray": "\n    N = NN; n = 0; arr = new int[N];\n\n",
    "SeqArray::~SeqArray": "\n    delete[] arr;\n",
    "SeqArray::insertElement": "\n    (void)value; return true;\n",
    "SeqArray::deletemin": "\n    return 0;\n",
    "SeqArray::deleteSame": "\n    (void)x;\n",
    "SeqArray::deleteSome": "\n    (void)s; (void)t;\n",
    "SeqArray::print": "\n\n\n",
}

_SUPPLIED_DEFINITIONS = {
    "MList::insertToTail": "\nvoid MList::insertToTail(int val)\n{\n    (void)val;\n}\n",
    "MList::Reverse": "\nvoid MList::Reverse()\n{\n}\n",
}


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise MaterialImportError(f"{name} must be an object")
    return value


def _require_exact_keys(
    value: dict[str, object], expected: set[str], name: str
) -> None:
    if set(value) != expected:
        raise MaterialImportError(f"unexpected {name} config keys")


def _open_without_symlink_components(path: Path, label: str) -> int:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise MaterialImportError(f"{label} symlink-safe reads are unavailable")
    absolute = Path(os.path.abspath(path))
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC
    directory_fd = os.open("/", directory_flags)
    try:
        for component in absolute.parent.parts[1:]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(absolute.name, file_flags, dir_fd=directory_fd)
    except OSError as exc:
        raise MaterialImportError(
            f"{label} must be a regular file with no symlink path components"
        ) from exc
    finally:
        os.close(directory_fd)


def _read_bounded_regular_file(
    path: Path, expected_hash: str | None, label: str
) -> bytes:
    try:
        file_fd = _open_without_symlink_components(path, label)
    except OSError as exc:
        raise MaterialImportError(
            f"{label} must be a regular file with no symlink path components"
        ) from exc
    try:
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise MaterialImportError(f"{label} must be a regular file")
        if metadata.st_size > MAX_INPUT_BYTES:
            raise MaterialImportError(f"{label} exceeds 256 KiB")
        chunks: list[bytes] = []
        retained = 0
        while retained <= MAX_INPUT_BYTES:
            chunk = os.read(file_fd, min(64 * 1024, MAX_INPUT_BYTES + 1 - retained))
            if not chunk:
                break
            chunks.append(chunk)
            retained += len(chunk)
        data = b"".join(chunks)
        if len(data) > MAX_INPUT_BYTES:
            raise MaterialImportError(f"{label} exceeds 256 KiB")
    finally:
        os.close(file_fd)
    if expected_hash is not None and hashlib.sha256(data).hexdigest() != expected_hash:
        raise MaterialImportError(f"{label} hash drift")
    return data


def _safe_input_path(config_dir: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise MaterialImportError(f"{label} path must be a safe basename")
    pure = PurePath(value)
    if pure.is_absolute() or pure.name != value or "/" in value or "\\" in value:
        raise MaterialImportError(f"{label} path must be a safe basename")
    return config_dir / value


def _decode_source(data: bytes, configured_encoding: object) -> tuple[str, str]:
    if configured_encoding not in {"utf-8", "gb18030"}:
        raise MaterialImportError("unexpected source encoding")
    try:
        utf8_text = data.decode("utf-8")
    except UnicodeDecodeError:
        if configured_encoding != "gb18030":
            raise MaterialImportError("unexpected source encoding") from None
        try:
            return data.decode("gb18030"), "gb18030"
        except UnicodeDecodeError:
            raise MaterialImportError("unexpected source encoding") from None
    if configured_encoding != "utf-8":
        raise MaterialImportError("unexpected source encoding")
    return utf8_text, "utf-8"


def _decode_tests(data: bytes, configured_encoding: object) -> str:
    if configured_encoding != "utf-8":
        raise MaterialImportError("unexpected TXT encoding")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise MaterialImportError("unexpected TXT encoding") from None


def _heading_set(parser_profile: str) -> set[str]:
    headings = {
        "1. 题目描述：",
        "用例：",
        "假如输入为：",
        "则输出为：",
        "测试用例1：",
        "测试用例2：",
        "输入：",
        "3. 考查的点（评价的维度）：",
    }
    if parser_profile == "sequence_txt_v1":
        return headings | {"期望输出:", "期望输出："}
    if parser_profile == "linked_txt_v1":
        return headings | {"习题要求：", "期望输出："}
    raise MaterialImportError("unsupported TXT parser profile")


def _looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    return stripped.endswith((":", "：")) or re.match(r"^\d+\.\s", stripped) is not None


def _trim_block(lines: list[str]) -> str:
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"


def _parse_teacher_tests(
    text: str, parser_profile: str, profile: dict[str, object]
) -> tuple[list[dict[str, object]], list[str]]:
    lines = text.splitlines()
    test_starts = [
        index
        for index, line in enumerate(lines)
        if re.fullmatch(r"测试用例\d+：", line.strip())
    ]
    if len(test_starts) != 2:
        raise MaterialImportError("TXT must contain exactly two parsed tests")
    numbers = [int(re.search(r"\d+", lines[index]).group()) for index in test_starts]
    if numbers != [1, 2]:
        raise MaterialImportError("TXT must contain exactly two ordered parsed tests")

    allowed_headings = _heading_set(parser_profile)
    heading_contract = _EXPECTED_TXT_HEADINGS[parser_profile]
    for line in lines:
        if (
            _looks_like_heading(line)
            and line.strip() not in allowed_headings
            and line.strip() != heading_contract["section"]
        ):
            raise MaterialImportError(f"unrecognized TXT heading: {line.strip()}")
    if sum(line.strip() == heading_contract["section"] for line in lines) != 1:
        raise MaterialImportError("unrecognized TXT section heading")
    observed_headings = [
        line.strip()
        for line in lines
        if line.strip() in allowed_headings
        or line.strip() == heading_contract["section"]
    ]
    if observed_headings != heading_contract["ordered"]:
        raise MaterialImportError("unrecognized TXT heading sequence")

    dimension_heading = "3. 考查的点（评价的维度）："
    try:
        dimension_start = next(
            index
            for index, line in enumerate(lines)
            if line.strip() == dimension_heading
        )
    except StopIteration:
        raise MaterialImportError(
            "unrecognized TXT heading: missing dimension heading"
        ) from None

    expected_headings = (
        ["期望输出:", "期望输出："]
        if parser_profile == "sequence_txt_v1"
        else ["期望输出：", "期望输出："]
    )
    parsed: list[dict[str, object]] = []
    test_ids = profile["test_ids"]
    test_names = profile["test_names"]
    for order, start in enumerate(test_starts):
        end = (
            test_starts[order + 1] if order + 1 < len(test_starts) else dimension_start
        )
        block = lines[start + 1 : end]
        try:
            input_index = next(
                i for i, line in enumerate(block) if line.strip() == "输入："
            )
        except StopIteration:
            raise MaterialImportError(
                "unrecognized TXT heading: missing input heading"
            ) from None
        required_output_heading = expected_headings[order]
        try:
            output_index = next(
                i
                for i, line in enumerate(block)
                if line.strip() == required_output_heading
            )
        except StopIteration:
            raise MaterialImportError(
                "unrecognized TXT heading: expected output format drift"
            ) from None
        if output_index <= input_index:
            raise MaterialImportError("unrecognized TXT heading order")
        test_without_hash = {
            "id": test_ids[order],
            "name": test_names[order],
            "kind": "stdin_stdout",
            "input": _trim_block(block[input_index + 1 : output_index]),
            "expected_stdout": _trim_block(block[output_index + 1 :]),
            "comparison": "normalized_text_v1",
            "timeout_ms": 2000,
            "enabled": True,
            "source": "teacher",
            "order": order,
        }
        parsed.append(
            {**test_without_hash, "content_hash": _canonical_sha256(test_without_hash)}
        )

    dimensions = [line.strip() for line in lines[dimension_start + 1 :] if line.strip()]
    if dimensions != profile["teacher_dimensions"]:
        raise MaterialImportError("teacher dimension format drift")
    return parsed, dimensions


def _find_definition_brace(source: str, symbol: str) -> int | None:
    name = re.escape(symbol)
    match = re.search(rf"(?:^|\n)[^\n;{{}}]*{name}\s*\([^;{{}}]*\)\s*\{{", source)
    return match.end() - 1 if match else None


def _replace_body(source: str, symbol: str, body: str) -> str:
    opening = _find_definition_brace(source, symbol)
    if opening is None:
        raise MaterialImportError(f"configured editable symbol is absent: {symbol}")
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                original_body = source[opening + 1 : index]
                if original_body.count("\n") != body.count("\n"):
                    raise MaterialImportError(
                        f"adapter body line contract drift: {symbol}"
                    )
                return source[: opening + 1] + body + source[index:]
    raise MaterialImportError(f"unterminated editable symbol: {symbol}")


def _build_probe(source: str, editable_symbols: list[str]) -> str:
    probe = source
    for symbol in editable_symbols:
        if symbol in _REPLACEMENT_BODIES:
            probe = _replace_body(probe, symbol, _REPLACEMENT_BODIES[symbol])
        elif symbol in _SUPPLIED_DEFINITIONS:
            if _find_definition_brace(probe, symbol) is None:
                probe += _SUPPLIED_DEFINITIONS[symbol]
            else:
                raise MaterialImportError(
                    f"supplied editable symbol unexpectedly implemented: {symbol}"
                )
        else:
            raise MaterialImportError(f"editable symbol is not adapter-owned: {symbol}")
    return probe


def _discover_compiler() -> str | None:
    for name in COMPILER_NAMES:
        compiler = shutil.which(name)
        if compiler is not None:
            return compiler
    return None


_DIAGNOSTIC_RE = re.compile(
    r"^(?P<path>.*?):(?P<line>\d+):(?P<column>\d+):\s*"
    r"(?P<severity>fatal error|error|warning|note):\s*(?P<message>.*?)(?:\s+\[(?P<flag>-[^\]]+)\])?$"
)
_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])/(?:[^/\s()'\":]+/)*[^/\s()'\":]+")
_WINDOWS_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z]:\\(?:[^\\\s()'\":]+\\)*[^\\\s()'\":]+"
)


def _sanitize_diagnostics(stderr: str) -> list[dict[str, object]]:
    diagnostics: list[dict[str, object]] = []
    for line in stderr.splitlines():
        match = _DIAGNOSTIC_RE.match(line)
        if match is None:
            continue
        severity = match.group("severity")
        code = match.group("flag") or (
            "compiler_error" if "error" in severity else f"compiler_{severity}"
        )
        message = match.group("message").replace("\x00", "")
        message = _ABSOLUTE_PATH_RE.sub("<path>", message)
        message = _WINDOWS_PATH_RE.sub("<path>", message)[:500]
        diagnostics.append(
            {
                "line": int(match.group("line")),
                "column": int(match.group("column")),
                "code": code,
                "message": message,
            }
        )
        if len(diagnostics) == 20:
            break
    return diagnostics


def _preflight_source(source: str, editable_symbols: list[str]) -> dict[str, object]:
    compiler = _discover_compiler()
    if compiler is None:
        return {
            "status": "unavailable",
            "accepted_editable_symbols": editable_symbols,
            "diagnostics": [],
        }
    probe = _build_probe(source, editable_symbols)
    try:
        with tempfile.TemporaryDirectory(prefix="cpp-material-preflight-") as temporary:
            probe_path = Path(temporary) / "probe.cpp"
            probe_path.write_text(probe, encoding="utf-8")
            with tempfile.TemporaryFile() as diagnostics_file:
                result = subprocess.run(
                    [compiler, *COMPILER_ARGUMENTS, str(probe_path)],
                    stdout=subprocess.DEVNULL,
                    stderr=diagnostics_file,
                    timeout=COMPILER_TIMEOUT_SECONDS,
                    check=False,
                    shell=False,
                    preexec_fn=_limit_compiler_output,
                )
                diagnostics_file.seek(0)
                diagnostics_bytes = diagnostics_file.read(
                    MAX_COMPILER_DIAGNOSTIC_BYTES + 1
                )
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return {
            "status": "unavailable",
            "accepted_editable_symbols": editable_symbols,
            "diagnostics": [],
        }
    fake_stderr = result.stderr if isinstance(result.stderr, str) else None
    if fake_stderr is not None:
        diagnostics_bytes = fake_stderr.encode("utf-8", errors="replace")
    if len(diagnostics_bytes) >= MAX_COMPILER_DIAGNOSTIC_BYTES:
        return {
            "status": "unavailable",
            "accepted_editable_symbols": editable_symbols,
            "diagnostics": [],
        }
    return {
        "status": "ready" if result.returncode == 0 else "blocked",
        "accepted_editable_symbols": editable_symbols,
        "diagnostics": _sanitize_diagnostics(
            diagnostics_bytes.decode("utf-8", errors="replace")
        ),
    }


def _limit_compiler_output() -> None:
    resource.setrlimit(
        resource.RLIMIT_FSIZE,
        (MAX_COMPILER_DIAGNOSTIC_BYTES, MAX_COMPILER_DIAGNOSTIC_BYTES),
    )


def _base_issues(profile_name: str) -> list[dict[str, object]]:
    if profile_name == "sequence_list_v1":
        return [
            {
                "code": "starter_source_non_utf8_confirmation_required",
                "severity": "blocking",
                "scope": "source",
                "requirement_id": None,
                "message": "GB18030 原件需要教师确认 UTF-8 候选副本后才能发布。",
            },
            {
                "code": "detector_profile_unavailable",
                "severity": "blocking",
                "scope": "requirement",
                "requirement_id": "REQ_SEQ_SPACE_RELEASE",
                "message": "空间释放要求依赖的 address_undefined_leak_v1 当前不可用。",
            },
            {
                "code": "boundary_coverage_incomplete",
                "severity": "warning",
                "scope": "test",
                "requirement_id": None,
                "message": "教师用例未覆盖空表、容量边界和全部删除场景。",
            },
        ]
    return [
        {
            "code": "teacher_dimension_not_student_responsibility",
            "severity": "blocking",
            "scope": "requirement",
            "requirement_id": "REQ_LINK_TRAVERSAL",
            "message": "链表遍历由受保护的 print 函数提供，不是学生编辑责任。",
        },
        {
            "code": "teacher_dimension_outside_task",
            "severity": "blocking",
            "scope": "requirement",
            "requirement_id": "REQ_LINK_DELETE",
            "message": "链表删除不在题目或学生 TODO 范围内。",
        },
        {
            "code": "required_student_dimension_missing",
            "severity": "blocking",
            "scope": "requirement",
            "requirement_id": "REQ_LINK_REVERSE",
            "message": "教师 TXT 的考查维度缺少学生必须完成的链表逆置。",
        },
        {
            "code": "boundary_coverage_incomplete",
            "severity": "warning",
            "scope": "test",
            "requirement_id": None,
            "message": "教师用例未覆盖空链表和单元素场景。",
        },
    ]


def _validate_config(
    config: dict[str, object],
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    _require_exact_keys(config, _CONFIG_KEYS, "top-level")
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise MaterialImportError("schema_version must be integer 1")
    if config["importer_version"] != IMPORTER_VERSION:
        raise MaterialImportError("unsupported importer_version")
    if config["toolchain_profile"] != "cpp17_stdio_v1":
        raise MaterialImportError("unsupported toolchain profile")
    profile_name = config["exercise_profile"]
    if not isinstance(profile_name, str) or profile_name not in _PROFILE_DEFINITIONS:
        raise MaterialImportError("unsupported exercise profile")
    profile = _PROFILE_DEFINITIONS[profile_name]
    source_config = _require_object(config["source"], "source")
    tests_config = _require_object(config["tests"], "tests")
    _require_exact_keys(source_config, _SOURCE_KEYS, "source")
    _require_exact_keys(tests_config, _TEST_KEYS, "tests")
    if source_config["editable_symbols"] != profile["editable_symbols"]:
        raise MaterialImportError("editable symbols do not match importer version")
    artifact_id = source_config["artifact_id"]
    if (
        not isinstance(artifact_id, str)
        or re.fullmatch(r"ART_[A-Z0-9_]{1,80}", artifact_id) is None
        or artifact_id != profile["artifact_id"]
    ):
        raise MaterialImportError("artifact_id does not match importer version")
    if tests_config["parser_profile"] != profile["parser_profile"]:
        raise MaterialImportError("TXT parser profile does not match exercise profile")
    if not isinstance(source_config["encoding"], str) or source_config[
        "encoding"
    ] not in {"utf-8", "gb18030"}:
        raise MaterialImportError("source_encoding must be utf-8 or gb18030")
    if tests_config["encoding"] != "utf-8":
        raise MaterialImportError("tests_encoding must be utf-8")
    expected_ids = [item["id"] for item in profile["requirements"]]
    if config["expected_requirement_ids"] != expected_ids:
        raise MaterialImportError(
            "expected requirement IDs do not match importer version"
        )
    for key in ("space_id", "parent_algorithm_id", "title", "statement"):
        if not isinstance(config[key], str) or not config[key]:
            raise MaterialImportError(f"{key} must be a non-empty string")
    for key, maximum in (
        ("space_id", 200),
        ("parent_algorithm_id", 200),
        ("title", 200),
        ("statement", 10_000),
    ):
        if len(config[key]) > maximum:
            raise MaterialImportError(f"{key} exceeds {maximum} characters")
    for value, label in (
        (source_config["sha256"], "source"),
        (tests_config["sha256"], "tests"),
    ):
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise MaterialImportError(f"invalid {label} hash")
    return source_config, tests_config, profile


def import_material_bundle(config_path: Path) -> dict[str, object]:
    """Import one versioned teacher-material bundle without network access."""

    config_path = Path(config_path)
    try:
        config_bytes = _read_bounded_regular_file(config_path, None, "config")
        config = json.loads(config_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterialImportError("config must be valid UTF-8 JSON") from exc
    config = _require_object(config, "config")
    source_config, tests_config, profile = _validate_config(config)
    config_dir = config_path.parent
    source_path = _safe_input_path(config_dir, source_config["path"], "source")
    tests_path = _safe_input_path(config_dir, tests_config["path"], "tests")
    source_bytes = _read_bounded_regular_file(
        source_path, source_config["sha256"], "source"
    )
    tests_bytes = _read_bounded_regular_file(
        tests_path, tests_config["sha256"], "tests"
    )
    source_text, detected_encoding = _decode_source(
        source_bytes, source_config["encoding"]
    )
    tests_text = _decode_tests(tests_bytes, tests_config["encoding"])
    assessment_tests, _teacher_dimensions = _parse_teacher_tests(
        tests_text, tests_config["parser_profile"], profile
    )
    preflight = _preflight_source(source_text, source_config["editable_symbols"])
    issues = _base_issues(config["exercise_profile"])
    if preflight["status"] == "unavailable":
        issues.insert(
            0,
            {
                "code": "material_preflight_unavailable",
                "severity": "blocking",
                "scope": "source",
                "requirement_id": None,
                "message": "受保护源框架预检工具不可用，源码未标记为可发布。",
            },
        )
    elif preflight["status"] == "blocked":
        has_expected_value_error = config[
            "exercise_profile"
        ] == "sequence_list_v1" and any(
            diagnostic["line"] == 72
            and diagnostic["code"] == "compiler_error"
            and "value" in diagnostic["message"]
            for diagnostic in preflight["diagnostics"]
        )
        issues.insert(
            0,
            {
                "code": "starter_source_protected_compile_error",
                "severity": "blocking",
                "scope": "source",
                "requirement_id": None,
                "message": (
                    "受保护 main 使用未声明标识符 value。"
                    if has_expected_value_error
                    else "受保护源框架编译预检失败。"
                ),
            },
        )

    utf8_candidate = source_text.encode("utf-8")
    starter_source = {
        "artifact_id": source_config["artifact_id"],
        "display_name": source_config["path"],
        "file_name": source_config["path"],
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "size_bytes": len(source_bytes),
        "source": "fincolab_experiment",
        "detected_encoding": detected_encoding,
        "utf8_candidate_sha256": hashlib.sha256(utf8_candidate).hexdigest(),
        "utf8_confirmed": detected_encoding == "utf-8",
        "preflight": preflight,
    }
    bundle: dict[str, object] = {
        "schema_version": 1,
        "importer_version": IMPORTER_VERSION,
        "space_id": config["space_id"],
        "parent_algorithm_id": config["parent_algorithm_id"],
        "title": config["title"],
        "statement": config["statement"],
        "toolchain_profile": config["toolchain_profile"],
        "starter_source": starter_source,
        "requirements": deepcopy(profile["requirements"]),
        "assessment_tests": assessment_tests,
        "detector_profiles": [
            {
                "id": "address_undefined_leak_v1",
                "available": False,
            }
        ],
        "issues": issues,
    }
    bundle["bundle_hash"] = _canonical_sha256(bundle)
    return bundle


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        bundle = import_material_bundle(args.config)
        if args.output.is_symlink():
            raise MaterialImportError("output must not be a symlink")
        args.output.write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (MaterialImportError, OSError) as exc:
        print(f"material import failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
