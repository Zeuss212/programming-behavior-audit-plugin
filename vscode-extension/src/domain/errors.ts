export const AUDIT_ERROR_CODES = [
  'storage_unavailable',
  'storage_write_failed',
  'storage_corrupt',
  'session_conflict',
  'session_recovery_required',
  'session_sequence_invalid',
  'workspace_untrusted',
  'python_interpreter_missing',
  'python_run_failed',
  'ai_not_configured',
  'ai_provider_timeout',
  'ai_provider_network_error',
  'ai_provider_auth_failed',
  'ai_provider_rate_limited',
  'ai_provider_unavailable',
  'ai_response_truncated',
  'ai_response_invalid',
  'export_failed',
  'import_invalid',
  'unsupported_schema_version',
] as const;

export type AuditErrorCode = (typeof AUDIT_ERROR_CODES)[number];

export class AuditError extends Error {
  public readonly code: AuditErrorCode;
  public readonly action: string;

  public constructor(code: AuditErrorCode, message: string, action: string, cause?: unknown) {
    super(message);
    this.name = 'AuditError';
    this.code = code;
    this.action = action;
    if (cause !== undefined) {
      this.cause = cause;
    }
  }
}
