import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { beforeEach, describe, expect, it } from 'vitest';

import type { PublishPlanInput } from '../domain/types';
import { FilePlanRepository } from '../plans/planRepository';

function input(problemText: string, planId?: string): PublishPlanInput {
  return {
    ...(planId === undefined ? {} : { plan_id: planId }),
    problem_text: problemText,
    knowledge_points: [
      {
        knowledge_point_id: 'kp-boundary',
        name: '边界条件',
        description: '处理空列表输入。',
        observation_basis: '空列表运行不会抛出异常。',
      },
    ],
    tests: [
      {
        test_id: 'test-empty',
        title: '空列表',
        description: '使用空列表调用函数。',
        expected_behavior: '返回约定的空统计结果。',
      },
    ],
  };
}

describe('FilePlanRepository', () => {
  let root: string;
  let repository: FilePlanRepository;

  beforeEach(async () => {
    root = await mkdtemp(join(tmpdir(), 'behavior-audit-plans-'));
    repository = new FilePlanRepository(
      join(root, 'plans'),
      () => new Date('2026-08-10T00:00:00.000Z'),
      () => 'plan-test-001',
    );
  });

  it('publishes immutable monotonically versioned plans', async () => {
    const first = await repository.publish(input('题目一'));
    const firstBytes = await repository.export(first.plan_id, first.version);
    const second = await repository.publish(input('题目二', first.plan_id));

    expect([first.version, second.version]).toEqual([1, 2]);
    expect((await repository.get(first.plan_id, 1))?.problem_text).toBe('题目一');
    expect(await repository.export(first.plan_id, first.version)).toEqual(firstBytes);
    expect(new Uint8Array(await readFile(join(root, 'plans', first.plan_id, 'v1.json')))).toEqual(
      firstBytes,
    );
  });

  it('lists newest versions first without mutating stored plans', async () => {
    const first = await repository.publish(input('题目一'));
    await repository.publish(input('题目二', first.plan_id));

    expect((await repository.list()).map((plan) => plan.version)).toEqual([2, 1]);
  });

  it('round-trips canonical export bytes and imports duplicates idempotently', async () => {
    const published = await repository.publish(input('可移植题目'));
    const exported = await repository.export(published.plan_id, published.version);
    const importedRepository = new FilePlanRepository(
      join(root, 'imported-plans'),
      () => new Date('2026-08-10T01:00:00.000Z'),
      () => 'unused-id',
    );

    const firstImport = await importedRepository.import(exported);
    const duplicateImport = await importedRepository.import(exported);

    expect(duplicateImport).toEqual(firstImport);
    expect(await importedRepository.export(firstImport.plan_id, firstImport.version)).toEqual(
      exported,
    );
    expect(await importedRepository.list()).toHaveLength(1);
  });

  it('rejects a plan whose contents were changed without updating its digest', async () => {
    const published = await repository.publish(input('原始题目'));
    const exported = await repository.export(published.plan_id, published.version);
    const tampered = JSON.parse(new TextDecoder().decode(exported)) as Record<string, unknown>;
    tampered.problem_text = '已被篡改';

    await expect(
      repository.import(new TextEncoder().encode(JSON.stringify(tampered))),
    ).rejects.toMatchObject({ code: 'import_invalid' });
  });
});
