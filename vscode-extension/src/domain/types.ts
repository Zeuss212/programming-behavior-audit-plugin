export const PLAN_SCHEMA_VERSION = 1 as const;
export const AUDIT_EVENT_SCHEMA_VERSION = 1 as const;
export const SESSION_SCHEMA_VERSION = 1 as const;
export const LEGACY_CLASSROOM_BRIEF_SCHEMA_VERSION = 1 as const;
export const CLASSROOM_BRIEF_SCHEMA_VERSION = 2 as const;
export const EXPORT_MANIFEST_SCHEMA_VERSION = 1 as const;
export const ANALYSIS_LOG_SCHEMA_VERSION = 1 as const;

export type JsonPrimitive = boolean | number | string | null;
export type JsonValue = JsonPrimitive | JsonObject | readonly JsonValue[];
export interface JsonObject {
  readonly [key: string]: JsonValue;
}

export const SESSION_STATUSES = [
  'collecting',
  'interrupted',
  'finalizing',
  'completed',
  'partial',
  'abandoned',
] as const;

export type SessionStatus = (typeof SESSION_STATUSES)[number];

export const AUDIT_EVENT_KINDS = [
  'edit',
  'paste_shortcut',
  'save',
  'document_focus',
  'window_focus',
  'python_run',
  'notebook_edit',
  'notebook_run',
  'external_terminal_activity',
] as const;

export type AuditEventKind = (typeof AUDIT_EVENT_KINDS)[number];

export interface KnowledgePoint {
  readonly knowledge_point_id: string;
  readonly name: string;
  readonly description: string;
  readonly observation_basis: string;
}

export interface TestDraft {
  readonly test_id: string;
  readonly title: string;
  readonly description: string;
  readonly expected_behavior: string;
}

export interface PublishedPlan {
  readonly schema_version: typeof PLAN_SCHEMA_VERSION;
  readonly plan_id: string;
  readonly version: number;
  readonly problem_text: string;
  readonly knowledge_points: readonly KnowledgePoint[];
  readonly tests: readonly TestDraft[];
  readonly published_at: string;
  readonly content_sha256: string;
}

export interface PublishPlanInput {
  readonly plan_id?: string;
  readonly problem_text: string;
  readonly knowledge_points: readonly KnowledgePoint[];
  readonly tests: readonly TestDraft[];
}

export interface DocumentRef {
  readonly relative_uri: string;
  readonly language_id: string;
  readonly notebook_cell_id?: string;
}

export interface AuditEvent {
  readonly schema_version: typeof AUDIT_EVENT_SCHEMA_VERSION;
  readonly event_id: string;
  readonly session_id: string;
  readonly session_seq: number;
  readonly occurred_at: string;
  readonly monotonic_ms: number;
  readonly kind: AuditEventKind;
  readonly document?: DocumentRef;
  readonly payload: JsonObject;
}

export interface PythonRunResult {
  readonly exitCode: number | null;
  readonly signal: NodeJS.Signals | null;
  readonly durationMs: number;
  readonly stdout: string;
  readonly stderr: string;
  readonly stdoutTruncated: boolean;
  readonly stderrTruncated: boolean;
}

export interface SessionState {
  readonly schema_version: typeof SESSION_SCHEMA_VERSION;
  readonly session_id: string;
  readonly workspace_id: string;
  readonly status: SessionStatus;
  readonly plan_id: string;
  readonly plan_version: number;
  readonly plan_content_sha256: string;
  readonly started_at: string;
  readonly updated_at: string;
  readonly last_event_seq: number;
  readonly last_persisted_seq: number;
  readonly last_flushed_at?: string;
  readonly ended_at?: string;
  readonly status_reason?: string;
}

export interface EvidenceSummaryItem {
  readonly occurred_at: string;
  readonly kind: AuditEventKind;
  readonly summary: string;
}

export type TeacherPerformanceGrade = 'S' | 'A' | 'B' | 'C' | 'D';
export type EvidenceConfidence = 'high' | 'medium' | 'low';
export type ClassroomFocusReference =
  | 'stable'
  | 'fluctuating'
  | 'frequent_switching'
  | 'insufficient';

