"""Generate service enum types from the classroom common JSON Schema."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ENUM_CLASS_NAMES = {
    "assignment_status": "AssignmentStatus",
    "session_status": "SessionStatus",
    "submission_reason": "SubmissionReason",
    "mastery_status": "MasteryStatus",
}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--common-schema", required=True, type=Path)
    parser.add_argument("--python-output", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def enum_member_name(value: str) -> str:
    return value.upper().replace("-", "_")


def render_python_enums(common_schema: dict[str, object]) -> str:
    definitions = common_schema.get("$defs")
    if not isinstance(definitions, dict):
        raise TypeError("Common schema is missing $defs.")

    sections = [
        '"""Generated from contracts/classroom/v1/common.schema.json; do not edit."""',
        "",
        "from enum import Enum",
    ]
    for definition_name, class_name in ENUM_CLASS_NAMES.items():
        definition = definitions.get(definition_name)
        if not isinstance(definition, dict):
            raise TypeError(f"Common schema is missing {definition_name}.")
        values = definition.get("enum")
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError(f"Common schema {definition_name} must contain string enum values.")
        sections.extend(["", "", f"class {class_name}(str, Enum):"])
        for value in values:
            sections.append(f'    {enum_member_name(value)} = "{value}"')
    return "\n".join(sections) + "\n"


def main() -> None:
    arguments = parse_arguments()
    common_schema = json.loads(arguments.common_schema.read_text(encoding="utf-8"))
    if not isinstance(common_schema, dict):
        raise TypeError("Common schema must be a JSON object.")
    output = render_python_enums(common_schema)
    if arguments.check:
        existing_output = (
            arguments.python_output.read_text(encoding="utf-8")
            if arguments.python_output.exists()
            else ""
        )
        if existing_output != output:
            print("Generated classroom types are stale.", file=sys.stderr)
            raise SystemExit(1)
        return

    arguments.python_output.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = arguments.python_output.with_suffix(".tmp")
    temporary_output.write_text(output, encoding="utf-8")
    temporary_output.replace(arguments.python_output)


if __name__ == "__main__":
    main()
