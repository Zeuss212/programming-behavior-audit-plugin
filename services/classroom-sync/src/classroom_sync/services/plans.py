"""Draft editing and immutable publication of classroom execution plans."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.canonical import sha256_json
from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    UpstreamUnavailableError,
    ValidationError,
)
from classroom_sync.models import (
    AuditEvent,
    PlanAuthoringSession,
    PlanDraft,
    PlanSeries,
    PlanVersion,
)
from classroom_sync.repositories import ClassroomRepository
from classroom_sync.services.assessment_materials import AssessmentMaterialBundle
from classroom_sync.services.publication_gate import PublicationGate


@dataclass(frozen=True)
class PlanDraftInput:
    space_id: str
    parent_algorithm_id: str
    title: str
    profile: dict[str, object]
    scheduled_start_at: datetime
    scheduled_end_at: datetime
    ai_policy: str
    authoring_session_id: str | None = None


class PlanService:
    """Create mutable drafts, then publish hash-addressed immutable plan versions."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        schema_registry: ClassroomSchemaRegistry,
        *,
        clock: Callable[[], datetime],
        publication_gate: PublicationGate | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._schema_registry = schema_registry
        self._clock = clock
        self._publication_gate = publication_gate or PublicationGate()

    def create_draft(self, draft_input: PlanDraftInput, *, teacher_id: str) -> PlanDraft:
        now = self._clock()
        draft = PlanDraft(
            id=str(uuid4()),
            authoring_session_id=draft_input.authoring_session_id,
            profile_id=str(uuid4()),
            space_id=draft_input.space_id,
            parent_algorithm_id=draft_input.parent_algorithm_id,
            title=draft_input.title,
            profile=draft_input.profile,
            scheduled_start_at=self._utc_schedule(draft_input.scheduled_start_at),
            scheduled_end_at=self._utc_schedule(draft_input.scheduled_end_at),
            ai_policy=draft_input.ai_policy,
            revision=0,
            published_revision=None,
            teacher_id=teacher_id,
            created_at=now,
            updated_at=now,
        )
        self._validate_draft(draft)
        with self._session_factory.begin() as session:
            repository = ClassroomRepository(session)
            if draft_input.authoring_session_id is not None:
                authoring = repository.get_authoring_session(
                    draft_input.authoring_session_id,
                    for_update=True,
                )
                self._validate_authoring_for_draft(
                    repository,
                    authoring,
                    draft=draft,
                    teacher_id=teacher_id,
                )
                binding = repository.get_binding(
                    draft.space_id,
                    draft.parent_algorithm_id,
                )
                if binding is not None:
                    series = repository.get_plan_series(binding.plan_id, for_update=True)
                    if series is None:
                        raise ConflictError("plan_series_not_found")
                    if (
                        series.space_id != draft.space_id
                        or series.parent_algorithm_id != draft.parent_algorithm_id
                    ):
                        raise ConflictError("plan_series_scope_mismatch")
                else:
                    series = PlanSeries(
                        id=draft.id,
                        profile_id=draft.profile_id,
                        space_id=draft.space_id,
                        parent_algorithm_id=draft.parent_algorithm_id,
                        latest_version=0,
                    )
                    session.add(series)
            else:
                series = PlanSeries(
                    id=draft.id,
                    profile_id=draft.profile_id,
                    space_id=draft.space_id,
                    parent_algorithm_id=draft.parent_algorithm_id,
                    latest_version=0,
                )
                session.add(series)
            draft.plan_id = series.id
            draft.profile_id = series.profile_id
            session.add(draft)
            session.flush()
            self._audit(session, teacher_id, "plan_draft_created", "plan_draft", draft.id, now)
        return draft

    def update_draft(
        self,
        draft_id: str,
        *,
        profile: dict[str, object],
        teacher_id: str,
        title: str | None = None,
        scheduled_start_at: datetime | None = None,
        scheduled_end_at: datetime | None = None,
        ai_policy: str | None = None,
        expected_revision: int | None = None,
    ) -> PlanDraft:
        now = self._clock()
        with self._session_factory.begin() as session:
            repository = ClassroomRepository(session)
            draft = repository.get_plan_draft(draft_id, for_update=True)
            if draft is None:
                raise NotFoundError("plan_draft_not_found")
            if draft.teacher_id != teacher_id:
                raise AuthorizationError("plan_draft_owner_mismatch")
            if expected_revision is not None and draft.revision != expected_revision:
                raise ConflictError("plan_draft_revision_conflict")
            if title is not None:
                draft.title = title
            draft.profile = profile
            if scheduled_start_at is not None:
                draft.scheduled_start_at = self._utc_schedule(scheduled_start_at)
            if scheduled_end_at is not None:
                draft.scheduled_end_at = self._utc_schedule(scheduled_end_at)
            if ai_policy is not None:
                draft.ai_policy = ai_policy
            draft.revision += 1
            draft.updated_at = now
            self._validate_draft(draft)
            self._audit(session, teacher_id, "plan_draft_updated", "plan_draft", draft.id, now)
        return draft

    def get_draft(
        self,
        draft_id: str,
        *,
        teacher_id: str | None = None,
    ) -> PlanDraft:
        """Read a draft for router-side ownership verification."""

        with self._session_factory() as session:
            draft = ClassroomRepository(session).get_plan_draft(draft_id)
            if draft is None:
                raise NotFoundError("plan_draft_not_found")
            if teacher_id is not None and draft.teacher_id != teacher_id:
                raise AuthorizationError("plan_draft_owner_mismatch")
            return draft

    def publish_draft(
        self,
        draft_id: str,
        *,
        teacher_id: str,
        materials: AssessmentMaterialBundle | None = None,
    ) -> PlanVersion:
        now = self._clock()
        with self._session_factory.begin() as session:
            repository = ClassroomRepository(session)
            draft = repository.get_plan_draft(draft_id, for_update=True)
            if draft is None:
                raise NotFoundError("plan_draft_not_found")
            if draft.teacher_id != teacher_id:
                raise AuthorizationError("plan_draft_owner_mismatch")

            is_v3 = draft.profile.get("schema_version") == 3
            authoring: PlanAuthoringSession | None = None
            if is_v3:
                if draft.authoring_session_id is None:
                    raise ConflictError("plan_authoring_session_required")
                authoring = repository.get_authoring_session(
                    draft.authoring_session_id,
                    for_update=True,
                )
                self._validate_authoring_for_publish(
                    authoring,
                    draft=draft,
                    teacher_id=teacher_id,
                )

            series = repository.get_plan_series(draft.plan_id, for_update=True)
            if series is None:
                raise ConflictError("plan_series_not_found")
            existing = repository.get_plan_version_for_source(draft.id, draft.revision)
            if existing is not None:
                if authoring is not None and authoring.status == "open":
                    self._close_authoring(authoring, draft=draft, now=now)
                return existing

            if authoring is not None and authoring.status != "open":
                raise ConflictError("plan_authoring_session_closed")

            if is_v3:
                if materials is None:
                    raise UpstreamUnavailableError(
                        "assessment_materials_not_configured",
                        retryable=False,
                    )
                if (
                    materials.space_id != draft.space_id
                    or materials.parent_algorithm_id != draft.parent_algorithm_id
                ):
                    raise UpstreamUnavailableError(
                        "assessment_materials_scope_invalid",
                        retryable=False,
                    )
                self._publication_gate.require_ready(draft.profile, materials)

            version = series.latest_version + 1
            profile_content = {
                **draft.profile,
                "profile_id": draft.profile_id,
                "version": version,
            }
            profile_hash = sha256_json(profile_content)
            published_profile = {
                **profile_content,
                "content_hash": profile_hash,
                "deployment_status": "pilot",
                "preview_status": "pending_real_samples",
            }
            scheduled_start_at = self._utc_storage_instant(
                draft.scheduled_start_at
            )
            scheduled_end_at = self._utc_storage_instant(draft.scheduled_end_at)
            plan_content = {
                "schema_version": 1,
                "plan_id": draft.plan_id,
                "version": version,
                "space_id": draft.space_id,
                "parent_algorithm_id": draft.parent_algorithm_id,
                "profile": published_profile,
                "scheduled_start_at": self._utc_rfc3339(scheduled_start_at),
                "scheduled_end_at": self._utc_rfc3339(scheduled_end_at),
                "ai_policy": draft.ai_policy,
                "published_at": now.isoformat(),
            }
            content_hash = sha256_json(plan_content)
            published_contract = {**plan_content, "content_hash": content_hash}
            self._schema_registry.validate("plan-version", published_contract)

            plan_version = PlanVersion(
                id=str(uuid4()),
                plan_id=draft.plan_id,
                profile_id=draft.profile_id,
                version=version,
                source_draft_id=draft.id,
                source_draft_revision=draft.revision,
                space_id=draft.space_id,
                parent_algorithm_id=draft.parent_algorithm_id,
                profile=published_profile,
                content_hash=content_hash,
                scheduled_start_at=scheduled_start_at,
                scheduled_end_at=scheduled_end_at,
                ai_policy=draft.ai_policy,
                published_at=now,
                teacher_id=teacher_id,
            )
            session.add(plan_version)
            series.latest_version = version
            session.flush()
            draft.published_revision = draft.revision
            if authoring is not None:
                self._close_authoring(authoring, draft=draft, now=now)
            self._audit(session, teacher_id, "plan_published", "plan_version", plan_version.id, now)
        return plan_version

    def get_plan_version(self, plan_version_id: str) -> PlanVersion:
        """Read one immutable version for assignment synchronization."""

        with self._session_factory() as session:
            plan_version = session.get(PlanVersion, plan_version_id)
            if plan_version is None:
                raise NotFoundError("plan_version_not_found")
            return plan_version

    def _validate_draft(self, draft: PlanDraft) -> None:
        self._schema_registry.validate(
            "plan-draft",
            {
                "schema_version": 1,
                "draft_id": draft.id,
                "space_id": draft.space_id,
                "parent_algorithm_id": draft.parent_algorithm_id,
                "title": draft.title,
                "profile": draft.profile,
                "scheduled_start_at": draft.scheduled_start_at.isoformat(),
                "scheduled_end_at": draft.scheduled_end_at.isoformat(),
                "ai_policy": draft.ai_policy,
                "revision": draft.revision,
                "updated_at": draft.updated_at.isoformat(),
            },
        )

    @staticmethod
    def _utc_schedule(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError("plan_schedule_timezone_required")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _utc_storage_instant(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _utc_rfc3339(cls, value: datetime) -> str:
        return cls._utc_storage_instant(value).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _validate_authoring_for_draft(
        repository: ClassroomRepository,
        authoring: PlanAuthoringSession | None,
        *,
        draft: PlanDraft,
        teacher_id: str,
    ) -> None:
        if authoring is None:
            raise NotFoundError("plan_authoring_session_not_found")
        if authoring.teacher_id != teacher_id:
            raise AuthorizationError("plan_authoring_session_not_owned")
        if authoring.status != "open" or authoring.active_slot != 1:
            raise ConflictError("plan_authoring_session_closed")
        if (
            authoring.space_id != draft.space_id
            or authoring.parent_algorithm_id != draft.parent_algorithm_id
        ):
            raise ConflictError("plan_authoring_session_scope_mismatch")
        if repository.get_plan_draft_for_authoring_session(authoring.id) is not None:
            raise ConflictError("plan_authoring_draft_exists")

    @staticmethod
    def _validate_authoring_for_publish(
        authoring: PlanAuthoringSession | None,
        *,
        draft: PlanDraft,
        teacher_id: str,
    ) -> None:
        if authoring is None:
            raise NotFoundError("plan_authoring_session_not_found")
        if authoring.teacher_id != teacher_id:
            raise AuthorizationError("plan_authoring_session_not_owned")
        if (
            authoring.space_id != draft.space_id
            or authoring.parent_algorithm_id != draft.parent_algorithm_id
        ):
            raise ConflictError("plan_authoring_session_scope_mismatch")
        if authoring.status not in {"open", "published"}:
            raise ConflictError("plan_authoring_session_closed")
        if (
            authoring.status == "published"
            and authoring.published_plan_id != draft.plan_id
        ):
            raise ConflictError("plan_authoring_session_publish_mismatch")

    @staticmethod
    def _close_authoring(
        authoring: PlanAuthoringSession,
        *,
        draft: PlanDraft,
        now: datetime,
    ) -> None:
        authoring.status = "published"
        authoring.active_slot = None
        authoring.published_plan_id = draft.plan_id
        authoring.closed_at = now
        authoring.updated_at = now

    @staticmethod
    def _audit(
        session: Session,
        actor_id: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        created_at: datetime,
    ) -> None:
        session.add(
            AuditEvent(
                id=str(uuid4()),
                actor_id=actor_id,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                request_id=None,
                payload={},
                created_at=created_at,
            )
        )