export interface TeacherEvaluationDimension {
  readonly name: '运行验证' | '调试与修正' | '任务推进';
  readonly score: number;
  readonly maximum_score: number;
  readonly evidence_event_ids: readonly string[];
}

export interface TeacherEvaluation {
  readonly label: '课题实践表现';
  readonly overall_grade: TeacherPerformanceGrade;
  readonly evidence_confidence: EvidenceConfidence;
  readonly summary: string;
  readonly dimensions: readonly TeacherEvaluationDimension[];
  readonly classroom_focus: {
    readonly reference: ClassroomFocusReference;
    readonly focus_loss_count: number;
    readonly focus_loss_milliseconds: number;
    readonly longest_focus_loss_milliseconds: number;
    readonly unclosed_focus_loss: boolean;
    readonly note: string;
  };
  readonly metrics: {
    readonly edit_count: number;
    readonly save_count: number;
    readonly run_count: number;
    readonly determinate_run_count: number;
    readonly successful_run_count: number;
    readonly failed_run_count: number;
    readonly unknown_run_count: number;
    readonly execution_success_rate: number | null;
    readonly recovery_success_count: number;
    readonly complete_work_cycle_count: number;
  };
  readonly teaching_suggestion: string;
  readonly limitations: string;
}

interface ClassroomBriefBase {
  readonly session_id: string;
  readonly generated_at: string;
  readonly session_result: {
    readonly status: Extract<SessionStatus, 'completed' | 'partial' | 'abandoned'>;
    readonly reason?: string;
  };
  readonly effective_observation: {
    readonly milliseconds: number;
    readonly method: 'focused_event_gaps_capped_at_30_seconds';
  };
  readonly run_statistics: {
    readonly total: number;
    readonly success: number;
    readonly failure: number;
    readonly unknown: number;
  };
  readonly evidence_summary: readonly EvidenceSummaryItem[];
  readonly attention_point: string | null;
}

export interface ClassroomBriefV1 extends ClassroomBriefBase {
  readonly schema_version: typeof LEGACY_CLASSROOM_BRIEF_SCHEMA_VERSION;
}

export interface ClassroomBriefV2 extends ClassroomBriefBase {
  readonly schema_version: typeof CLASSROOM_BRIEF_SCHEMA_VERSION;
  readonly teacher_evaluation: TeacherEvaluation;
}

export type ClassroomBrief = ClassroomBriefV1 | ClassroomBriefV2;

export interface ExportManifestFile {
  readonly path: string;
  readonly bytes: number;
  readonly sha256: string;
}

export interface ExportManifest {
  readonly schema_version: typeof EXPORT_MANIFEST_SCHEMA_VERSION;
  readonly extension_version: string;
  readonly session_id: string;
  readonly exported_at: string;
  readonly files: readonly ExportManifestFile[];
}

export const ANALYSIS_LOG_STATUSES = ['completed', 'skipped', 'failed'] as const;
export type AnalysisLogStatus = (typeof ANALYSIS_LOG_STATUSES)[number];

export const ANALYSIS_LOG_REASON_CODES = [
  'disabled_by_student',
  'ai_not_configured',
  'ai_provider_request_rejected',
  'ai_provider_timeout',
  'ai_provider_network_error',
  'ai_provider_auth_failed',
  'ai_provider_rate_limited',
  'ai_provider_unavailable',
  'ai_response_truncated',
  'ai_response_invalid',
  'analysis_unavailable',
] as const;
export type AnalysisLogReasonCode = (typeof ANALYSIS_LOG_REASON_CODES)[number];

export interface AnalysisLog {
  readonly schema_version: typeof ANALYSIS_LOG_SCHEMA_VERSION;
  readonly session_id: string;
  readonly generated_at: string;
  readonly status: AnalysisLogStatus;
  readonly analysis?: JsonObject;
  readonly reason?: Readonly<{
    code: AnalysisLogReasonCode;
    message: string;
  }>;
}
