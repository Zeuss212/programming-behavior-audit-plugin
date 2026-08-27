"""Validation against the versioned classroom JSON Schema contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

PLUGIN_SCHEMA_BASE_URI = "https://classroom.local/plugin/api-schemas/"
PROFILE_SCHEMA_FILENAMES = (
    "profile-draft-v2.json",
    "profile-version-v2.json",
    "profile-draft-v3.json",
    "profile-version-v3.json",
)


class ClassroomSchemaRegistry:
    """Load all v1 contracts once so their references resolve deterministically."""

    def __init__(
        self,
        contract_directory: Path,
        *,
        plugin_schema_directory: Path | None = None,
    ) -> None:
        documents: dict[str, dict[str, Any]] = {}
        resources: list[tuple[str, Resource[dict[str, Any]]]] = []

        for path in sorted(contract_directory.glob("*.schema.json")):
            document = json.loads(path.read_text(encoding="utf-8"))
            schema_id = document.get("$id")
            if not isinstance(schema_id, str) or not schema_id:
                raise ValueError(f"Schema {path.name} is missing a non-empty $id.")
            documents[path.stem.removesuffix(".schema")] = document
            resources.append((schema_id, Resource.from_contents(document)))

        plugin_schema_directory = plugin_schema_directory or (
            contract_directory.parents[2] / "myextension" / "api_schemas"
        )
        for filename in PROFILE_SCHEMA_FILENAMES:
            path = plugin_schema_directory / filename
            if not path.is_file():
                raise ValueError(f"Plugin schema {path} is missing.")
            document = json.loads(path.read_text(encoding="utf-8"))
            resources.append(
                (f"{PLUGIN_SCHEMA_BASE_URI}{filename}", Resource.from_contents(document))
            )

        self._documents = documents
        self._registry = Registry().with_resources(resources)

    def validate(self, schema_name: str, payload: object) -> None:
        """Validate a boundary payload or raise the JSON Schema error unchanged."""

        schema = self._documents.get(schema_name)
        if schema is None:
            raise KeyError(f"Unknown classroom schema: {schema_name}")
        Draft202012Validator(schema, registry=self._registry).validate(payload)
