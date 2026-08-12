import type { ErrorObject } from 'ajv';
import Ajv2020 from 'ajv/dist/2020';

import planV1Schema from '../../schemas/plan-v1.schema.json';
import { canonicalJson, sha256Hex } from './canonicalJson';
import { AuditError } from './errors';
import {
  PLAN_SCHEMA_VERSION,
  type JsonValue,
  type PublishedPlan,
} from './types';

const ajv = new Ajv2020({ allErrors: true, strict: true });
const validatePlanV1 = ajv.compile(planV1Schema);

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function validationMessage(errors: readonly ErrorObject[] | null | undefined): string {
  if (errors === null || errors === undefined || errors.length === 0) {
    return '方案内容不符合版本 1 数据格式。';
  }

  return errors
    .slice(0, 3)
    .map((error) => `${error.instancePath || '/'} ${error.message ?? '格式无效'}`)
    .join('；');
}

function assertUniqueIds(plan: PublishedPlan): void {
  const knowledgePointIds = plan.knowledge_points.map((item) => item.knowledge_point_id);
  const testIds = plan.tests.map((item) => item.test_id);
  if (
    new Set(knowledgePointIds).size !== knowledgePointIds.length ||
    new Set(testIds).size !== testIds.length
  ) {
    throw new AuditError(
      'import_invalid',
      '方案中存在重复的知识点或测试 ID。',
      '请修改重复 ID 后重新导入。',
    );
  }
}

export function planContentSha256(plan: Omit<PublishedPlan, 'content_sha256'>): string {
  return sha256Hex(canonicalJson(plan as unknown as JsonValue));
}

function hashWithoutDigest(plan: PublishedPlan): string {
  const entries = Object.entries(plan as unknown as Record<string, JsonValue>).filter(
    ([key]) => key !== 'content_sha256',
  );
  return sha256Hex(canonicalJson(Object.fromEntries(entries)));
}

export function validatePlan(value: unknown): PublishedPlan {
  if (
    isRecord(value) &&
    Object.hasOwn(value, 'schema_version') &&
    value.schema_version !== PLAN_SCHEMA_VERSION
  ) {
    throw new AuditError(
      'unsupported_schema_version',
      `不支持方案版本 ${String(value.schema_version)}。`,
      '请使用版本 1 的方案文件。',
    );
  }

  if (!validatePlanV1(value)) {
    throw new AuditError(
      'import_invalid',
      validationMessage(validatePlanV1.errors),
      '请检查方案字段后重新导入。',
    );
  }

  const plan = value as unknown as PublishedPlan;
  assertUniqueIds(plan);
  if (hashWithoutDigest(plan) !== plan.content_sha256) {
    throw new AuditError(
      'import_invalid',
      '方案内容与 content_sha256 校验值不一致。',
      '请重新导出原始方案后再导入。',
    );
  }

  return plan;
}
