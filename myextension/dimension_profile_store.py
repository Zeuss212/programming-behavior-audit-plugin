"""Filesystem storage for guided drafts and immutable published profiles."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID, uuid4

from jsonschema import ValidationError

from .canonical_json import atomic_write_json, canonical_json_bytes, sha256_json
from .profile_validator import validate_profile_draft
from .schema_registry import validate_schema


class InvalidProfileIdError(ValueError):
    """Raised before an untrusted profile identifier reaches a path."""


class ProfileConflictError(RuntimeError):
    """Raised when an immutable published version already exists."""


class ProfileConfirmationError(ProfileConflictError):
    """Raised when a v2 draft has not been confirmed in its current form."""


class ProfileIntegrityError(RuntimeError):
    """Raised when stored profile content and its projection disagree."""


_STORED_VERSION_V1_KEYS = {
    "schema_version",
    "profile_id",
    "version",
    "problem_id",
    "title",
    "dimensions",
    "content_hash",
}
_STORED_VERSION_V2_KEYS = _STORED_VERSION_V1_KEYS | {
    "problem_context",
    "knowledge_points",
    "assessment_tests",
    "confirmations",
}
_PROJECTION_KEYS = {
    "profile_id",
    "version",
    "content_hash",
    "deployment_status",
    "preview_status",
}


def _write_all(descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("Audit projection write made no progress.")
        remaining = remaining[written:]


def _canonical_profile_id(profile_id: str) -> str:
    if not isinstance(profile_id, str):
        raise InvalidProfileIdError("profile_id must be a canonical UUID.")
    try:
        parsed = UUID(profile_id)
    except (ValueError, AttributeError) as error:
        raise InvalidProfileIdError("profile_id must be a canonical UUID.") from error
    if str(parsed) != profile_id:
        raise InvalidProfileIdError("profile_id must be a canonical UUID.")
    return profile_id


class DimensionProfileStore:
    """Store drafts, immutable versions, and mutable deployment projections."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._profiles_root = self.root / "config" / "dimension_profiles"
        self._audit_path = self.root / "audit" / "profile_deployment.jsonl"
        self._lock = threading.RLock()

    def _profile_dir(self, profile_id: str) -> Path:
        return self._profiles_root / _canonical_profile_id(profile_id)

    def create_draft(self, payload: Mapping[str, object]) -> dict[str, object]:
        draft = validate_profile_draft(payload)
        profile_id = str(uuid4())
        stored = {"profile_id": profile_id, "revision": 1, **draft}
        with self._lock:
            atomic_write_json(self._profile_dir(profile_id) / "draft.json", stored)
        return stored

    def update_draft(
        self,
        profile_id: str,
        payload: Mapping[str, object],
        *,
        expected_revision: int | None = None,
    ) -> dict[str, object]:
        profile_dir = self._profile_dir(profile_id)
        draft = validate_profile_draft(payload)
        if expected_revision is not None and (
            not isinstance(expected_revision, int)
            or isinstance(expected_revision, bool)
            or expected_revision < 1
        ):
            raise ValueError("expected_revision must be a positive integer.")
        with self._lock:
            current = self._read_json(profile_dir / "draft.json")
            if (
                expected_revision is not None
                and current["revision"] != expected_revision
            ):
                raise ProfileConflictError("Draft revision does not match.")
            stored = {
                "profile_id": profile_id,
                "revision": current["revision"] + 1,
                **draft,
            }
            atomic_write_json(profile_dir / "draft.json", stored)
        return stored

    def _draft_payload(self, profile_id: str) -> dict[str, object]:
        stored = self._read_json(self._profile_dir(profile_id) / "draft.json")
        return {
            key: value
            for key, value in stored.items()
            if key not in {"profile_id", "revision"}
        }

    def _next_version(self, profile_dir: Path) -> int:
        versions = []
        for path in profile_dir.glob("v*.json"):
            stem = path.stem
            if stem[1:].isdigit() and int(stem[1:]) >= 1:
                versions.append(int(stem[1:]))
        return max(versions, default=0) + 1

    def publish(self, profile_id: str) -> dict[str, object]:
        profile_dir = self._profile_dir(profile_id)
        with self._lock:
            draft = validate_profile_draft(self._draft_payload(profile_id))
            version = self._next_version(profile_dir)
            version_path = profile_dir / f"v{version}.json"
            if version_path.exists():
                raise ProfileConflictError(
                    f"Published profile version v{version} already exists."
                )
            if draft["schema_version"] == 2:
                confirmations = draft["confirmations"]
                if not draft["knowledge_points"]:
                    raise ProfileConfirmationError(
                        "At least one knowledge point is required to publish."
                    )
                if (
                    confirmations["knowledge_points_hash"] is None
                    or confirmations["tests_hash"] is None
                ):
                    raise ProfileConfirmationError(
                        "Current knowledge points and tests must be confirmed."
                    )
                covered_points = {
                    point_id
                    for assessment_test in draft["assessment_tests"]
                    if assessment_test["enabled"]
                    for point_id in assessment_test["knowledge_point_ids"]
                }
                expected_points = {
                    item["id"] for item in draft["knowledge_points"]
                }
                if covered_points != expected_points:
                    raise ProfileConfirmationError(
                        "Every knowledge point requires an enabled test."
                    )

            content = {
                "schema_version": draft["schema_version"],
                "profile_id": profile_id,
                "version": version,
                "problem_id": draft["problem_id"],
                "title": draft["title"],
                "dimensions": draft["dimensions"],
            }
            if draft["schema_version"] == 2:
                content.update(
                    {
                        "problem_context": draft["problem_context"],
                        "knowledge_points": draft["knowledge_points"],
                        "assessment_tests": draft["assessment_tests"],
                        "confirmations": draft["confirmations"],
                    }
                )
            stored = {**content, "content_hash": sha256_json(content)}
            projection = {
                "profile_id": profile_id,
                "version": version,
                "content_hash": stored["content_hash"],
                "deployment_status": "pilot",
                "preview_status": "pending_real_samples",
            }
            published = {
                **stored,
                "deployment_status": projection["deployment_status"],
                "preview_status": projection["preview_status"],
            }
            validate_schema(
                f"profile-version-v{draft['schema_version']}",
                published,
            )
            atomic_write_json(version_path, stored)
            self._append_projection(projection)
            return published

    def _append_projection(self, projection: Mapping[str, object]) -> None:
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self._audit_path,
            os.O_RDWR | os.O_CREAT | os.O_APPEND,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            size = os.lseek(descriptor, 0, os.SEEK_END)
            if size:
                os.lseek(descriptor, 0, os.SEEK_SET)
                existing = bytearray()
                while len(existing) < size:
                    chunk = os.read(descriptor, min(65536, size - len(existing)))
                    if not chunk:
                        break
                    existing.extend(chunk)
                if existing and not existing.endswith(b"\n"):
                    last_complete_line = existing.rfind(b"\n") + 1
                    tail = existing[last_complete_line:]
                    try:
                        json.loads(tail)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        os.ftruncate(descriptor, last_complete_line)
                    else:
                        _write_all(descriptor, b"\n")
            _write_all(descriptor, canonical_json_bytes(projection) + b"\n")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _projection(self, profile_id: str, version: int) -> dict[str, object]:
        if not self._audit_path.is_file():
            raise ProfileIntegrityError("Deployment projection audit is missing.")
        match = None
        audit_bytes = self._audit_path.read_bytes()
        for raw_line in audit_bytes.splitlines():
            try:
                projection = json.loads(raw_line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(projection, dict):
                continue
            if (
                projection.get("profile_id") == profile_id
                and projection.get("version") == version
            ):
                match = projection
        if match is None:
            raise ProfileIntegrityError(
                f"Deployment projection is missing for profile v{version}."
            )
        return match

    @staticmethod
    def _validate_stored_version(
        stored: Mapping[str, object], profile_id: str, version: int
    ) -> None:
        schema_version = stored.get("schema_version")
        expected_keys = (
            _STORED_VERSION_V1_KEYS
            if schema_version == 1
            else _STORED_VERSION_V2_KEYS
            if schema_version == 2
            else None
        )
        if expected_keys is None or set(stored) != expected_keys:
            raise ProfileIntegrityError(
                "Immutable profile version has unexpected or missing fields."
            )
        if stored["profile_id"] != profile_id:
            raise ProfileIntegrityError("Stored profile_id does not match its path.")
        if stored["version"] != version:
            raise ProfileIntegrityError("Stored version does not match its path.")
        hash_input = {
            key: value for key, value in stored.items() if key != "content_hash"
        }
        try:
            expected_hash = sha256_json(hash_input)
        except (TypeError, ValueError) as error:
            raise ProfileIntegrityError(
                "Immutable profile content is not canonical JSON."
            ) from error
        if stored["content_hash"] != expected_hash:
            raise ProfileIntegrityError("Immutable profile content hash does not match.")

    @staticmethod
    def _validate_projection(
        projection: Mapping[str, object],
        stored: Mapping[str, object],
    ) -> None:
        if set(projection) != _PROJECTION_KEYS:
            raise ProfileIntegrityError(
                "Deployment projection has unexpected or missing fields."
            )
        for field in ("profile_id", "version", "content_hash"):
            if projection[field] != stored[field]:
                raise ProfileIntegrityError(
                    f"Deployment projection {field} does not match immutable content."
                )

    def list_profiles(
        self, problem_id: str | None = None
    ) -> list[dict[str, object]]:
        with self._lock:
            if not self._profiles_root.is_dir():
                return []
            profiles = []
            for profile_dir in self._profiles_root.iterdir():
                if not profile_dir.is_dir():
                    continue
                try:
                    profile_id = _canonical_profile_id(profile_dir.name)
                except InvalidProfileIdError:
                    continue
                version = self._next_version(profile_dir) - 1
                if version < 1:
                    continue
                profile = self.get_version(profile_id, version)
                if problem_id is None or profile["problem_id"] == problem_id:
                    profiles.append(profile)
            return sorted(
                profiles,
                key=lambda item: (
                    item["problem_id"],
                    item["title"],
                    item["profile_id"],
                ),
            )

    def get_version(
        self, profile_id: str, version: int
    ) -> dict[str, object]:
        profile_dir = self._profile_dir(profile_id)
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError("version must be a positive integer.")
        with self._lock:
            try:
                stored = self._read_json(profile_dir / f"v{version}.json")
            except (json.JSONDecodeError, ValueError) as error:
                raise ProfileIntegrityError(
                    "Immutable profile version is not a valid JSON object."
                ) from error
            self._validate_stored_version(stored, profile_id, version)
            projection = self._projection(profile_id, version)
            self._validate_projection(projection, stored)
            published = {
                **stored,
                "deployment_status": projection["deployment_status"],
                "preview_status": projection["preview_status"],
            }
            try:
                validate_schema(
                    f"profile-version-v{stored['schema_version']}",
                    published,
                )
            except ValidationError as error:
                raise ProfileIntegrityError(
                    "Stored profile version violates the frozen profile schema."
                ) from error
            return published

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        if not path.is_file():
            raise KeyError(path.name)
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Expected a JSON object in {path.name}.")
        return value
