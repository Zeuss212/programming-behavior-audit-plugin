import { appendFile, mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { beforeEach, describe, expect, it } from 'vitest';

import {
  AUDIT_EVENT_SCHEMA_VERSION,
  PLAN_SCHEMA_VERSION,
  type AuditEvent,
  type JsonValue,
  type PublishedPlan,
} from '../domain/types';
import { canonicalJson, sha256Hex } from '../domain/canonicalJson';
import { FileSessionRepository } from '../storage/sessionRepository';

function plan(): PublishedPlan {
  const unsigned: Omit<PublishedPlan, 'content_sha256'> = {
    schema_version: PLAN_SCHEMA_VERSION,
    plan_id: 'plan-session-test',
    version: 1,
    problem_text: '实现空列表边界处理。',
    knowledge_points: [
      {
        knowledge_point_id: 'kp-empty',
        name: '空列表',
        description: '处理空列表。',
        observation_basis: '运行空列表时不抛出异常。',
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

function event(sessionId: string, sequence: number): AuditEvent {
  return {
    schema_version: AUDIT_EVENT_SCHEMA_VERSION,
    event_id: `${sessionId}:${String(sequence)}`,
    session_id: sessionId,
    session_seq: sequence,
    occurred_at: `2026-08-10T00:00:${String(sequence).padStart(2, '0')}.000Z`,
    monotonic_ms: sequence * 1000,
    kind: 'save',
    payload: {},
  };
}

describe('FileSessionRepository', () => {
  let root: string;
  let repository: FileSessionRepository;

  beforeEach(async () => {
    root = await mkdtemp(join(tmpdir(), 'behavior-audit-sessions-'));
    repository = new FileSessionRepository(
      root,
      () => new Date('2026-08-10T00:00:00.000Z'),
      () => 'session-test-001',
    );
  });

  it('creates one active session and rejects a second active session', async () => {
    const created = await repository.create(plan(), 'workspace-test-001');

    expect(created.status).toBe('collecting');
    expect(created.last_persisted_seq).toBe(0);
    await expect(repository.create(plan(), 'workspace-test-001')).rejects.toMatchObject({
      code: 'session_conflict',
    });
  });

  it('persists ordered events and rejects gaps and duplicates', async () => {
    const session = await repository.create(plan(), 'workspace-test-001');
    await repository.append(session.session_id, [event(session.session_id, 1), event(session.session_id, 2)]);

    await expect(repository.append(session.session_id, [event(session.session_id, 2)])).rejects.toMatchObject({
      code: 'session_sequence_invalid',
    });
    await expect(repository.append(session.session_id, [event(session.session_id, 4)])).rejects.toMatchObject({
      code: 'session_sequence_invalid',
    });

    const rows: AuditEvent[] = [];
    for await (const row of repository.readEvents(session.session_id)) {
      rows.push(row);
    }
    expect(rows.map((row) => row.session_seq)).toEqual([1, 2]);
    expect((await repository.get(session.session_id))?.last_persisted_seq).toBe(2);
  });

  it('recovers persisted collecting state as interrupted in a new instance', async () => {
    const session = await repository.create(plan(), 'workspace-test-001');
    await repository.append(session.session_id, [event(session.session_id, 1)]);

    const restarted = new FileSessionRepository(
      root,
      () => new Date('2026-08-10T00:05:00.000Z'),
      () => 'unused-session-id',
    );
    const recovered = await restarted.findActive('workspace-test-001');

    expect(recovered).toMatchObject({
      session_id: session.session_id,
      status: 'interrupted',
      last_persisted_seq: 1,
    });
  });

  it('enforces the finalize transition and clears the active pointer at completion', async () => {
    const session = await repository.create(plan(), 'workspace-test-001');

    await expect(
      repository.transition(session.session_id, 'collecting', 'completed'),
    ).rejects.toMatchObject({ code: 'session_conflict' });
    const finalizing = await repository.transition(
      session.session_id,
      'collecting',
      'finalizing',
    );
    const completed = await repository.transition(
      session.session_id,
      'finalizing',
      'completed',
    );

    expect(finalizing.status).toBe('finalizing');
    expect(completed.status).toBe('completed');
    expect(completed.ended_at).toBeTypeOf('string');
    expect(await repository.findActive('workspace-test-001')).toBeUndefined();
  });

  it('reports the exact corrupt JSONL line without deleting evidence', async () => {
    const session = await repository.create(plan(), 'workspace-test-001');
    await repository.append(session.session_id, [event(session.session_id, 1)]);
    const path = join(
      root,
      'workspaces',
      'workspace-test-001',
      'sessions',
      session.session_id,
      'events.jsonl',
    );
    await appendFile(path, '{broken-json}\n');

    const read = async (): Promise<void> => {
      for await (const row of repository.readEvents(session.session_id)) {
        void row;
      }
    };
    try {
      await read();
      throw new Error('Expected corrupt JSONL to be rejected.');
    } catch (error) {
      expect(error).toMatchObject({ code: 'storage_corrupt' });
      expect(error).toBeInstanceOf(Error);
      if (error instanceof Error) {
        expect(error.message).toContain('第 2 行');
      }
    }
  });

  it('atomically stores only declared session artifacts', async () => {
    const session = await repository.create(plan(), 'workspace-test-001');
    const bytes = new TextEncoder().encode('{"ok":true}\n');

    await repository.writeArtifact(session.session_id, 'operation_log', bytes);
    expect(await repository.readArtifact(session.session_id, 'operation_log')).toEqual(bytes);
    await expect(
      (
        repository as unknown as {
          writeArtifact: (sessionId: string, kind: string, value: Uint8Array) => Promise<void>;
        }
      ).writeArtifact(session.session_id, '../escape', bytes),
    ).rejects.toMatchObject({ code: 'storage_write_failed' });
  });
});
