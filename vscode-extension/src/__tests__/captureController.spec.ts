import { mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { beforeEach, describe, expect, it } from 'vitest';

import { DurableCaptureController } from '../capture/captureController';
import { canonicalJson, sha256Hex } from '../domain/canonicalJson';
import { AuditError } from '../domain/errors';
import {
  PLAN_SCHEMA_VERSION,
  SESSION_SCHEMA_VERSION,
  type AuditEvent,
  type JsonValue,
  type PublishedPlan,
  type SessionState,
} from '../domain/types';
import type { SessionRepository } from '../storage/sessionRepository';
import { FileSessionRepository } from '../storage/sessionRepository';

function plan(): PublishedPlan {
  const unsigned: Omit<PublishedPlan, 'content_sha256'> = {
    schema_version: PLAN_SCHEMA_VERSION,
    plan_id: 'plan-capture-test',
    version: 1,
    problem_text: '实现空列表边界处理。',
    knowledge_points: [
      {
        knowledge_point_id: 'kp-empty',
        name: '空列表',
        description: '处理空列表。',
        observation_basis: '空列表运行不抛出异常。',
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

function initialState(): SessionState {
  return {
    schema_version: SESSION_SCHEMA_VERSION,
    session_id: 'session-failure-test',
    workspace_id: 'workspace-failure-test',
    status: 'collecting',
    plan_id: 'plan-capture-test',
    plan_version: 1,
    plan_content_sha256: '0'.repeat(64),
    started_at: '2026-08-10T00:00:00.000Z',
    updated_at: '2026-08-10T00:00:00.000Z',
    last_event_seq: 0,
    last_persisted_seq: 0,
  };
}

describe('DurableCaptureController', () => {
  let repository: FileSessionRepository;

  beforeEach(async () => {
    const root = await mkdtemp(join(tmpdir(), 'behavior-audit-controller-'));
    repository = new FileSessionRepository(
      root,
      () => new Date('2026-08-10T00:00:00.000Z'),
      () => 'session-controller-test',
    );
  });

  it('requires trusted workspace and literal consent before creating a session', async () => {
    const untrusted = new DurableCaptureController({
      repository,
      workspaceId: 'workspace-controller-test',
      isTrusted: () => false,
      now: () => new Date('2026-08-10T00:00:00.000Z'),
      monotonicNow: () => 0,
    });
    await expect(untrusted.start(plan(), true)).rejects.toMatchObject({ code: 'workspace_untrusted' });

    const trusted = new DurableCaptureController({
      repository,
      workspaceId: 'workspace-controller-test',
      isTrusted: () => true,
      now: () => new Date('2026-08-10T00:00:00.000Z'),
      monotonicNow: () => 0,
    });
    await expect(
      trusted.start(plan(), false as unknown as true),
    ).rejects.toMatchObject({ code: 'session_conflict' });
  });

  it('persists contiguous events and flushes before the terminal transition', async () => {
    let elapsed = 0;
    const controller = new DurableCaptureController({
      repository,
      workspaceId: 'workspace-controller-test',
      isTrusted: () => true,
      now: () => new Date('2026-08-10T00:00:00.000Z'),
      monotonicNow: () => {
        elapsed += 10;
        return elapsed;
      },
    });
    const session = await controller.start(plan(), true);
    await controller.record({ kind: 'edit', payload: { inserted_chars: 1 } });
    await controller.record({ kind: 'save', payload: {} });
    const completed = await controller.finish('completed');

    const events: AuditEvent[] = [];
    for await (const event of repository.readEvents(session.session_id)) {
      events.push(event);
    }
    expect(events.map((event) => event.event_id)).toEqual([
      `${session.session_id}:1`,
      `${session.session_id}:2`,
    ]);
    expect(completed.status).toBe('completed');
    expect(controller.current()).toBeUndefined();
  });

  it('clears collecting context and preserves the storage error when flushing fails', async () => {
    const state = initialState();
    let appendShouldFail = true;
    const failingRepository: SessionRepository = {
      create: () => Promise.resolve(state),
      append: () => {
        if (appendShouldFail) {
          appendShouldFail = false;
          return Promise.reject(
            new AuditError(
              'storage_write_failed',
              '磁盘写入失败。',
              '请结束当前会话。',
            ),
          );
        }
        return Promise.resolve();
      },
      transition: () => Promise.resolve(state),
      readEvents: async function* () {
        await Promise.resolve();
        yield* [] as AuditEvent[];
      },
      readPlanSnapshot: () => Promise.resolve(plan()),
      writeArtifact: () => Promise.resolve(),
      readArtifact: () => Promise.resolve(undefined),
      findActive: () => Promise.resolve(state),
      get: () => Promise.resolve(state),
    };
    const contextValues: boolean[] = [];
    const controller = new DurableCaptureController({
      repository: failingRepository,
      workspaceId: state.workspace_id,
      isTrusted: () => true,
      now: () => new Date('2026-08-10T00:00:00.000Z'),
      monotonicNow: () => 1,
      setCollectingContext: (value) => {
        contextValues.push(value);
        return Promise.resolve();
      },
    });

    await controller.start(plan(), true);
    await controller.record({ kind: 'edit', payload: { inserted_chars: 1 } });
    await expect(controller.flush()).rejects.toMatchObject({ code: 'storage_write_failed' });
    expect(contextValues).toEqual([true, false]);
    expect(controller.current()).toBeUndefined();

    await controller.start(plan(), true);
    await controller.record({ kind: 'save', payload: {} });
    await expect(controller.flush()).resolves.toBeUndefined();
  });
});
