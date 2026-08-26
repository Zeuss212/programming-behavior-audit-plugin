import { mkdtemp, readFile, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import type { AiClient } from '../ai/aiClient';
import { canonicalJson, sha256Hex } from '../domain/canonicalJson';
import { AuditError } from '../domain/errors';
import {
  AUDIT_EVENT_SCHEMA_VERSION,
  PLAN_SCHEMA_VERSION,
  type AuditEvent,
  type JsonValue,
  type PublishedPlan,
} from '../domain/types';
import { FileSessionAnalysisService } from '../reports/analysisService';
import { FileReportService, FileSessionExporter } from '../reports/exporter';
import { FileSessionRepository } from '../storage/sessionRepository';
import { analyzeAndExport } from '../workflows/finishAnalyzeExport';

const encoder = new TextEncoder();

function plan(): PublishedPlan {
  const unsigned: Omit<PublishedPlan, 'content_sha256'> = {
    schema_version: PLAN_SCHEMA_VERSION,
    plan_id: 'plan-blind-review-001',
    version: 1,
    problem_text: '处理空列表输入。',
    knowledge_points: [
      {
        knowledge_point_id: 'kp-empty-list',
        name: '空列表边界',
        description: '处理空列表输入。',
        observation_basis: '记录编辑、保存和运行事件。',
      },
    ],
    tests: [],
    published_at: '2026-08-14T00:00:00.000Z',
  };
  return {
    ...unsigned,
    content_sha256: sha256Hex(canonicalJson(unsigned as unknown as JsonValue)),
  };
}

function saveEvent(sessionId: string): AuditEvent {
  return {
    schema_version: AUDIT_EVENT_SCHEMA_VERSION,
    event_id: `${sessionId}:1`,
    session_id: sessionId,
    session_seq: 1,
    occurred_at: '2026-08-14T00:00:01.000Z',
    monotonic_ms: 1000,
    kind: 'save',
    payload: {},
  };
}

describe('finish, analyze, and export black-box flow', () => {
  it('exports local reports after an AI timeout without leaking the provider error', async () => {
    const storageRoot = await mkdtemp(join(tmpdir(), 'behavior-audit-blind-storage-'));
    const destinationRoot = await mkdtemp(join(tmpdir(), 'behavior-audit-blind-export-'));
    const repository = new FileSessionRepository(
      storageRoot,
      () => new Date('2026-08-14T00:01:00.000Z'),
      () => 'session-blind-review-001',
    );
    const created = await repository.create(plan(), 'workspace-blind-review-001');
    await repository.append(created.session_id, [saveEvent(created.session_id)]);
    await repository.transition(created.session_id, 'collecting', 'finalizing');
    await repository.transition(created.session_id, 'finalizing', 'completed');
    await new FileReportService(repository, () => new Date('2026-08-14T00:02:00.000Z')).materialize(
      created.session_id,
    );

    const aiClient: Pick<AiClient, 'analyzeSession'> = {
      analyzeSession: () =>
        Promise.reject(
          new AuditError(
            'ai_provider_timeout',
            'Bearer ark-blind-review-secret at /Users/student/private-workspace',
            '请勿导出此信息。',
          ),
        ),
    };
    const analysisService = new FileSessionAnalysisService(
      repository,
      aiClient,
      () => new Date('2026-08-14T00:03:00.000Z'),
    );
    const exporter = new FileSessionExporter(
      repository,
      '0.1.1',
      () => new Date('2026-08-14T00:04:00.000Z'),
    );

    const result = await analyzeAndExport({
      sessionId: created.session_id,
      workspaceRoot: '/Users/student/private-workspace',
      autoAnalyze: true,
      analysisService,
      exporter,
      chooseDestination: () => Promise.resolve({ fsPath: destinationRoot }),
    });

    expect(result).toMatchObject({
      kind: 'exported',
      analysis: { status: 'failed', reason: { code: 'ai_provider_timeout' } },
    });
    if (result.kind !== 'exported') {
      throw new Error('Expected a completed export.');
    }
    const outputDirectory = join(destinationRoot, created.session_id);
    const analysisBytes = new Uint8Array(await readFile(join(outputDirectory, 'analysis_log.json')));
    const analysisText = new TextDecoder().decode(analysisBytes);
    const manifest = JSON.parse(await readFile(join(outputDirectory, 'manifest.json'), 'utf8')) as {
      readonly files: readonly { readonly path: string; readonly sha256: string }[];
    };
    const analysisFile = manifest.files.find((file) => file.path === 'analysis_log.json');

    expect(JSON.parse(analysisText)).toMatchObject({
      session_id: created.session_id,
      status: 'failed',
      reason: { code: 'ai_provider_timeout' },
    });
    expect(analysisText).not.toContain('ark-blind-review-secret');
    expect(analysisText).not.toContain('/Users/student/private-workspace');
    expect(analysisFile?.sha256).toBe(sha256Hex(analysisBytes));
    expect(analysisFile).toBeDefined();
    expect(encoder.encode(analysisText).byteLength).toBe(analysisBytes.byteLength);
  });

  it('keeps the local classroom brief without starting AI analysis when export-folder selection is cancelled', async () => {
    const storageRoot = await mkdtemp(join(tmpdir(), 'behavior-audit-blind-cancel-storage-'));
    const destinationRoot = await mkdtemp(join(tmpdir(), 'behavior-audit-blind-cancel-export-'));
    const repository = new FileSessionRepository(
      storageRoot,
      () => new Date('2026-08-14T00:01:00.000Z'),
      () => 'session-blind-cancel-001',
    );
    const created = await repository.create(plan(), 'workspace-blind-cancel-001');
    await repository.transition(created.session_id, 'collecting', 'finalizing');
    await repository.transition(created.session_id, 'finalizing', 'completed');
    await new FileReportService(repository, () => new Date('2026-08-14T00:02:00.000Z')).materialize(
      created.session_id,
    );
    const aiClient: Pick<AiClient, 'analyzeSession'> = {
      analyzeSession: () => Promise.reject(new Error('AI must not be called while disabled.')),
    };
    const analysisService = new FileSessionAnalysisService(
      repository,
      aiClient,
      () => new Date('2026-08-14T00:03:00.000Z'),
    );

    const result = await analyzeAndExport({
      sessionId: created.session_id,
      workspaceRoot: '',
      autoAnalyze: false,
      analysisService,
      exporter: new FileSessionExporter(
        repository,
        '0.1.1',
        () => new Date('2026-08-14T00:04:00.000Z'),
      ),
      chooseDestination: () => Promise.resolve(undefined),
    });

    expect(result).toEqual({ kind: 'export_cancelled' });
    expect(await repository.readArtifact(created.session_id, 'ai_analysis')).toBeUndefined();
    expect(await readdir(destinationRoot)).toEqual([]);
  });
});
