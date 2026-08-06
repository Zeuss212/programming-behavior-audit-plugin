"""Read-only catalog of packaged teacher-facing dimension templates."""

from __future__ import annotations

import json
from importlib.resources import files


_TEMPLATE_IDS = (
    "repeated-editing",
    "debug-chain",
    "repeated-run-failures",
    "pause-without-validation",
)


def get_template(template_id: str, version: int = 1) -> dict[str, object]:
    """Return one exact packaged template version."""
    if template_id not in _TEMPLATE_IDS or version != 1:
        raise KeyError((template_id, version))
    resource = (
        files("myextension")
        .joinpath("resources", "dimension_templates")
        .joinpath(f"{template_id}-v{version}.json")
    )
    return json.loads(resource.read_text(encoding="utf-8"))


def list_templates() -> list[dict[str, object]]:
    """Return the fixed Pilot template catalog in teacher-facing order."""
    return [get_template(template_id) for template_id in _TEMPLATE_IDS]
