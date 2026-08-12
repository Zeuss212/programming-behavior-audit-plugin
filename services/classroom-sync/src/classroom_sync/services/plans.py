"""Draft editing and immutable publication of classroom execution plans."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.canonical import sha256_json
from classroom_sync.domain.schemas import ClassroomSchemaRegistry
from classroom_sync.errors import AuthorizationError, NotFoundError
from classroom_sync.models import AuditEvent, PlanDraft, PlanVersion
from classroom_sync.repositories import ClassroomRepository


@dataclass(frozen=True)
class PlanDraftInput:
    space_id: str
    parent_algorithm_id: str
    title: str
    profile: dict[str, object]
    scheduled_start_at: datetime
    scheduled_end_at: datetime
    ai_policy: str


class PlanService:
    """Create mutable drafts, then publish hash-addressed immutable plan versions."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        schema_registry: ClassroomSchemaRegistry,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._session_factory = session_factory
        self._schema_registry = schema_registry
        self._clock = clock

    def create_draft(self, draft_input: PlanDraftInput, *, teacher_id: str) -> PlanDraft:
        now = self._clock()
        draft = PlanDraft(
            id=str(uuid4()),
            profile_id=str(uuid4()),
            space_id=draft_input.space_id,
            parent_algorithm_id=draft_input.parent_algorithm_id,
            title=draft_input.title,
            profile=draft_input.profile,
            scheduled_start_at=draft_input.scheduled_start_at,
            scheduled_end_at=draft_input.scheduled_end_at,
            ai_policy=draft_input.ai_policy,
            revision=0,
            published_revision=None,
            teacher_id=teacher_id,
            created_at=now,
            updated_at=now,
        )
        self._validate_draft(draft)
        with self._session_factory.begin() as session:
            session.add(draft)
            self._audit(session, teacher_id, "plan_draft_created", "plan_draft", draft.id, now)
        return draft

    def update_draft(
        self,
        draft_id: str,
        *,
        profile: dict[str, object],
        teacher_id: str,
    ) -> PlanDraft:
        now = self._clock()
        with self._session_factory.begin() as session:
            repository = ClassroomRepository(session)
            draft = repository.get_plan_draft(draft_id, for_update=True)
            if draft is None:
                raise NotFoundError("plan_draft_not_found")
            if draft.teacher_id != teacher_id:
                raise AuthorizationError("plan_draft_owner_mismatch")
            draft.profile = profile
            draft.revision += 1
            draft.updated_at = now
            self._validate_draft(draft)
            self._audit(session, teacher_id, "plan_draft_updated", "plan_draft", draft.id, now)
        return draft

    def get_draft(self, draft_id: str) -> PlanDraft:
        """Read a draft for router-side ownership verification."""

        with self._session_factory() as session:
            draft = ClassroomRepository(session).get_plan_draft(draft_id)
            if draft is None:
                raise NotFoundError("plan_draft_not_found")
            return draft

    def publish_draft(self, draft_id: str, *, teacher_id: str) -> PlanVersion:
        now = self._clock()
        with self._session_factory.begin() as session:
            repository = ClassroomRepository(session)
            draft = repository.get_plan_draft(draft_id, for_update=True)
            if draft is None:
                raise NotFoundError("plan_draft_not_found")
            if draft.teacher_id != teacher_id:
                raise AuthorizationError("plan_draft_owner_mismatch")

            latest = repository.latest_plan_version(draft.id)
            if latest is not None and latest.source_draft_revision == draft.revision:
                return latest

            version = 1 if latest is None else latest.version + 1
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
            plan_content = {
                "schema_version": 1,
                "plan_id": draft.id,
                "version": version,
                "space_id": draft.space_id,
                "parent_algorithm_id": draft.parent_algorithm_id,
                "profile": published_profile,
                "scheduled_start_at": draft.scheduled_start_at.isoformat(),
                "scheduled_end_at": draft.scheduled_end_at.isoformat(),
                "ai_policy": draft.ai_policy,
                "published_at": now.isoformat(),
            }
            content_hash = sha256_json(plan_content)
            published_contract = {**plan_content, "content_hash": content_hash}
            self._schema_registry.validate("plan-version", published_contract)

            plan_version = PlanVersion(
                id=str(uuid4()),
                plan_id=draft.id,
                profile_id=draft.profile_id,
                version=version,
                source_draft_revision=draft.revision,
                space_id=draft.space_id,
                parent_algorithm_id=draft.parent_algorithm_id,
                profile=published_profile,
                content_hash=content_hash,
                scheduled_start_at=draft.scheduled_start_at,
                scheduled_end_at=draft.scheduled_end_at,
                ai_policy=draft.ai_policy,
                published_at=now,
                teacher_id=teacher_id,
            )
            session.add(plan_version)
            draft.published_revision = draft.revision
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
