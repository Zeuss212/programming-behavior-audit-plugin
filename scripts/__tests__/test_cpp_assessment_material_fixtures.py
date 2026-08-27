from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MATERIALS_ROOT = ROOT / "deploy" / "classroom" / "local-demo" / "materials"
MANIFEST_PATH = MATERIALS_ROOT / "source-manifest.json"

EXPECTED_MATERIALS = {
    "sequence-list/顺序表操作练习01.cpp": {
        "original_basename": "顺序表操作练习01.cpp",
        "sha256": "44717d0b2e1a9c2fe829db08e0171b2aa86f82b3e0cd0ab97b41512af190eecc",
        "encoding": "gb18030",
        "media_type": "text/x-c++src",
    },
    "sequence-list/编码习题1-线性表的基本操作(1).txt": {
        "original_basename": "编码习题1-线性表的基本操作(1).txt",
        "sha256": "2feef3567dd6ce47eda7dda2f14a775fc756684f21de9a8c315ccf91d3bb6cff",
        "encoding": "utf-8",
        "media_type": "text/plain",
    },
    "linked-list/链表操作练习02.cpp": {
        "original_basename": "链表操作练习02.cpp",
        "sha256": "6468a373fbf602c7c8d5012fca2a5ead4973bb8daf54714c7ef22d84bbd68cb0",
        "encoding": "utf-8",
        "media_type": "text/x-c++src",
    },
    "linked-list/编码习题2-链表的逆置操作.txt": {
        "original_basename": "编码习题2-链表的逆置操作.txt",
        "sha256": "78c32919dc9bcbf2cf1477f44faf1cda649f5c2c93aa160788ffa8ad2a2d8afd",
        "encoding": "utf-8",
        "media_type": "text/plain",
    },
}


def test_teacher_material_fixtures_preserve_approved_bytes_and_text_contract():
    """Real teacher materials remain immutable, encoded, and provenance-bound."""
    for relative_path, expected in EXPECTED_MATERIALS.items():
        path = MATERIALS_ROOT / relative_path
        assert path.is_file(), f"missing immutable material fixture: {relative_path}"
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected["sha256"]

    sequence_source = (MATERIALS_ROOT / "sequence-list/顺序表操作练习01.cpp").read_bytes()
    with pytest.raises(UnicodeDecodeError):
        sequence_source.decode("utf-8")

    linked_source = (MATERIALS_ROOT / "linked-list/链表操作练习02.cpp").read_bytes()
    linked_source.decode("utf-8")

    sequence_text = (MATERIALS_ROOT / "sequence-list/编码习题1-线性表的基本操作(1).txt").read_text(
        encoding="utf-8"
    )
    sequence_output_lines = [
        line
        for line in sequence_text.splitlines()
        if line.startswith(
            (
                "顺序表数据为",
                "删除最小值后为",
                "最小值",
                "删除相同值后为",
                "删除指定范围数值后为",
            )
        )
    ]
    assert sequence_output_lines
    assert all(":" in line and "：" not in line for line in sequence_output_lines)

    linked_text = (MATERIALS_ROOT / "linked-list/编码习题2-链表的逆置操作.txt").read_text(
        encoding="utf-8"
    )
    linked_output_lines = [
        line for line in linked_text.splitlines() if line.startswith(("倒置前为", "倒置后为"))
    ]
    assert linked_output_lines
    assert all("：" in line and ":" not in line for line in linked_output_lines)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["captured_at"] == "2026-08-27"
    assert len(manifest["materials"]) == len(EXPECTED_MATERIALS)
    assert {entry["destination"] for entry in manifest["materials"]} == set(EXPECTED_MATERIALS)
    for entry in manifest["materials"]:
        relative_path = entry["destination"]
        assert relative_path in EXPECTED_MATERIALS
        expected = EXPECTED_MATERIALS[relative_path]
        assert entry == {
            "destination": relative_path,
            "original_basename": expected["original_basename"],
            "location_class": "teacher_provided_local_material",
            "sha256": expected["sha256"],
            "encoding": expected["encoding"],
            "media_type": expected["media_type"],
        }

    manifest_text = MANIFEST_PATH.read_text(encoding="utf-8")
    assert "/Users/sxh/Library/Containers/com.tencent.xinWeChat" not in manifest_text
    assert "wxid_" not in manifest_text
    assert "ARK_API_KEY" not in manifest_text
