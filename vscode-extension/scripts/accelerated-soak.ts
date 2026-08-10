import { strict as assert } from 'node:assert';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { DurableCaptureController } from '../src/capture/captureController';
import { canonicalJson } from '../src/domain/canonicalJson';
import {
  AUDIT_EVENT_SCHEMA_VERSION,
  PLAN_SCHEMA_VERSION,
  type AuditEvent,
  type JsonValue,
  type PublishedPlan,
} from '../src/domain/types';
import { planContentSha256 } from '../src/domain/validation';
import { FileReportService } from '../src/reports/exporter';
import { MAX_EVENT_JSON_BYTES, OrderedEventWriter } from '../src/storage/eventWriter';
import { FileSessionRepository } from '../src/storage/sessionRepository';

function plan(): PublishedPlan {
  const unsigned: Omit<PublishedPlan, 'content_sha256'> = {
    schema_version: PLAN_SCHEMA_VERSION,
    plan_id: 'plan-accelerated-soak',
    version: 1,
    problem_text: '模拟一节 40 分钟课程中的连续编程行为。',
    knowledge_points: [
      {
        knowledge_point_id: 'kp-continuity',
        name: '持续修改与验证',
        description: '通过编辑、保存和运行逐步修改程序。',
        observation_basis: '事件序号连续并记录运行结果。',
      },
    ],
    tests: [],
    published_at: '2026-08-10T00:00:00.000Z',
  };
  return { ...unsigned, content_sha256: planContentSha256(unsigned) };
}

function standaloneEvent(sequence: number): AuditEvent {
  return {
    schema_version: AUDIT_EVENT_SCHEMA_VERSION,
    event_id: `writer-soak:${String(sequence)}`,
    session_id: 'writer-soak',
    session_seq: sequence,
    occurred_at: new Date(Date.UTC(2026, 7, 10, 0, 0, sequence)).toISOString(),
    monotonic_ms: sequence * 1000,
    kind: 'edit',
    payload: { inserted_chars: 1 },
  };
}

export async function runAcceleratedSoak(): Promise<void> {
  const root = await mkdtemp(join(tmpdir(), 'behavior-audit-accelerated-soak-'));
  let monotonicMs = 0;
  let nowMs = Date.parse('2026-08-10T00:00:00.000Z');
  const now = () => new Date(nowMs);
  const monotonicNow = () => monotonicMs;
  const firstRepository = new FileSessionRepository(root, now, () => 'session-soak-001');
  const firstController = new DurableCaptureController({
    repository: firstRepository,
    workspaceId: 'workspace-soak-001',
    isTrusted: () => true,
    now,
    monotonicNow,
  });
  const session = await firstController.start(plan(), true);

  const recordSequence = async (
    controller: DurableCaptureController,
    first: number,
    last: number,
  ): Promise<void> => {
    for (let sequence = first; sequence <= last; sequence += 1) {
      monotonicMs = sequence * 1000;
      nowMs = Date.parse('2026-08-10T00:00:00.000Z') + monotonicMs;
      if (sequence === 601) {
        await controller.record({ kind: 'window_focus', payload: { focused: false } });
      } else if (sequence === 603) {
        await controller.record({ kind: 'window_focus', payload: { focused: true } });
      } else if (sequence % 100 === 0) {
        await controller.record({
          kind: 'python_run',
          payload: {
            exit_code: sequence % 200 === 0 ? 0 : 1,
            timed_out: false,
            launch_failed: false,
          },
        });
      } else {
        await controller.record({
          kind: sequence % 2 === 0 ? 'save' : 'edit',
          payload: sequence % 2 === 0 ? {} : { inserted_chars: 1 },
        });
      }
    }
  };

  await recordSequence(firstController, 1, 1200);
  await firstController.flush();
  const restartedRepository = new FileSessionRepository(
    root,
    now,
    () => 'unused-session-id',
  );
  const interrupted = await restartedRepository.findActive('workspace-soak-001');
  assert.equal(interrupted?.status, 'interrupted');
  assert.equal(interrupted?.last_persisted_seq, 1200);

  const resumedController = new DurableCaptureController({
    repository: restartedRepository,
    workspaceId: 'workspace-soak-001',
    isTrusted: () => true,
    now,
    monotonicNow,
  });
  await resumedController.resume(session.session_id);
  await recordSequence(resumedController, 1201, 2400);
  const terminal = await resumedController.finish('partial', '40 分钟加速模拟结束。');
  assert.equal(terminal.last_persisted_seq, 2400);

  const events: AuditEvent[] = [];
  for await (const event of restartedRepository.readEvents(session.session_id)) {
    events.push(event);
    assert.ok(
      Buffer.byteLength(canonicalJson(event as unknown as JsonValue), 'utf8') <=
        MAX_EVENT_JSON_BYTES,
    );
  }
  assert.equal(events.length, 2400);
  assert.deepEqual(
    events.map((event) => event.session_seq),
    Array.from({ length: 2400 }, (_, index) => index + 1),
  );

  const reports = new FileReportService(restartedRepository, now);
  const brief = await reports.materialize(session.session_id);
  assert.equal(brief.effective_observation.milliseconds, 2_395_000);
  assert.equal(brief.run_statistics.total, 24);
  assert.equal(brief.run_statistics.success, 12);
  assert.equal(brief.run_statistics.failure, 12);

  const writerPath = join(root, 'writer-queue-check.jsonl');
  const writer = new OrderedEventWriter(writerPath);
  let maximumPending = 0;
  for (let sequence = 1; sequence <= 100; sequence += 1) {
    await writer.append(standaloneEvent(sequence));
    maximumPending = Math.max(maximumPending, writer.pendingCount);
  }
  await writer.flush();
  assert.ok(maximumPending < 20);
  assert.equal(writer.pendingCount, 0);
  await writer.close();
  assert.equal((await readFile(writerPath, 'utf8')).trim().split('\n').length, 100);

  console.log(
    JSON.stringify({
      simulated_minutes: 40,
      events: events.length,
      effective_observation_ms: brief.effective_observation.milliseconds,
      maximum_writer_pending: maximumPending,
      result: 'OK',
    }),
  );
}
