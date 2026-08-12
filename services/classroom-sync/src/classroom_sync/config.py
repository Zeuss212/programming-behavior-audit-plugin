"""Runtime configuration for the classroom synchronization service."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Only configuration required by the currently implemented service."""

    database_url: str | None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        values = os.environ if env is None else env
        database_url = values.get("CLASSROOM_DATABASE_URL", "").strip() or None
        if database_url is None:
            raise RuntimeError("CLASSROOM_DATABASE_URL must be configured.")
        return cls(database_url=database_url)
