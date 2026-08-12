import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

import { canonicalJson } from '../domain/canonicalJson';
import { AuditError } from '../domain/errors';
import {
  PLAN_SCHEMA_VERSION,
  type JsonValue,
  type PublishedPlan,
  type PublishPlanInput,
} from '../domain/types';
import { planContentSha256, validatePlan } from '../domain/validation';

const PLAN_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const VERSION_FILE_PATTERN = /^v([1-9]\d*)\.json$/;

export interface PlanRepository {
  list(): Promise<readonly PublishedPlan[]>;
  publish(input: PublishPlanInput): Promise<PublishedPlan>;
  import(bytes: Uint8Array): Promise<PublishedPlan>;
  export(planId: string, version: number): Promise<Uint8Array>;
  get(planId: string, version: number): Promise<PublishedPlan | undefined>;
}

function isNodeError(error: unknown): error is NodeJS.ErrnoException {
  return error instanceof Error && 'code' in error;
}

function isAlreadyExists(error: unknown): boolean {
  return isNodeError(error) && error.code === 'EEXIST';
}

function isNotFound(error: unknown): boolean {
  return isNodeError(error) && error.code === 'ENOENT';
}

function serialize(plan: PublishedPlan): Uint8Array {
  return new TextEncoder().encode(`${canonicalJson(plan as unknown as JsonValue)}\n`);
}

function parseImported(bytes: Uint8Array): PublishedPlan {
  try {
    const text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    return validatePlan(JSON.parse(text) as unknown);
  } catch (error) {
    if (error instanceof AuditError) {
      throw error;
    }
    throw new AuditError(
      'import_invalid',
      '方案文件不是有效的 UTF-8 JSON。',
      '请重新导出原始方案后再导入。',
      error,
    );
  }
}

export class FilePlanRepository implements PlanRepository {
  public constructor(
    private readonly plansRoot: string,
    private readonly now: () => Date,
    private readonly randomId: () => string,
  ) {}

  public async list(): Promise<readonly PublishedPlan[]> {
    let planDirectories;
    try {
      planDirectories = await readdir(this.plansRoot, { withFileTypes: true });
    } catch (error) {
      if (isNotFound(error)) {
        return [];
      }
      throw this.storageUnavailable('无法读取本地方案目录。', error);
    }

    const plans: PublishedPlan[] = [];
    for (const directory of planDirectories) {
      if (!directory.isDirectory() || !PLAN_ID_PATTERN.test(directory.name)) {
        continue;
      }
      const versions = await this.listVersions(directory.name);
      for (const version of versions) {
        const plan = await this.get(directory.name, version);
        if (plan !== undefined) {
          plans.push(plan);
        }
      }
    }

    return plans.sort((left, right) => {
      const timeOrder = right.published_at.localeCompare(left.published_at);
      if (timeOrder !== 0) {
        return timeOrder;
      }
      const versionOrder = right.version - left.version;
      return versionOrder !== 0 ? versionOrder : left.plan_id.localeCompare(right.plan_id);
    });
  }

  public async publish(input: PublishPlanInput): Promise<PublishedPlan> {
    const planId = input.plan_id ?? this.randomId();
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const version = await this.nextVersion(planId);
      const plan = this.buildPlan(input, planId, version);
      try {
        await this.writeExclusive(plan);
        return plan;
      } catch (error) {
        if (isAlreadyExists(error) && attempt === 0) {
          continue;
        }
        if (isAlreadyExists(error)) {
          throw new AuditError(
            'storage_write_failed',
            '方案版本同时被其他操作占用。',
            '请刷新方案列表后重试发布。',
            error,
          );
        }
        throw this.storageWriteFailed(error);
      }
    }

