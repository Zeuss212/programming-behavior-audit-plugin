import { describe, expect, it, vi } from 'vitest';

import type { AiClient, SessionAnalysis } from '../ai/aiClient';
import { AuditError } from '../domain/errors';
import type { JsonObject } from '../domain/types';
import {
  createCompletedAnalysisLog,
  createFailedAnalysisLog,
  createSkippedAnalysisLog,
  normalizeAnalysisArtifact,
  serializeAnalysisLog,
} from '../reports/analysisLog';
import {
  FileSessionAnalysisService,
  type AnalysisArtifactRepository,
} from '../reports/analysisService';

const encoder = new TextEncoder();
const decoder = new TextDecoder();
const sessionId = 'session-analysis-001';
const generatedAt = '2026-08-14T12:00:00.000Z';

function analysis(): SessionAnalysis {
  return {
    schema_version: 1,
    summary: '已完成本地课堂行为观察。',
    observations: [
      {
        title: '保存后运行',
        description: '学生在保存代码后执行了 Python 运行。',
        evidence_event_ids: ['session-analysis-001:2'],
      },
    ],
    attention_points: ['可继续检查边界条件。'],
  };
}

function brief(): JsonObject {
  return {
    schema_version: 1,
    session_id: sessionId,
    generated_at: generatedAt,
    session_result: { status: 'completed' },
    effective_observation: { milliseconds: 1200, method: 'focused_event_gaps_capped_at_30_seconds' },
    run_statistics: { total: 1, success: 1, failure: 0, unknown: 0 },
    evidence_summary: [],
    attention_point: null,
  };
}

function setupService(options: {
  readonly ai?: Pick<AiClient, 'analyzeSession'>;
  readonly briefBytes?: Uint8Array;
  readonly writeFailure?: Error;
} = {}) {
  const artifacts = new Map<string, Uint8Array>();
  if (options.briefBytes !== undefined) {
    artifacts.set('classroom_brief', options.briefBytes);
  } else {
    artifacts.set('classroom_brief', encoder.encode(JSON.stringify(brief())));
  }
  const repository: AnalysisArtifactRepository = {
    readArtifact: (_sessionId, kind) => Promise.resolve(artifacts.get(kind)),
    writeArtifact: (_sessionId, kind, bytes) => {
      if (options.writeFailure !== undefined) {
        return Promise.reject(options.writeFailure);
      }
      artifacts.set(kind, bytes);
      return Promise.resolve();
    },
  };
  const ai: Pick<AiClient, 'analyzeSession'> =
    options.ai ?? { analyzeSession: vi.fn(() => Promise.resolve(analysis())) };
  const service = new FileSessionAnalysisService(
    repository,
    ai,
    () => new Date(generatedAt),
  );
  return { artifacts, repository, ai, service };
}

describe('analysis log artifacts', () => {
  it('serializes a completed analysis with the session identity and validated AI content', () => {
    const bytes = serializeAnalysisLog(createCompletedAnalysisLog(sessionId, generatedAt, analysis()));

    expect(JSON.parse(decoder.decode(bytes))).toEqual({
      schema_version: 1,
      session_id: sessionId,
      generated_at: generatedAt,
      status: 'completed',
      analysis: analysis(),
    });
    expect(decoder.decode(bytes).endsWith('\n')).toBe(true);
  });

  it('records explicit opt-out and missing configuration as skipped without sensitive content', () => {
    const disabled = createSkippedAnalysisLog(sessionId, generatedAt, 'disabled_by_student');
    const unconfigured = createSkippedAnalysisLog(sessionId, generatedAt, 'ai_not_configured');
    const serialized = decoder.decode(serializeAnalysisLog(unconfigured));

    expect(disabled).toMatchObject({
      status: 'skipped',
      reason: { code: 'disabled_by_student' },
    });
    expect(unconfigured).toMatchObject({
      status: 'skipped',
      reason: { code: 'ai_not_configured' },
    });
    expect(serialized).not.toContain('ark-demo-secret');
    expect(serialized).not.toContain('/Users/student/workspace');
  });

  it('maps provider failures to fixed safe reasons instead of serializing the thrown message', () => {
    const error = new AuditError(
      'ai_provider_network_error',
      'Bearer ark-demo-secret failed at /Users/student/workspace',
      'do not serialize this action',
    );
    const serialized = decoder.decode(
      serializeAnalysisLog(createFailedAnalysisLog(sessionId, generatedAt, error)),
    );

    expect(JSON.parse(serialized)).toMatchObject({
      status: 'failed',
      reason: { code: 'ai_provider_network_error' },
    });
    expect(serialized).not.toContain('Bearer');
    expect(serialized).not.toContain('ark-demo-secret');
    expect(serialized).not.toContain('/Users/student/workspace');
    expect(serialized).not.toContain('do not serialize this action');
  });

  it('redacts secret-shaped values and absolute paths from AI suggestion content before export', () => {
    const unsafe = {
      ...analysis(),
      summary: 'Bearer ark-demo-secret was seen in /Users/student/workspace/main.py',
      attention_points: ['不要展示 /home/student/private.py。'],
    };
    const serialized = decoder.decode(
      serializeAnalysisLog(createCompletedAnalysisLog(sessionId, generatedAt, unsafe)),
    );

    expect(serialized).not.toContain('ark-demo-secret');
    expect(serialized).not.toContain('/Users/student/workspace/main.py');
    expect(serialized).not.toContain('/home/student/private.py');
    expect(JSON.parse(serialized)).toMatchObject({ status: 'completed' });
  });

  it('normalizes a historical raw AI artifact and corrupt bytes into safe versioned logs', () => {
    const historical = normalizeAnalysisArtifact(
      encoder.encode(`${JSON.stringify(analysis())}\n`),
      sessionId,
      generatedAt,
    );
    const corrupt = normalizeAnalysisArtifact(
      encoder.encode('Bearer ark-demo-secret at /Users/student/workspace'),
      sessionId,
      generatedAt,
    );

    expect(JSON.parse(decoder.decode(historical))).toMatchObject({
      status: 'completed',
      analysis: analysis(),
    });
    expect(JSON.parse(decoder.decode(corrupt))).toMatchObject({
      status: 'failed',
      reason: { code: 'analysis_unavailable' },
    });
    expect(decoder.decode(corrupt)).not.toContain('ark-demo-secret');
    expect(decoder.decode(corrupt)).not.toContain('/Users/student/workspace');
  });
});

