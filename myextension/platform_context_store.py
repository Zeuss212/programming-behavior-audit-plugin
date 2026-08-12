"""Private on-disk context for a registered classroom plugin session."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from .canonical_json import atomic_write_json

CONTEXT_FILENAME = "platform-context.json"
CONTEXT_KEYS = {
    "assignment_id",
    "plan_id",
    "plan_version",
    "session_id",
    "access_token",
    "access_token_expires_at",
    "evidence_cutoff_at",
}


@dataclass(frozen=True)
class RegisteredPlatformContext:
    assignment_id: str
    plan_id: str
    plan_version: int
    session_id: str
    access_token: str
    access_token_expires_at: str
    evidence_cutoff_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "assignment_id": self.assignment_id,
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "session_id": self.session_id,
            "access_token": self.access_token,
            "access_token_expires_at": self.access_token_expires_at,
            "evidence_cutoff_at": self.evidence_cutoff_at,
        }

    @classmethod
    def from_dict(cls, value: object) -> RegisteredPlatformContext:
        if not isinstance(value, dict) or set(value) != CONTEXT_KEYS:
            raise ValueError("Platform context has invalid keys.")
        fields = {key: value[key] for key in CONTEXT_KEYS}
        for key in (
            "assignment_id",
            "plan_id",
            "session_id",
            "access_token",
            "access_token_expires_at",
            "evidence_cutoff_at",
        ):
            if not isinstance(fields[key], str) or not fields[key].strip():
                raise ValueError(f"Platform context {key} must be a non-empty string.")
        for key in ("assignment_id", "plan_id", "session_id"):
            try:
                UUID(str(fields[key]))
            except ValueError as error:
                raise ValueError(f"Platform context {key} must be a UUID.") from error
        if (
            not isinstance(fields["plan_version"], int)
            or isinstance(fields["plan_version"], bool)
            or fields["plan_version"] < 1
        ):
            raise ValueError("Platform context plan_version must be positive.")
        for key in ("access_token_expires_at", "evidence_cutoff_at"):
            cls._require_timezone_aware_timestamp(str(fields[key]), key)
        return cls(
            assignment_id=str(fields["assignment_id"]),
            plan_id=str(fields["plan_id"]),
            plan_version=fields["plan_version"],
            session_id=str(fields["session_id"]),
            access_token=str(fields["access_token"]),
            access_token_expires_at=str(fields["access_token_expires_at"]),
            evidence_cutoff_at=str(fields["evidence_cutoff_at"]),
        )

    @staticmethod
    def _require_timezone_aware_timestamp(value: str, field: str) -> None:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"Platform context {field} must be an ISO-8601 timestamp.") from error
        if parsed.tzinfo is None:
            raise ValueError(f"Platform context {field} must include a timezone.")


class PlatformContextStore:
    """Persist only the post-registration context; launch tickets never enter this store."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._path = self._root / CONTEXT_FILENAME

    def save_registered_context(
        self, context: RegisteredPlatformContext
    ) -> RegisteredPlatformContext:
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        atomic_write_json(self._path, context.to_dict())
        return context

    def read_registered_context(self) -> RegisteredPlatformContext | None:
        if not self._path.is_file():
            return None
        import json

        try:
            raw: Any = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("Platform context is unreadable.") from error
        return RegisteredPlatformContext.from_dict(raw)
