import { createReadStream } from 'node:fs';
import { access, mkdir, readFile, readdir, unlink, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { createInterface } from 'node:readline';

import { AuditError } from '../domain/errors';
import {
  AUDIT_EVENT_KINDS,
  AUDIT_EVENT_SCHEMA_VERSION,
  SESSION_SCHEMA_VERSION,
  SESSION_STATUSES,
  type AuditEvent,
  type AuditEventKind,
  type PublishedPlan,
  type SessionState,
  type SessionStatus,
} from '../domain/types';
import { validatePlan } from '../domain/validation';
import { writeFileAtomic, writeJsonAtomic } from './atomicFile';
import { OrderedEventWriter } from './eventWriter';

export type SessionArtifactKind =
  | 'operation_log'
  | 'process_log'
  | 'classroom_brief'
  | 'ai_analysis';

const ARTIFACT_FILES: Readonly<Record<SessionArtifactKind, string>> = {
  operation_log: 'operation_log.json',
  process_log: 'process_log.md',
  classroom_brief: 'classroom_brief.json',
  ai_analysis: 'ai_analysis.json',
};

const SAFE_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const ACTIVE_STATUSES: readonly SessionStatus[] = ['collecting', 'interrupted', 'finalizing'];
const TERMINAL_STATUSES: readonly SessionStatus[] = ['completed', 'partial', 'abandoned'];
const ALLOWED_TRANSITIONS: Readonly<Record<SessionStatus, readonly SessionStatus[]>> = {
  collecting: ['interrupted', 'finalizing'],
  interrupted: ['collecting', 'partial', 'abandoned'],
  finalizing: ['completed', 'partial', 'abandoned'],
  completed: [],
  partial: [],
  abandoned: [],
};

interface SessionLocation {
  readonly workspaceId: string;
  readonly directory: string;
}

interface ActivePointer {
  readonly session_id: string;
}

export interface SessionRepository {
  create(plan: PublishedPlan, workspaceId: string): Promise<SessionState>;
  append(sessionId: string, events: readonly AuditEvent[]): Promise<void>;
  transition(
    sessionId: string,
    expected: SessionStatus,
    next: SessionStatus,
    reason?: string,
  ): Promise<SessionState>;
  readEvents(sessionId: string): AsyncIterable<AuditEvent>;
  writeArtifact(
    sessionId: string,
    kind: SessionArtifactKind,
    bytes: Uint8Array,
  ): Promise<void>;
  readArtifact(
    sessionId: string,
    kind: SessionArtifactKind,
  ): Promise<Uint8Array | undefined>;
  findActive(workspaceId: string): Promise<SessionState | undefined>;
  get(sessionId: string): Promise<SessionState | undefined>;
}

function isNodeError(error: unknown): error is NodeJS.ErrnoException {
  return error instanceof Error && 'code' in error;
}

function isNotFound(error: unknown): boolean {
  return isNodeError(error) && error.code === 'ENOENT';
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isSessionStatus(value: unknown): value is SessionStatus {
  return typeof value === 'string' && SESSION_STATUSES.includes(value as SessionStatus);
}

function isAuditEventKind(value: unknown): value is AuditEventKind {
  return typeof value === 'string' && AUDIT_EVENT_KINDS.includes(value as AuditEventKind);
}

function parseSessionState(value: unknown): SessionState {
  if (
    !isRecord(value) ||
    value.schema_version !== SESSION_SCHEMA_VERSION ||
    typeof value.session_id !== 'string' ||
    typeof value.workspace_id !== 'string' ||
    !isSessionStatus(value.status) ||
    typeof value.plan_id !== 'string' ||
    typeof value.plan_version !== 'number' ||
    typeof value.plan_content_sha256 !== 'string' ||
    typeof value.started_at !== 'string' ||
    typeof value.updated_at !== 'string' ||
    !Number.isSafeInteger(value.last_event_seq) ||
    !Number.isSafeInteger(value.last_persisted_seq)
  ) {
    throw new Error('Invalid session state structure.');
  }
  return value as unknown as SessionState;
}

function parseActivePointer(value: unknown): ActivePointer {
  if (!isRecord(value) || typeof value.session_id !== 'string' || !SAFE_ID_PATTERN.test(value.session_id)) {
    throw new Error('Invalid active session pointer.');
  }
  return { session_id: value.session_id };
}

function assertEvent(
  value: unknown,
  sessionId: string,
  expectedSequence: number,
  lineNumber: number,
): AuditEvent {
  if (
    !isRecord(value) ||
    value.schema_version !== AUDIT_EVENT_SCHEMA_VERSION ||
    value.session_id !== sessionId ||
    value.session_seq !== expectedSequence ||
    value.event_id !== `${sessionId}:${String(expectedSequence)}` ||
    typeof value.occurred_at !== 'string' ||
    typeof value.monotonic_ms !== 'number' ||
    !Number.isFinite(value.monotonic_ms) ||
    !isAuditEventKind(value.kind) ||
    !isRecord(value.payload)
  ) {
    throw new AuditError(
      'storage_corrupt',
      `行为事件文件第 ${String(lineNumber)} 行无效或序号不连续。`,
      '请保留原文件并导出诊断信息。',
    );
  }
  return value as unknown as AuditEvent;
}

export class FileSessionRepository implements SessionRepository {
  private readonly sessionLocations = new Map<string, SessionLocation>();
  private readonly writers = new Map<string, OrderedEventWriter>();
  private readonly ownedSessions = new Set<string>();
  private readonly blockedSessions = new Map<string, AuditError>();
  private readonly locks = new Map<string, Promise<void>>();

  public constructor(
    private readonly storageRoot: string,
    private readonly now: () => Date,
    private readonly randomId: () => string,
  ) {}

  public async create(plan: PublishedPlan, workspaceId: string): Promise<SessionState> {
    this.assertSafeId(workspaceId, '工作区标识');
    return this.withLock(`workspace:${workspaceId}`, async () => {
      const active = await this.findActive(workspaceId);
      if (active !== undefined) {
        throw new AuditError(
          'session_conflict',
          '当前工作区已有未结束的行为采集会话。',
          '请先恢复、结束或放弃已有会话。',
        );
      }

      const validPlan = validatePlan(plan);
      const sessionId = this.randomId();
      this.assertSafeId(sessionId, '会话标识');
      const location = this.location(workspaceId, sessionId);
      const timestamp = this.now().toISOString();
      const state: SessionState = {
        schema_version: SESSION_SCHEMA_VERSION,
        session_id: sessionId,
        workspace_id: workspaceId,
        status: 'collecting',
        plan_id: validPlan.plan_id,
        plan_version: validPlan.version,
        plan_content_sha256: validPlan.content_sha256,
        started_at: timestamp,
        updated_at: timestamp,
        last_event_seq: 0,
        last_persisted_seq: 0,
        last_flushed_at: timestamp,
      };

      try {
        await mkdir(dirname(location.directory), { recursive: true });
        await mkdir(location.directory, { recursive: false });
        await writeJsonAtomic(join(location.directory, 'plan_snapshot.json'), validPlan);
        await writeFile(join(location.directory, 'events.jsonl'), new Uint8Array(), { flag: 'wx' });
        await writeJsonAtomic(join(location.directory, 'session_state.json'), state);
        await writeJsonAtomic(this.activePointerPath(workspaceId), { session_id: sessionId });
      } catch (error) {
        throw this.storageWriteFailed('无法创建本地会话文件。', error);
      }

      this.sessionLocations.set(sessionId, location);
      this.ownedSessions.add(sessionId);
      return state;
    });
  }

  public async append(sessionId: string, events: readonly AuditEvent[]): Promise<void> {
    await this.withLock(`session:${sessionId}`, async () => {
      const blockingFailure = this.blockedSessions.get(sessionId);
      if (blockingFailure !== undefined) {
        throw blockingFailure;
      }
      const state = await this.getRequired(sessionId);
      if (state.status !== 'collecting') {
        throw new AuditError(
          'session_conflict',
          '当前会话不处于采集状态。',
          '请先恢复会话或开始新会话。',
        );
      }
      if (events.length === 0) {
        return;
      }

      let expectedSequence = state.last_persisted_seq + 1;
      for (const event of events) {
        if (
          event.session_id !== sessionId ||
          event.session_seq !== expectedSequence ||
          event.event_id !== `${sessionId}:${String(expectedSequence)}`
        ) {
          throw new AuditError(
            'session_sequence_invalid',
            `会话事件应从序号 ${String(expectedSequence)} 连续写入。`,
            '请停止采集并保留本地诊断数据。',
          );
        }
        expectedSequence += 1;
      }

      try {
        const writer = this.writerFor(sessionId);
        for (const event of events) {
          await writer.append(event);
        }
        await writer.flush();

        const lastSequence = expectedSequence - 1;
        const timestamp = this.now().toISOString();
        const updated: SessionState = {
          ...state,
          updated_at: timestamp,
          last_event_seq: lastSequence,
          last_persisted_seq: lastSequence,
          last_flushed_at: timestamp,
        };
        await this.writeState(updated);
      } catch (error) {
        if (
          error instanceof AuditError &&
          ['storage_corrupt', 'storage_unavailable', 'storage_write_failed'].includes(error.code)
        ) {
          this.blockedSessions.set(sessionId, error);
        }
        throw error;
      }
    });
  }

  public async transition(
    sessionId: string,
    expected: SessionStatus,
    next: SessionStatus,
    reason?: string,
  ): Promise<SessionState> {
    return this.withLock(`session:${sessionId}`, async () => {
      const state = await this.getRequired(sessionId);
      if (state.status !== expected || !ALLOWED_TRANSITIONS[expected].includes(next)) {
        throw new AuditError(
          'session_conflict',
          `会话无法从 ${state.status} 转换为 ${next}。`,
          '请刷新会话状态后重试。',
        );
      }

      const writer = this.writers.get(sessionId);
      if (writer !== undefined) {
        await writer.flush();
        if (next !== 'collecting') {
          await writer.close();
          this.writers.delete(sessionId);
        }
      }

      const timestamp = this.now().toISOString();
      const updated: SessionState = {
        ...state,
        status: next,
        updated_at: timestamp,
        ...(reason === undefined ? {} : { status_reason: reason }),
        ...(TERMINAL_STATUSES.includes(next) ? { ended_at: timestamp } : {}),
      };
      await this.writeState(updated);
      if (TERMINAL_STATUSES.includes(next)) {
        await this.clearActivePointer(updated.workspace_id, sessionId);
        this.ownedSessions.delete(sessionId);
      } else if (next === 'collecting') {
        this.ownedSessions.add(sessionId);
      }
      return updated;
    });
  }

  public async *readEvents(sessionId: string): AsyncIterable<AuditEvent> {
    const location = await this.locateSession(sessionId);
    if (location === undefined) {
      return;
    }

    const stream = createReadStream(join(location.directory, 'events.jsonl'), { encoding: 'utf8' });
    const lines = createInterface({ input: stream, crlfDelay: Infinity });
    let lineNumber = 0;
    try {
      for await (const line of lines) {
        lineNumber += 1;
        try {
          const value = JSON.parse(line) as unknown;
          yield assertEvent(value, sessionId, lineNumber, lineNumber);
        } catch (error) {
          if (error instanceof AuditError) {
            throw error;
          }
          throw new AuditError(
            'storage_corrupt',
            `行为事件文件第 ${String(lineNumber)} 行不是有效 JSON。`,
            '请保留原文件并导出诊断信息。',
            error,
          );
        }
      }
    } catch (error) {
      if (error instanceof AuditError) {
        throw error;
      }
      throw new AuditError(
        'storage_corrupt',
        '无法完整读取行为事件文件。',
        '请保留原文件并导出诊断信息。',
        error,
      );
    } finally {
      lines.close();
      stream.destroy();
    }
  }

  public async writeArtifact(
    sessionId: string,
    kind: SessionArtifactKind,
    bytes: Uint8Array,
  ): Promise<void> {
    const fileName = ARTIFACT_FILES[kind];
    if (fileName === undefined) {
      throw this.storageWriteFailed('不允许写入未声明的会话产物。');
    }
    const location = await this.locateRequired(sessionId);
    try {
      await writeFileAtomic(join(location.directory, fileName), bytes);
    } catch (error) {
      if (error instanceof AuditError) {
        throw error;
      }
      throw this.storageWriteFailed('无法保存会话产物。', error);
    }
  }

  public async readArtifact(
    sessionId: string,
    kind: SessionArtifactKind,
  ): Promise<Uint8Array | undefined> {
    const fileName = ARTIFACT_FILES[kind];
    if (fileName === undefined) {
      return undefined;
    }
    const location = await this.locateSession(sessionId);
    if (location === undefined) {
      return undefined;
    }
    try {
      return new Uint8Array(await readFile(join(location.directory, fileName)));
    } catch (error) {
      if (isNotFound(error)) {
        return undefined;
      }
      throw new AuditError(
        'storage_unavailable',
        '无法读取会话产物。',
        '请检查本机存储权限后重试。',
        error,
      );
    }
  }

  public async findActive(workspaceId: string): Promise<SessionState | undefined> {
    this.assertSafeId(workspaceId, '工作区标识');
    let bytes;
    try {
      bytes = await readFile(this.activePointerPath(workspaceId));
    } catch (error) {
      if (isNotFound(error)) {
        return undefined;
      }
      throw new AuditError(
        'storage_unavailable',
        '无法读取工作区活动会话指针。',
        '请检查本机存储权限后重试。',
        error,
      );
    }

    let pointer: ActivePointer;
    try {
      pointer = parseActivePointer(JSON.parse(new TextDecoder().decode(bytes)) as unknown);
    } catch (error) {
      throw new AuditError(
        'storage_corrupt',
        '工作区活动会话指针损坏。',
        '请保留原文件并导出诊断信息。',
        error,
      );
    }
    this.sessionLocations.set(pointer.session_id, this.location(workspaceId, pointer.session_id));
    const state = await this.get(pointer.session_id);
    if (state === undefined || !ACTIVE_STATUSES.includes(state.status)) {
      await this.clearActivePointer(workspaceId, pointer.session_id);
      return undefined;
    }
    return state;
  }

  public async get(sessionId: string): Promise<SessionState | undefined> {
    const location = await this.locateSession(sessionId);
    if (location === undefined) {
      return undefined;
    }
    let state = await this.readState(location);
    if (state.status === 'collecting' && !this.ownedSessions.has(sessionId)) {
      const lastPersistedSequence = await this.readLastPersistedSequence(sessionId);
      const timestamp = this.now().toISOString();
      state = {
        ...state,
        status: 'interrupted',
        updated_at: timestamp,
        last_event_seq: lastPersistedSequence,
        last_persisted_seq: lastPersistedSequence,
        last_flushed_at: timestamp,
        status_reason: 'VS Code 上次退出时会话仍在采集。',
      };
      await this.writeState(state);
    }
    return state;
  }

  private async getRequired(sessionId: string): Promise<SessionState> {
    const state = await this.get(sessionId);
    if (state === undefined) {
      throw new AuditError(
        'storage_unavailable',
        '找不到本地会话。',
        '请刷新会话列表后重新选择。',
      );
    }
    return state;
  }

  private async readState(location: SessionLocation): Promise<SessionState> {
    try {
      const bytes = await readFile(join(location.directory, 'session_state.json'));
      const state = parseSessionState(JSON.parse(new TextDecoder().decode(bytes)) as unknown);
      if (state.workspace_id !== location.workspaceId) {
        throw new Error('Workspace identifier mismatch.');
      }
      return state;
    } catch (error) {
      throw new AuditError(
        'storage_corrupt',
        '本地会话状态文件损坏。',
        '请保留原文件并导出诊断信息。',
        error,
      );
    }
  }

  private async writeState(state: SessionState): Promise<void> {
    const location = await this.locateRequired(state.session_id);
    try {
      await writeJsonAtomic(join(location.directory, 'session_state.json'), state);
    } catch (error) {
      throw this.storageWriteFailed('无法更新本地会话状态。', error);
    }
  }

  private writerFor(sessionId: string): OrderedEventWriter {
    const existing = this.writers.get(sessionId);
    if (existing !== undefined) {
      return existing;
    }
    const location = this.sessionLocations.get(sessionId);
    if (location === undefined) {
      throw new AuditError(
        'storage_unavailable',
        '找不到会话事件目录。',
        '请刷新会话状态后重试。',
      );
    }
    const writer = new OrderedEventWriter(join(location.directory, 'events.jsonl'));
    this.writers.set(sessionId, writer);
    return writer;
  }

  private async readLastPersistedSequence(sessionId: string): Promise<number> {
    let lastSequence = 0;
    for await (const event of this.readEvents(sessionId)) {
      lastSequence = event.session_seq;
    }
    return lastSequence;
  }

  private async locateRequired(sessionId: string): Promise<SessionLocation> {
    const location = await this.locateSession(sessionId);
    if (location === undefined) {
      throw new AuditError(
        'storage_unavailable',
        '找不到本地会话目录。',
        '请刷新会话列表后重新选择。',
      );
    }
    return location;
  }

  private async locateSession(sessionId: string): Promise<SessionLocation | undefined> {
    this.assertSafeId(sessionId, '会话标识');
    const cached = this.sessionLocations.get(sessionId);
    if (cached !== undefined) {
      return cached;
    }

    let workspaces;
    try {
      workspaces = await readdir(join(this.storageRoot, 'workspaces'), { withFileTypes: true });
    } catch (error) {
      if (isNotFound(error)) {
        return undefined;
      }
      throw new AuditError(
        'storage_unavailable',
        '无法读取本地工作区会话目录。',
        '请检查本机存储权限后重试。',
        error,
      );
    }

    for (const workspace of workspaces) {
      if (!workspace.isDirectory() || !SAFE_ID_PATTERN.test(workspace.name)) {
        continue;
      }
      const location = this.location(workspace.name, sessionId);
      try {
        await access(join(location.directory, 'session_state.json'));
        this.sessionLocations.set(sessionId, location);
        return location;
      } catch (error) {
        if (!isNotFound(error)) {
          throw new AuditError(
            'storage_unavailable',
            '无法检查本地会话状态。',
            '请检查本机存储权限后重试。',
            error,
          );
        }
      }
    }
    return undefined;
  }

  private location(workspaceId: string, sessionId: string): SessionLocation {
    return {
      workspaceId,
      directory: join(this.storageRoot, 'workspaces', workspaceId, 'sessions', sessionId),
    };
  }

  private activePointerPath(workspaceId: string): string {
    return join(this.storageRoot, 'workspaces', workspaceId, 'active_session.json');
  }

  private async clearActivePointer(workspaceId: string, sessionId: string): Promise<void> {
    const path = this.activePointerPath(workspaceId);
    try {
      const pointer = parseActivePointer(
        JSON.parse(await readFile(path, 'utf8')) as unknown,
      );
      if (pointer.session_id === sessionId) {
        await unlink(path);
      }
    } catch (error) {
      if (!isNotFound(error)) {
        throw new AuditError(
          'storage_corrupt',
          '无法清理工作区活动会话指针。',
          '请保留原文件并导出诊断信息。',
          error,
        );
      }
    }
  }

  private assertSafeId(value: string, label: string): void {
    if (!SAFE_ID_PATTERN.test(value)) {
      throw this.storageWriteFailed(`${label}格式无效。`);
    }
  }

  private storageWriteFailed(message: string, cause?: unknown): AuditError {
    return new AuditError(
      'storage_write_failed',
      message,
      '请检查本机存储空间和权限后重试。',
      cause,
    );
  }

  private async withLock<T>(key: string, operation: () => Promise<T>): Promise<T> {
    const previous = this.locks.get(key) ?? Promise.resolve();
    const current = previous.catch(() => undefined).then(operation);
    this.locks.set(
      key,
      current.then(
        () => undefined,
        () => undefined,
      ),
    );
    return current;
  }
}
