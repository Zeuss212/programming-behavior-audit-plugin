export const PLAN_SCHEMA_VERSION = 1 as const;
export const AUDIT_EVENT_SCHEMA_VERSION = 1 as const;
export const SESSION_SCHEMA_VERSION = 1 as const;
export const CLASSROOM_BRIEF_SCHEMA_VERSION = 1 as const;
export const EXPORT_MANIFEST_SCHEMA_VERSION = 1 as const;

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

export interface ClassroomBrief {
  readonly schema_version: typeof CLASSROOM_BRIEF_SCHEMA_VERSION;
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

export interface ExportManifestFile {
  readonly path: string;
  readonly bytes: number;
  readonly sha256: string;
}

export interface ExportManifest {
  readonly schema_version: typeof EXPORT_MANIFEST_SCHEMA_VERSION;
  readonly session_id: string;
  readonly exported_at: string;
  readonly files: readonly ExportManifestFile[];
}
