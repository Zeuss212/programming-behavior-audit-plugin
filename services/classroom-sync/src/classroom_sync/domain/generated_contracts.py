"""Generated from contracts/classroom/v1/common.schema.json; do not edit."""

from enum import Enum


class AssignmentStatus(str, Enum):
    PENDING_ACCEPTANCE = "pending_acceptance"
    READY = "ready"
    ACTIVE = "active"
    SUBMITTED = "submitted"


class SessionStatus(str, Enum):
    COLLECTING = "collecting"
    TEMPORARILY_OFFLINE = "temporarily_offline"
    SUBMITTING = "submitting"
    PENDING_UPLOAD = "pending_upload"
    COMPLETED = "completed"
    PARTIAL = "partial"


class SubmissionReason(str, Enum):
    STUDENT_MANUAL = "student_manual"
    TEACHER_ENDED = "teacher_ended"
    SYSTEM_DEADLINE = "system_deadline"


class MasteryStatus(str, Enum):
    MASTERED = "mastered"
    PARTIAL = "partial"
    NOT_DEMONSTRATED = "not_demonstrated"
    REVIEW_REQUIRED = "review_required"
