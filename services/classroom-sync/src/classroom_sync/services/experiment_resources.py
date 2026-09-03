"""Private teacher-owned files staged against an experiment."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import PurePath
from typing import Literal, cast
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.errors import NotFoundError, UpstreamUnavailableError, ValidationError
from classroom_sync.models import AuditEvent, ExperimentResource
from classroom_sync.repositories import ClassroomRepository
from classroom_sync.storage import PrivateObjectStorage, StorageUnavailable

logger = logging.getLogger(__name__)

ResourceKind = Literal["assignment_material", "code_framework"]
MAX_RESOURCE_BYTES = 10 * 1024 * 1024
MAX_RESOURCE_BYTES_PER_EXPERIMENT = 50 * 1024 * 1024
MAX_RESOURCES_PER_KIND = 20

_TEXT_EXTENSIONS = frozenset({".txt", ".cpp", ".cc", ".cxx", ".h", ".hpp"})
_WORD_EXTENSIONS = frozenset({".doc", ".docx"})
_ALLOWED_EXTENSIONS: dict[ResourceKind, frozenset[str]] = {
    "assignment_material": frozenset({".txt", ".doc", ".docx"}),
    "code_framework": frozenset({".cpp", ".cc", ".cxx", ".h", ".hpp"}),
}
_CONTENT_TYPES = {
    ".txt": "text/plain; charset=utf-8",
    ".cpp": "text/x-c++src; charset=utf-8",
    ".cc": "text/x-c++src; charset=utf-8",
    ".cxx": "text/x-c++src; charset=utf-8",
    ".h": "text/x-c++hdr; charset=utf-8",
    ".hpp": "text/x-c++hdr; charset=utf-8",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_ZIP_MAGIC = b"PK\x03\x04"


@dataclass(frozen=True)
class ExperimentResourceMetadata:
    id: str
    resource_kind: ResourceKind
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    download_only: bool
    created_at: datetime


@dataclass(frozen=True)
class ExperimentResourceDownload:
    resource: ExperimentResourceMetadata
    body: bytes


class ExperimentResourceService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        storage: PrivateObjectStorage,
        clock: Callable[[], datetime],
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._clock = clock

    def upload(
        self,
        *,
        space_id: str,
        parent_algorithm_id: str,
        teacher_id: str,
        resource_kind: ResourceKind,
        filename: str,
        body: bytes,
    ) -> ExperimentResourceMetadata:
        extension, content_type, download_only = self._validate_upload(
            resource_kind=resource_kind,
            filename=filename,
            body=body,
        )
        content_hash = sha256(body).hexdigest()
        now = self._clock()
        resource_id = str(uuid4())
        object_key = f"classroom-resources/{resource_id}/{content_hash}{extension}"
        put_attempted = False
        committed = False
        try:
            with self._session_factory.begin() as session:
                ClassroomRepository(session).lock_plan_scope(space_id, parent_algorithm_id)
                existing = session.scalar(
                    select(ExperimentResource).where(
                        ExperimentResource.space_id == space_id,
                        ExperimentResource.parent_algorithm_id == parent_algorithm_id,
                        ExperimentResource.resource_kind == resource_kind,
                        ExperimentResource.filename == filename,
                        ExperimentResource.content_sha256 == content_hash,
                    )
                )
                if existing is not None:
                    return self._metadata(existing)
                self._require_capacity(
                    session, space_id, parent_algorithm_id, resource_kind, len(body)
                )
                put_attempted = True
                try:
                    self._storage.put_bytes(object_key, body, content_type=content_type)
                except (OSError, StorageUnavailable) as error:
                    raise UpstreamUnavailableError(
                        "experiment_resource_storage_unavailable"
                    ) from error
                resource = ExperimentResource(
                    id=resource_id,
                    space_id=space_id,
                    parent_algorithm_id=parent_algorithm_id,
                    teacher_id=teacher_id,
                    resource_kind=resource_kind,
                    filename=filename,
                    content_type=content_type,
                    content_sha256=content_hash,
                    size_bytes=len(body),
                    download_only=download_only,
                    object_key=object_key,
                    state="draft",
                    created_at=now,
                )
                session.add(resource)
                self._audit(session, teacher_id, "experiment_resource_uploaded", resource.id, now)
                metadata = self._metadata(resource)
            committed = True
            return metadata
        except Exception:
            if put_attempted and not committed:
                self._best_effort_delete(object_key)
            raise

    def list_resources(
        self, space_id: str, parent_algorithm_id: str
    ) -> tuple[ExperimentResourceMetadata, ...]:
        with self._session_factory() as session:
            resources = tuple(
                session.scalars(
                    select(ExperimentResource)
                    .where(
                        ExperimentResource.space_id == space_id,
                        ExperimentResource.parent_algorithm_id == parent_algorithm_id,
                    )
                    .order_by(
                        ExperimentResource.resource_kind,
                        ExperimentResource.created_at,
                        ExperimentResource.filename,
                    )
                )
            )
        return tuple(self._metadata(item) for item in resources)

    def download(
        self, space_id: str, parent_algorithm_id: str, resource_id: str
    ) -> ExperimentResourceDownload:
        with self._session_factory() as session:
            resource = self._get_scoped(session, space_id, parent_algorithm_id, resource_id)
            metadata = self._metadata(resource)
            object_key = resource.object_key
        try:
            stored = self._storage.get_bytes(object_key)
        except (KeyError, OSError, StorageUnavailable) as error:
            raise UpstreamUnavailableError("experiment_resource_storage_unavailable") from error
        if len(stored.body) != metadata.size_bytes or sha256(stored.body).hexdigest() != metadata.sha256:
            raise UpstreamUnavailableError(
                "experiment_resource_storage_invalid", retryable=False
            )
        return ExperimentResourceDownload(resource=metadata, body=stored.body)

    def delete(
        self,
        *,
        space_id: str,
        parent_algorithm_id: str,
        teacher_id: str,
        resource_id: str,
    ) -> None:
        now = self._clock()
        with self._session_factory.begin() as session:
            ClassroomRepository(session).lock_plan_scope(space_id, parent_algorithm_id)
            resource = self._get_scoped(session, space_id, parent_algorithm_id, resource_id)
            object_key = resource.object_key
            session.delete(resource)
            self._audit(session, teacher_id, "experiment_resource_deleted", resource_id, now)
        self._best_effort_delete(object_key)

    @staticmethod
    def _validate_upload(
        *, resource_kind: ResourceKind, filename: str, body: bytes
    ) -> tuple[str, str, bool]:
        if resource_kind not in _ALLOWED_EXTENSIONS:
            raise ValidationError("experiment_resource_kind_invalid")
        if (
            not filename
            or filename != filename.strip()
            or len(filename) > 255
            or filename in {".", ".."}
            or any(value in filename for value in ("/", "\\", "\x00", "\r", "\n"))
        ):
            raise ValidationError("experiment_resource_filename_invalid")
        if not body:
            raise ValidationError("experiment_resource_empty")
        if len(body) > MAX_RESOURCE_BYTES:
            raise ValidationError("experiment_resource_too_large")
        extension = PurePath(filename).suffix.casefold()
        if extension not in _ALLOWED_EXTENSIONS[resource_kind]:
            raise ValidationError("experiment_resource_extension_invalid")
        if extension in _TEXT_EXTENSIONS and b"\x00" in body:
            raise ValidationError("experiment_resource_text_binary")
        if extension == ".doc" and not body.startswith(_OLE_MAGIC):
            raise ValidationError("experiment_resource_word_format_invalid")
        if extension == ".docx" and not body.startswith(_ZIP_MAGIC):
            raise ValidationError("experiment_resource_word_format_invalid")
        return extension, _CONTENT_TYPES[extension], extension in _WORD_EXTENSIONS

    @staticmethod
    def _require_capacity(
        session: Session,
        space_id: str,
        parent_algorithm_id: str,
        resource_kind: ResourceKind,
        new_size: int,
    ) -> None:
        scope = (
            ExperimentResource.space_id == space_id,
            ExperimentResource.parent_algorithm_id == parent_algorithm_id,
        )
        count = session.scalar(
            select(func.count()).select_from(ExperimentResource).where(
                *scope, ExperimentResource.resource_kind == resource_kind
            )
        )
        total = session.scalar(
            select(func.coalesce(func.sum(ExperimentResource.size_bytes), 0)).where(*scope)
        )
        if count is None or total is None:
            raise RuntimeError("experiment_resource_capacity_unavailable")
        if count >= MAX_RESOURCES_PER_KIND:
            raise ValidationError("experiment_resource_kind_limit_exceeded")
        if int(total) + new_size > MAX_RESOURCE_BYTES_PER_EXPERIMENT:
            raise ValidationError("experiment_resource_total_limit_exceeded")

    @staticmethod
    def _get_scoped(
        session: Session, space_id: str, parent_algorithm_id: str, resource_id: str
    ) -> ExperimentResource:
        resource = session.scalar(
            select(ExperimentResource).where(
                ExperimentResource.id == resource_id,
                ExperimentResource.space_id == space_id,
                ExperimentResource.parent_algorithm_id == parent_algorithm_id,
            )
        )
        if resource is None:
            raise NotFoundError("experiment_resource_not_found")
        return resource

    def _best_effort_delete(self, object_key: str) -> None:
        try:
            self._storage.delete_bytes(object_key)
        except (KeyError, OSError, StorageUnavailable):
            logger.warning("experiment_resource_storage_cleanup_failed")

    @staticmethod
    def _metadata(resource: ExperimentResource) -> ExperimentResourceMetadata:
        return ExperimentResourceMetadata(
            id=resource.id,
            resource_kind=cast(ResourceKind, resource.resource_kind),
            filename=resource.filename,
            content_type=resource.content_type,
            size_bytes=resource.size_bytes,
            sha256=resource.content_sha256,
            download_only=resource.download_only,
            created_at=resource.created_at,
        )

    @staticmethod
    def _audit(
        session: Session,
        teacher_id: str,
        event_type: str,
        resource_id: str,
        created_at: datetime,
    ) -> None:
        session.add(
            AuditEvent(
                id=str(uuid4()),
                actor_id=teacher_id,
                event_type=event_type,
                entity_type="experiment_resource",
                entity_id=resource_id,
                request_id=None,
                payload={},
                created_at=created_at,
            )
        )
