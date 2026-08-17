import { mkdtemp, readFile, readdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { beforeEach, describe, expect, it } from 'vitest';

import { canonicalJson, sha256Hex } from '../domain/canonicalJson';
import {
  AUDIT_EVENT_SCHEMA_VERSION,
  LEGACY_CLASSROOM_BRIEF_SCHEMA_VERSION,
  PLAN_SCHEMA_VERSION,
  type AuditEvent,
  type JsonValue,
  type PublishedPlan,
} from '../domain/types';
import { FileSessionExporter, FileReportService } from '../reports/exporter';
import { FileSessionRepository } from '../storage/sessionRepository';

function plan(): PublishedPlan {
  const unsigned: Omit<PublishedPlan, 'content_sha256'> = {
    schema_version: PLAN_SCHEMA_VERSION,
    plan_id: 'plan-export-001',
    version: 1,
    problem_text: '实现空列表处理。',
    knowledge_points: [
      {
        knowledge_point_id: 'kp-empty',
        name: '空列表处理',
        description: '处理空列表输入。',
        observation_basis: '运行空列表用例并得到约定结果。',
      },
    ],
    tests: [],
    published_at: '2026-08-10T00:00:00.000Z',
  };
  return {
    ...unsigned,
    content_sha256: sha256Hex(canonicalJson(unsigned as unknown as JsonValue)),
  };
}

function event(sessionId: string): AuditEvent {
  return {
    schema_version: AUDIT_EVENT_SCHEMA_VERSION,
    event_id: `${sessionId}:1`,
    session_id: sessionId,
    session_seq: 1,
    occurred_at: '2026-08-10T00:00:01.000Z',
    monotonic_ms: 1000,
    kind: 'save',
    payload: {},
  };
}

describe('FileReportService and FileSessionExporter', () => {
  let storageRoot: string;
  let destinationRoot: string;
  let repository: FileSessionRepository;

  beforeEach(async () => {
    storageRoot = await mkdtemp(join(tmpdir(), 'behavior-audit-report-storage-'));
    destinationRoot = await mkdtemp(join(tmpdir(), 'behavior-audit-export-'));
    repository = new FileSessionRepository(
      storageRoot,
      () => new Date('2026-08-10T00:03:00.000Z'),
      () => 'session-export-001',
    );
  });

  it('materializes idempotent reports and exports the exact portable file set with valid hashes', async () => {
    const created = await repository.create(plan(), 'workspace-export-001');
    await repository.append(created.session_id, [event(created.session_id)]);
    await repository.transition(created.session_id, 'collecting', 'finalizing');
    await repository.transition(created.session_id, 'finalizing', 'completed');

    const service = new FileReportService(
      repository,
      () => new Date('2026-08-10T00:04:00.000Z'),
    );
    const first = await service.materialize(created.session_id);
    const second = await service.materialize(created.session_id);
    expect(second).toEqual(first);

    const aiBytes = new TextEncoder().encode('{"summary":"可选分析"}\n');
    await repository.writeArtifact(created.session_id, 'ai_analysis', aiBytes);

    const exporter = new FileSessionExporter(
      repository,
      '0.1.0',
      () => new Date('2026-08-10T00:05:00.000Z'),
    );
    const manifest = await exporter.exportSession(created.session_id, {
      fsPath: destinationRoot,
    });
    const exportDirectory = join(destinationRoot, created.session_id);
    const names = (await readdir(exportDirectory)).sort();

    expect(names).toEqual([
      'ai_analysis.json',
      'classroom_brief.json',
      'manifest.json',
      'operation_log.json',
      'plan_snapshot.json',
      'process_log.md',
      'teacher_brief.md',
    ]);
    expect(manifest).toMatchObject({
      schema_version: 1,
      extension_version: '0.1.0',
      session_id: created.session_id,
      exported_at: '2026-08-10T00:05:00.000Z',
    });
    for (const file of manifest.files) {
      const bytes = new Uint8Array(await readFile(join(exportDirectory, file.path)));
      expect(bytes.byteLength).toBe(file.bytes);
      expect(sha256Hex(bytes)).toBe(file.sha256);
    }
    expect(JSON.parse(await readFile(join(exportDirectory, 'manifest.json'), 'utf8'))).toEqual(
      manifest,
    );

    await expect(
      exporter.exportSession(created.session_id, { fsPath: destinationRoot }),
    ).rejects.toMatchObject({ code: 'export_failed' });
  });

  it('rejects report generation for a non-terminal session', async () => {
    const created = await repository.create(plan(), 'workspace-export-001');
    const service = new FileReportService(repository, () => new Date());

    await expect(service.materialize(created.session_id)).rejects.toMatchObject({
      code: 'session_conflict',
    });
  });

  it('keeps a persisted V1 brief readable and does not fabricate a teacher brief for it', async () => {
    const created = await repository.create(plan(), 'workspace-export-legacy-001');
    await repository.append(created.session_id, [event(created.session_id)]);
    await repository.transition(created.session_id, 'collecting', 'finalizing');
    await repository.transition(created.session_id, 'finalizing', 'completed');
    await repository.writeArtifact(created.session_id, 'operation_log', new TextEncoder().encode('{}\n'));
    await repository.writeArtifact(created.session_id, 'process_log', new TextEncoder().encode('# 原有日志\n'));
    await repository.writeArtifact(
      created.session_id,
      'classroom_brief',
      new TextEncoder().encode(
        `${canonicalJson({
          schema_version: LEGACY_CLASSROOM_BRIEF_SCHEMA_VERSION,
          session_id: created.session_id,
          generated_at: '2026-08-10T00:04:00.000Z',
          session_result: { status: 'completed' },
          effective_observation: {
            milliseconds: 0,
            method: 'focused_event_gaps_capped_at_30_seconds',
          },
          run_statistics: { total: 0, success: 0, failure: 0, unknown: 0 },
          evidence_summary: [],
          attention_point: null,
        })}\n`,
      ),
    );

    const service = new FileReportService(repository, () => new Date('2026-08-10T00:05:00.000Z'));
    const brief = await service.materialize(created.session_id);

    expect(brief.schema_version).toBe(LEGACY_CLASSROOM_BRIEF_SCHEMA_VERSION);
    await expect(repository.readArtifact(created.session_id, 'teacher_brief')).resolves.toBeUndefined();

    const exporter = new FileSessionExporter(repository, '0.1.1', () => new Date());
    await exporter.exportSession(created.session_id, { fsPath: destinationRoot });
    expect((await readdir(join(destinationRoot, created.session_id))).sort()).not.toContain(
      'teacher_brief.md',
    );
  });

  it('rejects a malformed persisted V2 teacher evaluation instead of rendering it', async () => {
    const created = await repository.create(plan(), 'workspace-export-invalid-v2-001');
    await repository.append(created.session_id, [event(created.session_id)]);
    await repository.transition(created.session_id, 'collecting', 'finalizing');
    await repository.transition(created.session_id, 'finalizing', 'completed');
    await repository.writeArtifact(
      created.session_id,
      'classroom_brief',
      new TextEncoder().encode(
        `${canonicalJson({
          schema_version: 2,
          session_id: created.session_id,
          generated_at: '2026-08-10T00:04:00.000Z',
          session_result: { status: 'completed' },
          effective_observation: {
            milliseconds: 0,
            method: 'focused_event_gaps_capped_at_30_seconds',
          },
          run_statistics: { total: 0, success: 0, failure: 0, unknown: 0 },
          evidence_summary: [],
          attention_point: null,
          teacher_evaluation: {},
        })}\n`,
      ),
    );

    const service = new FileReportService(repository, () => new Date());
    await expect(service.materialize(created.session_id)).rejects.toMatchObject({
      code: 'storage_corrupt',
    });
  });
});
