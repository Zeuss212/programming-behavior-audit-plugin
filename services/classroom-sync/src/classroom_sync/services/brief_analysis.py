"""Bounded server-side teaching analysis for already-sanitized classroom briefs."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Annotated, Protocol

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from classroom_sync.errors import UpstreamUnavailableError
from classroom_sync.models import ClassroomBriefAnalysisJob, StudentBrief
from classroom_sync.services.plan_suggestions import OpenAiCompletionClient

if TYPE_CHECKING:
    from classroom_sync.services.briefs import BriefService

AnalysisItem = Annotated[str, Field(min_length=1, max_length=500)]


@dataclass(frozen=True)
class BriefAnalysisInput:
    """The allowlisted teaching text that can leave the classroom service."""

    summary: str
    knowledge_points: tuple[dict[str, str], ...]
    process_overview: tuple[str, ...]
    issues: tuple[str, ...]

    @classmethod
    def from_brief_payload(cls, payload: dict[str, object]) -> BriefAnalysisInput:
        raw_points = payload.get("knowledge_points")
        knowledge_points = (
            tuple(cls._knowledge_point(point) for point in raw_points if isinstance(point, dict))
            if isinstance(raw_points, list)
            else ()
        )
        return cls(
            summary=cls._text(payload.get("summary")),
            knowledge_points=knowledge_points,
            process_overview=cls._text_items(payload.get("process_overview")),
            issues=cls._text_items(payload.get("issues")),
        )

    @staticmethod
    def _knowledge_point(point: dict[object, object]) -> dict[str, str]:
        return {
            name: BriefAnalysisInput._text(point.get(name))
            for name in ("name", "status", "demonstrated", "gap", "teacher_suggestion")
        }

    @staticmethod
    def _text(value: object) -> str:
        return value if isinstance(value, str) else ""

    @staticmethod
    def _text_items(value: object) -> tuple[str, ...]:
        if not isinstance(value, list):
            return ()
        return tuple(item for item in value if isinstance(item, str))


class BriefAiAnalysis(BaseModel):
    """Strict provider result saved to a teacher-only brief revision."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    learning_overview: Annotated[str, Field(min_length=1, max_length=1000)]
    evidence_based_observations: list[AnalysisItem] = Field(min_length=1, max_length=5)
    teaching_suggestions: list[AnalysisItem] = Field(min_length=1, max_length=5)


class OpenAiBriefAnalysisService:
    """Create auxiliary teaching analysis through one bounded provider call."""

    def __init__(self, completion_client: OpenAiCompletionClient) -> None:
        self._completion_client = completion_client

    def generate(self, source: BriefAnalysisInput) -> BriefAiAnalysis:
        try:
            content = self._completion_client.complete(
                self.messages_for(source), temperature=0.2, max_tokens=1200
            )
            return BriefAiAnalysis.model_validate_json(self._strip_optional_json_fence(content))
        except (PydanticValidationError, ValueError, UpstreamUnavailableError) as error:
            raise UpstreamUnavailableError("ai_brief_analysis_upstream_unavailable") from error

    @staticmethod
    def messages_for(source: BriefAnalysisInput) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "你是课堂教学分析助手。只返回一个 JSON 对象，不要 Markdown。"
                    "对象必须含 learning_overview、evidence_based_observations、"
                    "teaching_suggestions。分析只依据提供的课堂简报摘要，"
                    "不得推断个人属性，不得自动评分或给出纪律结论。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "summary": source.summary,
                        "knowledge_points": list(source.knowledge_points),
                        "process_overview": list(source.process_overview),
                        "issues": list(source.issues),
                    },
                    ensure_ascii=False,
                ),
            },
        ]

    @staticmethod
    def _strip_optional_json_fence(content: str) -> str:
        value = content.strip()
        if not value.startswith("```"):
            return value
        lines = value.splitlines()
        if len(lines) < 3 or not lines[0].lower().startswith("```json") or lines[-1] != "```":
            raise ValueError("provider fenced response is invalid")
        return "\n".join(lines[1:-1]).strip()


class BriefAnalysisGenerator(Protocol):
    """The one bounded provider capability required by the durable worker."""

    def generate(self, source: BriefAnalysisInput) -> BriefAiAnalysis: ...


class BriefAnalysisJobService:
    """Lease, execute, and retry brief analysis without blocking student submission."""

    _MAX_ATTEMPTS = 3
    _RETRY_DELAYS = (timedelta(seconds=5), timedelta(seconds=30))
    _LEASE_SECONDS = 60

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        brief_service: BriefService,
        analysis_service: BriefAnalysisGenerator,
        *,
        clock: Callable[[], datetime],
    ) -> None:
        self._session_factory = session_factory
        self._brief_service = brief_service
        self._analysis_service = analysis_service
        self._clock = clock

    def claim_due_jobs(
        self, worker_id: str, now: datetime | None = None
    ) -> tuple[ClassroomBriefAnalysisJob, ...]:
        """Lease all due jobs once; a new worker may reclaim an expired lease."""

        claim_time = self._utc_now() if now is None else self._as_utc(now)
        with self._session_factory.begin() as session:
            jobs = list(
                session.scalars(
                    select(ClassroomBriefAnalysisJob)
                    .where(
                        ClassroomBriefAnalysisJob.run_at <= claim_time,
                        ClassroomBriefAnalysisJob.status != "completed",
                        or_(
                            ClassroomBriefAnalysisJob.lease_expires_at.is_(None),
                            ClassroomBriefAnalysisJob.lease_expires_at <= claim_time,
                        ),
                    )
                    .order_by(ClassroomBriefAnalysisJob.run_at, ClassroomBriefAnalysisJob.id)
                    .with_for_update(skip_locked=True)
                )
            )
            for job in jobs:
                job.status = "leased"
                job.lease_owner = worker_id
                job.lease_expires_at = claim_time + timedelta(seconds=self._LEASE_SECONDS)
                job.attempts += 1
                job.updated_at = claim_time
        return tuple(jobs)

    def run_due_jobs(self, worker_id: str) -> int:
        """Process every currently due job while persisting provider failures safely."""

        jobs = self.claim_due_jobs(worker_id)
        for job in jobs:
            try:
                source = self._source_for_leased_job(job.id, worker_id)
                analysis = self._analysis_service.generate(source)
                self._brief_service.complete_analysis_job(
                    job.id,
                    worker_id=worker_id,
                    analysis=analysis.model_dump(),
                )
            except UpstreamUnavailableError:
                self._brief_service.record_analysis_failure(
                    job.id,
                    worker_id=worker_id,
                    failure_code="ai_brief_analysis_upstream_unavailable",
                    retry_delay=self._retry_delay(job.attempts),
                    occurred_at=self._utc_now(),
                )
        return len(jobs)

    def _source_for_leased_job(self, job_id: str, worker_id: str) -> BriefAnalysisInput:
        with self._session_factory() as session:
            job = session.get(ClassroomBriefAnalysisJob, job_id)
            if job is None or job.status != "leased" or job.lease_owner != worker_id:
                raise UpstreamUnavailableError("ai_brief_analysis_upstream_unavailable")
            source = session.get(StudentBrief, job.source_brief_id)
            if source is None:
                raise UpstreamUnavailableError("ai_brief_analysis_upstream_unavailable")
            return BriefAnalysisInput.from_brief_payload(source.payload)

    def _retry_delay(self, attempts: int) -> timedelta | None:
        if attempts >= self._MAX_ATTEMPTS:
            return None
        return self._RETRY_DELAYS[attempts - 1]

    def _utc_now(self) -> datetime:
        return self._as_utc(self._clock())

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
