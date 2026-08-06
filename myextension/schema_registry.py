"""Validation entry point for versioned Pilot API JSON contracts."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


SCHEMA_ROOT = Path(__file__).with_name("api_schemas")


def schema_path(schema_name: str) -> Path:
    """Return a packaged schema path without allowing path traversal."""
    if not schema_name.replace("-", "").isalnum():
        raise ValueError("Invalid schema name.")
    path = SCHEMA_ROOT / f"{schema_name}.json"
    if not path.is_file():
        raise KeyError(schema_name)
    return path


def validate_schema(schema_name: str, payload: object) -> None:
    """Validate a payload against a registered JSON contract."""
    root_path = schema_path(schema_name)
    schema = json.loads(root_path.read_text(encoding="utf-8"))
    schema.setdefault("$id", root_path.as_uri())
    resources = []
    for path in SCHEMA_ROOT.glob("*.json"):
        resources.append(
            (
                path.as_uri(),
                Resource.from_contents(
                    json.loads(path.read_text(encoding="utf-8")),
                    default_specification=DRAFT202012,
                ),
            )
        )
    Draft202012Validator(
        schema,
        registry=Registry().with_resources(resources),
    ).validate(payload)

    if schema_name == "segment-batch-v1":
        _validate_segment_batch_range(payload)


def _validate_segment_batch_range(payload: object) -> None:
    """Apply the two cross-field invariants unavailable in standard JSON Schema."""
    if not isinstance(payload, dict):
        return

    first_sequence = payload.get("first_sequence")
    last_sequence = payload.get("last_sequence")
    segments = payload.get("segments")
    if not isinstance(first_sequence, int) or not isinstance(last_sequence, int):
        return
    if last_sequence < first_sequence:
        raise ValidationError("last_sequence must be greater than or equal to first_sequence.")
    if isinstance(segments, list) and len(segments) != last_sequence - first_sequence + 1:
        raise ValidationError("segments length must equal the inclusive sequence range.")
