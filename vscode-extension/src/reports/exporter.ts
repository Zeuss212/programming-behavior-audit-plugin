import { mkdir, readdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import { canonicalJson, sha256Hex } from '../domain/canonicalJson';
import { AuditError } from '../domain/errors';
import {
  CLASSROOM_BRIEF_SCHEMA_VERSION,
  EXPORT_MANIFEST_SCHEMA_VERSION,
  LEGACY_CLASSROOM_BRIEF_SCHEMA_VERSION,
  type AuditEvent,
  type ClassroomBrief,
  type ExportManifest,
  type ExportManifestFile,
  type JsonValue,
} from '../domain/types';
import type { SessionArtifactKind, SessionRepository } from '../storage/sessionRepository';
import { generateClassroomBrief } from './briefGenerator';
import { generateOperationLog, generateProcessLog } from './logGenerator';
import { renderTeacherBrief } from './teacherBrief';

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isTeacherEvaluation(value: unknown): boolean {
  if (!isRecord(value)) return false;
  const focus = value.classroom_focus;
  const metrics = value.metrics;
  const dimensions = value.dimensions;
  const validDimensions =
    Array.isArray(dimensions) &&
    dimensions.every(
      (dimension) =>
        isRecord(dimension) &&
        ['运行验证', '调试与修正', '任务推进'].includes(dimension.name as string) &&
        typeof dimension.score === 'number' &&
        typeof dimension.maximum_score === 'number' &&
        Array.isArray(dimension.evidence_event_ids) &&
        dimension.evidence_event_ids.every((eventId) => typeof eventId === 'string'),
    );
  return (
    value.label === '课题实践表现' &&
    ['S', 'A', 'B', 'C', 'D'].includes(value.overall_grade as string) &&
    ['high', 'medium', 'low'].includes(value.evidence_confidence as string) &&
    typeof value.summary === 'string' &&
    validDimensions &&
    isRecord(focus) &&
    ['stable', 'fluctuating', 'frequent_switching', 'insufficient'].includes(
      focus.reference as string,
    ) &&
    typeof focus.focus_loss_count === 'number' &&
    typeof focus.focus_loss_milliseconds === 'number' &&
    typeof focus.longest_focus_loss_milliseconds === 'number' &&
    typeof focus.unclosed_focus_loss === 'boolean' &&
    typeof focus.note === 'string' &&
    isRecord(metrics) &&
    typeof metrics.edit_count === 'number' &&
    typeof metrics.save_count === 'number' &&
    typeof metrics.run_count === 'number' &&
    typeof metrics.determinate_run_count === 'number' &&
    typeof metrics.successful_run_count === 'number' &&
    typeof metrics.failed_run_count === 'number' &&
    typeof metrics.unknown_run_count === 'number' &&
    (typeof metrics.execution_success_rate === 'number' || metrics.execution_success_rate === null) &&
    typeof metrics.recovery_success_count === 'number' &&
    typeof metrics.complete_work_cycle_count === 'number' &&
    typeof value.teaching_suggestion === 'string' &&
    typeof value.limitations === 'string'
  );
}

export interface ReportService {
  materialize(sessionId: string): Promise<ClassroomBrief>;
}

export interface ExportDestination {
  readonly fsPath: string;
}

export interface SessionExporter {
  exportSession(sessionId: string, destination: ExportDestination): Promise<ExportManifest>;
}

function parseBrief(bytes: Uint8Array): ClassroomBrief {
  try {
    const value = JSON.parse(decoder.decode(bytes)) as Partial<ClassroomBrief>;
    if (
      (value.schema_version !== CLASSROOM_BRIEF_SCHEMA_VERSION &&
        value.schema_version !== LEGACY_CLASSROOM_BRIEF_SCHEMA_VERSION) ||
      typeof value.session_id !== 'string' ||
      typeof value.generated_at !== 'string' ||
      value.session_result === undefined ||
      value.effective_observation === undefined ||
      value.run_statistics === undefined ||
      !Array.isArray(value.evidence_summary) ||
      !('attention_point' in value)
    ) {
      throw new Error('Invalid classroom brief.');
    }
    if (
      value.schema_version === CLASSROOM_BRIEF_SCHEMA_VERSION &&
      !isTeacherEvaluation(value.teacher_evaluation)
    ) {
      throw new Error('Invalid teacher evaluation.');
    }
    return value as ClassroomBrief;
  } catch (error) {
    throw new AuditError(
      'storage_corrupt',
      '已保存的课堂简报损坏。',
      '请保留文件并重试生成简报。',
      error,
    );
  }
}

export class FileReportService implements ReportService {
  public constructor(
    private readonly repository: SessionRepository,
    private readonly now: () => Date,
  ) {}

  public async materialize(sessionId: string): Promise<ClassroomBrief> {
    const session = await this.repository.get(sessionId);
    if (session === undefined) {
      throw new AuditError('storage_unavailable', '找不到本地会话。', '请刷新会话列表后重试。');
    }
    const plan = await this.repository.readPlanSnapshot(sessionId);
    if (plan === undefined) {
      throw new AuditError('storage_corrupt', '找不到会话方案快照。', '请保留本地文件并导出诊断信息。');
    }
    const events: AuditEvent[] = [];
    for await (const event of this.repository.readEvents(sessionId)) {
      events.push(event);
    }

    const existingBriefBytes = await this.repository.readArtifact(sessionId, 'classroom_brief');
    if (existingBriefBytes !== undefined) {
      const existingBrief = parseBrief(existingBriefBytes);
      if (
        existingBrief.schema_version === CLASSROOM_BRIEF_SCHEMA_VERSION &&
        (await this.repository.readArtifact(sessionId, 'teacher_brief')) === undefined
      ) {
        await this.repository.writeArtifact(
          sessionId,
          'teacher_brief',
          encoder.encode(renderTeacherBrief(existingBrief)),
        );
      }
      return existingBrief;
    }

    const generatedAt =
      existingBriefBytes === undefined ? this.now().toISOString() : parseBrief(existingBriefBytes).generated_at;
    const reportInput = { session, plan, events };
    const brief = generateClassroomBrief({ ...reportInput, generatedAt });
    await this.repository.writeArtifact(
      sessionId,
      'operation_log',
      generateOperationLog(reportInput),
    );
    await this.repository.writeArtifact(sessionId, 'process_log', generateProcessLog(reportInput));
    await this.repository.writeArtifact(
      sessionId,
      'classroom_brief',
      encoder.encode(`${canonicalJson(brief as unknown as JsonValue)}\n`),
    );
    await this.repository.writeArtifact(
      sessionId,
      'teacher_brief',
      encoder.encode(renderTeacherBrief(brief)),
    );
    return brief;
  }
}

interface ExportFileSource {
  readonly path: string;
  readonly bytes: Uint8Array;
}

export class FileSessionExporter implements SessionExporter {
  public constructor(
    private readonly repository: SessionRepository,
    private readonly extensionVersion: string,
    private readonly now: () => Date,
  ) {}

  public async exportSession(
    sessionId: string,
    destination: ExportDestination,
  ): Promise<ExportManifest> {
    try {
      const session = await this.repository.get(sessionId);
      if (session === undefined) {
        throw new Error('Session not found.');
      }
      const plan = await this.repository.readPlanSnapshot(sessionId);
      if (plan === undefined) {
        throw new Error('Plan snapshot not found.');
      }
      const sources: ExportFileSource[] = [
        {
          path: 'plan_snapshot.json',
          bytes: encoder.encode(`${canonicalJson(plan as unknown as JsonValue)}\n`),
        },
      ];
      const artifacts: readonly [SessionArtifactKind, string, boolean][] = [
        ['operation_log', 'operation_log.json', true],
        ['process_log', 'process_log.md', true],
        ['classroom_brief', 'classroom_brief.json', true],
        ['ai_analysis', 'ai_analysis.json', false],
      ];
      for (const [kind, path, required] of artifacts) {
        const bytes = await this.repository.readArtifact(sessionId, kind);
        if (bytes === undefined) {
          if (required) {
            throw new Error(`Required artifact missing: ${kind}`);
          }
        } else {
          sources.push({ path, bytes });
        }
      }
      const classroomBrief = parseBrief(
        sources.find((source) => source.path === 'classroom_brief.json')?.bytes ?? new Uint8Array(),
      );
      if (classroomBrief.schema_version === CLASSROOM_BRIEF_SCHEMA_VERSION) {
        const teacherBrief = await this.repository.readArtifact(sessionId, 'teacher_brief');
        sources.push({
          path: 'teacher_brief.md',
          bytes: teacherBrief ?? encoder.encode(renderTeacherBrief(classroomBrief)),
        });
      }

      const exportDirectory = join(destination.fsPath, sessionId);
      try {
        await mkdir(exportDirectory, { recursive: false });
      } catch (error) {
        const entries = await readdir(exportDirectory);
        if (entries.length > 0) {
          throw new Error('Export directory is not empty.', { cause: error });
        }
      }

      const files: ExportManifestFile[] = [];
      for (const source of sources) {
        await writeFile(join(exportDirectory, source.path), source.bytes, { flag: 'wx' });
        files.push({
          path: source.path,
          bytes: source.bytes.byteLength,
          sha256: sha256Hex(source.bytes),
        });
      }
      const manifest: ExportManifest = {
        schema_version: EXPORT_MANIFEST_SCHEMA_VERSION,
        extension_version: this.extensionVersion,
        session_id: session.session_id,
        exported_at: this.now().toISOString(),
        files,
      };
      await writeFile(
        join(exportDirectory, 'manifest.json'),
        encoder.encode(`${canonicalJson(manifest as unknown as JsonValue)}\n`),
        { flag: 'wx' },
      );
      return manifest;
    } catch (error) {
      if (error instanceof AuditError && error.code === 'export_failed') {
        throw error;
      }
      throw new AuditError(
        'export_failed',
        '无法导出会话文件。',
        '请选择一个可写且不包含同名会话目录的位置后重试。',
        error,
      );
    }
  }
}