describe('FileSessionAnalysisService', () => {
  it('persists completed AI analysis after reading the local classroom brief', async () => {
    const { ai, artifacts, service } = setupService();

    const result = await service.materialize(sessionId, {
      enabled: true,
      workspaceRoot: '/Users/student/workspace',
    });

    expect(result).toMatchObject({ status: 'completed', analysis: analysis() });
    expect(ai.analyzeSession).toHaveBeenCalledWith({
      sessionId,
      workspaceRoot: '/Users/student/workspace',
      brief: brief(),
      evidence: [],
      codeFragments: [],
    });
    expect(JSON.parse(decoder.decode(artifacts.get('ai_analysis')))).toMatchObject({
      status: 'completed',
    });
  });

  it('persists a skipped result without calling AI when the student disables it', async () => {
    const { ai, artifacts, service } = setupService();

    const result = await service.materialize(sessionId, { enabled: false, workspaceRoot: '' });

    expect(result).toMatchObject({
      status: 'skipped',
      reason: { code: 'disabled_by_student' },
    });
    expect(ai.analyzeSession).not.toHaveBeenCalled();
    expect(JSON.parse(decoder.decode(artifacts.get('ai_analysis')))).toMatchObject({
      status: 'skipped',
    });
  });

  it('converts unconfigured and provider failures to persisted non-blocking outcomes', async () => {
    const unconfigured = setupService({
      ai: {
        analyzeSession: vi.fn(() =>
          Promise.reject(new AuditError('ai_not_configured', 'do not expose', 'do not expose')),
        ),
      },
    });
    const providerFailure = setupService({
      ai: {
        analyzeSession: vi.fn(() =>
          Promise.reject(
            new AuditError(
            'ai_provider_timeout',
            'Bearer ark-demo-secret /Users/student/workspace',
            'do not expose',
            ),
          ),
        ),
      },
    });

    const skipped = await unconfigured.service.materialize(sessionId, {
      enabled: true,
      workspaceRoot: '',
    });
    const failed = await providerFailure.service.materialize(sessionId, {
      enabled: true,
      workspaceRoot: '',
    });

    expect(skipped).toMatchObject({ status: 'skipped', reason: { code: 'ai_not_configured' } });
    expect(failed).toMatchObject({ status: 'failed', reason: { code: 'ai_provider_timeout' } });
    expect(decoder.decode(providerFailure.artifacts.get('ai_analysis'))).not.toContain('ark-demo-secret');
    expect(decoder.decode(providerFailure.artifacts.get('ai_analysis'))).not.toContain(
      '/Users/student/workspace',
    );
  });

  it('persists a safe failed result for a missing brief but rejects when writing the artifact fails', async () => {
    const missingBrief = setupService();
    missingBrief.artifacts.delete('classroom_brief');
    const writeFailure = setupService({ writeFailure: new Error('disk full') });

    const unavailable = await missingBrief.service.materialize(sessionId, {
      enabled: true,
      workspaceRoot: '',
    });

    expect(unavailable).toMatchObject({
      status: 'failed',
      reason: { code: 'analysis_unavailable' },
    });
    await expect(
      writeFailure.service.materialize(sessionId, { enabled: false, workspaceRoot: '' }),
    ).rejects.toThrow('disk full');
  });
});
