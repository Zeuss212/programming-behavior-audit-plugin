import type { SessionAnalysis } from '../ai/aiClient';
import { canonicalJson } from '../domain/canonicalJson';
import { AuditError } from '../domain/errors';
import {
  ANALYSIS_LOG_REASON_CODES,
  ANALYSIS_LOG_SCHEMA_VERSION,
  ANALYSIS_LOG_STATUSES,
  type AnalysisLog,
  type AnalysisLogReasonCode,
  type AnalysisLogStatus,
  type JsonObject,
  type JsonValue,
} from '../domain/types';

const encoder = new TextEncoder();
const decoder = new TextDecoder();

const REASON_MESSAGES: Readonly<Record<AnalysisLogReasonCode, string>> = {
  disabled_by_student: '学生未选择生成 AI 建议，已保留本地课堂简报。',
  ai_not_configured: '未配置 AI 服务，已保留本地课堂简报。',
  ai_provider_request_rejected: 'AI 服务拒绝了请求，已保留本地课堂简报。',
  ai_provider_timeout: 'AI 服务响应超时，已保留本地课堂简报。',
  ai_provider_network_error: '无法连接 AI 服务，已保留本地课堂简报。',
  ai_provider_auth_failed: 'AI 服务认证失败，已保留本地课堂简报。',
  ai_provider_rate_limited: 'AI 服务暂时限流，已保留本地课堂简报。',
  ai_provider_unavailable: 'AI 服务暂不可用，已保留本地课堂简报。',
  ai_response_truncated: 'AI 返回内容不完整，已保留本地课堂简报。',
  ai_response_invalid: 'AI 返回内容格式无效，已保留本地课堂简报。',
  analysis_unavailable: 'AI 建议暂不可用，已保留本地课堂简报。',
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isReasonCode(value: unknown): value is AnalysisLogReasonCode {
  return (
    typeof value === 'string' &&
    ANALYSIS_LOG_REASON_CODES.includes(value as AnalysisLogReasonCode)
  );
}

function isStatus(value: unknown): value is AnalysisLogStatus {
  return typeof value === 'string' && ANALYSIS_LOG_STATUSES.includes(value as AnalysisLogStatus);
}

function isStringArray(value: unknown): value is readonly string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string');
}

function isSessionAnalysis(value: unknown): value is SessionAnalysis {
  if (!isRecord(value) || value.schema_version !== 1 || typeof value.summary !== 'string') {
    return false;
  }
  if (!Array.isArray(value.observations) || !isStringArray(value.attention_points)) {
    return false;
  }
  return value.observations.every(
    (observation) =>
      isRecord(observation) &&
      typeof observation.title === 'string' &&
      typeof observation.description === 'string' &&
      isStringArray(observation.evidence_event_ids),
  );
}

function redactText(value: string): string {
  return value
    .replace(/Bearer\s+\S+/giu, 'Bearer [REDACTED]')
    .replace(/\b(?:sk|ark)-[A-Za-z0-9_-]{8,}\b/gu, '[REDACTED]')
    .replace(/(?:[A-Za-z]:\\|\/Users\/|\/home\/)[^\s"']+/gu, '[PATH]');
}

function redactAnalysis(analysis: SessionAnalysis): SessionAnalysis {
  return {
    schema_version: analysis.schema_version,
    summary: redactText(analysis.summary),
    observations: analysis.observations.map((observation) => ({
      title: redactText(observation.title),
      description: redactText(observation.description),
      evidence_event_ids: observation.evidence_event_ids.map((eventId) => redactText(eventId)),
    })),
    attention_points: analysis.attention_points.map((point) => redactText(point)),
  };
}

function asJsonObject(value: object): JsonObject {
  return value as JsonObject;
}

function createReason(
  status: Extract<AnalysisLogStatus, 'skipped' | 'failed'>,
  sessionId: string,
  generatedAt: string,
  code: AnalysisLogReasonCode,
): AnalysisLog {
  return {
    schema_version: ANALYSIS_LOG_SCHEMA_VERSION,
    session_id: sessionId,
    generated_at: generatedAt,
    status,
    reason: { code, message: REASON_MESSAGES[code] },
  };
}

function codeFromError(error: unknown): AnalysisLogReasonCode {
  if (error instanceof AuditError && isReasonCode(error.code)) {
    return error.code;
  }
  return 'analysis_unavailable';
}

function isNormalizedAnalysisLog(value: unknown, sessionId: string): value is AnalysisLog {
  if (
    !isRecord(value) ||
    value.schema_version !== ANALYSIS_LOG_SCHEMA_VERSION ||
    value.session_id !== sessionId ||
    typeof value.generated_at !== 'string' ||
    !isStatus(value.status)
  ) {
    return false;
  }
  if (value.status === 'completed') {
    return isSessionAnalysis(value.analysis);
  }
  return isRecord(value.reason) && isReasonCode(value.reason.code);
}

export function createCompletedAnalysisLog(
  sessionId: string,
  generatedAt: string,
  analysis: SessionAnalysis,
): AnalysisLog {
  return {
    schema_version: ANALYSIS_LOG_SCHEMA_VERSION,
    session_id: sessionId,
    generated_at: generatedAt,
    status: 'completed',
    analysis: asJsonObject(redactAnalysis(analysis)),
  };
}

export function createSkippedAnalysisLog(
  sessionId: string,
  generatedAt: string,
  code: Extract<
    AnalysisLogReasonCode,
    'disabled_by_student' | 'ai_not_configured' | 'analysis_unavailable'
  >,
): AnalysisLog {
  return createReason('skipped', sessionId, generatedAt, code);
}

export function createFailedAnalysisLog(
  sessionId: string,
  generatedAt: string,
  error: unknown,
): AnalysisLog {
  const code = codeFromError(error);
  if (code === 'ai_not_configured') {
    return createSkippedAnalysisLog(sessionId, generatedAt, code);
  }
  return createReason('failed', sessionId, generatedAt, code);
}

export function serializeAnalysisLog(log: AnalysisLog): Uint8Array {
  return encoder.encode(`${canonicalJson(log as unknown as JsonValue)}\n`);
}

export function normalizeAnalysisArtifact(
  bytes: Uint8Array | undefined,
  sessionId: string,
  generatedAt: string,
): Uint8Array {
  if (bytes === undefined) {
    return serializeAnalysisLog(
      createSkippedAnalysisLog(sessionId, generatedAt, 'analysis_unavailable'),
    );
  }
  try {
    const value = JSON.parse(decoder.decode(bytes)) as unknown;
    if (isNormalizedAnalysisLog(value, sessionId)) {
      if (value.status === 'completed') {
        return serializeAnalysisLog(
          createCompletedAnalysisLog(
            sessionId,
            value.generated_at,
            value.analysis as unknown as SessionAnalysis,
          ),
        );
      }
      return serializeAnalysisLog(
        createReason(value.status, sessionId, value.generated_at, value.reason!.code),
      );
    }
    if (isSessionAnalysis(value)) {
      return serializeAnalysisLog(createCompletedAnalysisLog(sessionId, generatedAt, value));
    }
  } catch {
    // Invalid historical bytes are converted to a stable safe outcome below.
  }
  return serializeAnalysisLog(
    createFailedAnalysisLog(sessionId, generatedAt, new Error('Analysis artifact is invalid.')),
  );
}