    throw new AuditError(
      'storage_write_failed',
      '方案发布未能分配唯一版本。',
      '请刷新方案列表后重试发布。',
    );
  }

  public async import(bytes: Uint8Array): Promise<PublishedPlan> {
    const plan = parseImported(bytes);
    const existing = await this.get(plan.plan_id, plan.version);
    if (existing !== undefined) {
      if (existing.content_sha256 === plan.content_sha256) {
        return existing;
      }
      throw new AuditError(
        'import_invalid',
        '相同方案 ID 和版本已经保存了不同内容。',
        '请由方案发布者导出新的版本后再导入。',
      );
    }

    try {
      await this.writeExclusive(plan);
      return plan;
    } catch (error) {
      if (isAlreadyExists(error)) {
        const raced = await this.get(plan.plan_id, plan.version);
        if (raced?.content_sha256 === plan.content_sha256) {
          return raced;
        }
        throw new AuditError(
          'import_invalid',
          '导入时相同版本被写入了不同内容。',
          '请刷新方案列表并核对方案来源。',
          error,
        );
      }
      throw this.storageWriteFailed(error);
    }
  }

  public async export(planId: string, version: number): Promise<Uint8Array> {
    const plan = await this.get(planId, version);
    if (plan === undefined) {
      throw new AuditError(
        'storage_unavailable',
        '找不到要导出的方案版本。',
        '请刷新方案列表后重新选择。',
      );
    }
    return serialize(plan);
  }

  public async get(planId: string, version: number): Promise<PublishedPlan | undefined> {
    if (!PLAN_ID_PATTERN.test(planId) || !Number.isSafeInteger(version) || version < 1) {
      return undefined;
    }

    let bytes;
    try {
      bytes = await readFile(this.planPath(planId, version));
    } catch (error) {
      if (isNotFound(error)) {
        return undefined;
      }
      throw this.storageUnavailable('无法读取本地方案文件。', error);
    }

    try {
      return parseImported(bytes);
    } catch (error) {
      throw new AuditError(
        'storage_corrupt',
        '本地方案文件损坏或校验失败。',
        '请保留原文件并重新导入可信方案。',
        error,
      );
    }
  }

  private buildPlan(
    input: PublishPlanInput,
    planId: string,
    version: number,
  ): PublishedPlan {
    const unsignedPlan: Omit<PublishedPlan, 'content_sha256'> = {
      schema_version: PLAN_SCHEMA_VERSION,
      plan_id: planId,
      version,
      problem_text: input.problem_text,
      knowledge_points: input.knowledge_points,
      tests: input.tests,
      published_at: this.now().toISOString(),
    };
    return validatePlan({
      ...unsignedPlan,
      content_sha256: planContentSha256(unsignedPlan),
    });
  }

  private async writeExclusive(plan: PublishedPlan): Promise<void> {
    await mkdir(join(this.plansRoot, plan.plan_id), { recursive: true });
    await writeFile(this.planPath(plan.plan_id, plan.version), serialize(plan), { flag: 'wx' });
  }

  private async nextVersion(planId: string): Promise<number> {
    const versions = await this.listVersions(planId);
    return (versions[0] ?? 0) + 1;
  }

  private async listVersions(planId: string): Promise<readonly number[]> {
    let names: string[];
    try {
      names = await readdir(join(this.plansRoot, planId));
    } catch (error) {
      if (isNotFound(error)) {
        return [];
      }
      throw this.storageUnavailable('无法读取方案版本目录。', error);
    }

    return names
      .map((name) => VERSION_FILE_PATTERN.exec(name)?.[1])
      .filter((value): value is string => value !== undefined)
      .map((value) => Number.parseInt(value, 10))
      .filter((value) => Number.isSafeInteger(value))
      .sort((left, right) => right - left);
  }

  private planPath(planId: string, version: number): string {
    return join(this.plansRoot, planId, `v${String(version)}.json`);
  }

  private storageUnavailable(message: string, cause: unknown): AuditError {
    return new AuditError('storage_unavailable', message, '请检查本机存储权限后重试。', cause);
  }

  private storageWriteFailed(cause: unknown): AuditError {
    return new AuditError(
      'storage_write_failed',
      '无法保存本地方案文件。',
      '请检查本机存储空间和权限后重试。',
      cause,
    );
  }
}
