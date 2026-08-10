import { appendFile, mkdir, stat } from 'node:fs/promises';
import { dirname } from 'node:path';

import { canonicalJson } from '../domain/canonicalJson';
import { AuditError } from '../domain/errors';
import type { AuditEvent, JsonValue } from '../domain/types';

export const EVENT_BATCH_SIZE = 20;
export const EVENT_FLUSH_INTERVAL_MS = 1_000;
export const SESSION_CHECKPOINT_INTERVAL_MS = 5_000;
export const MAX_EVENT_JSON_BYTES = 64 * 1024;
export const MAX_SESSION_EVENT_BYTES = 10 * 1024 * 1024;

function isNotFound(error: unknown): boolean {
  return error instanceof Error && 'code' in error && error.code === 'ENOENT';
}

function concatenate(chunks: readonly Uint8Array[]): Uint8Array {
  const total = chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0);
  const result = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}

export class OrderedEventWriter {
  private readonly pending: Uint8Array[] = [];
  private flushChain: Promise<void> = Promise.resolve();
  private flushTimer: NodeJS.Timeout | undefined;
  private persistedBytes: number | undefined;
  private failure: AuditError | undefined;
  private closed = false;

  public constructor(private readonly path: string) {}

  public get pendingCount(): number {
    return this.pending.length;
  }

  public async append(event: AuditEvent): Promise<void> {
    this.assertWritable();
    const bytes = new TextEncoder().encode(
      `${canonicalJson(event as unknown as JsonValue)}\n`,
    );
    if (bytes.byteLength > MAX_EVENT_JSON_BYTES) {
      throw new AuditError(
        'storage_write_failed',
        '单条行为事件超过 64 KiB 上限。',
        '请缩短本次事件的输出或摘要后重试。',
      );
    }

    this.pending.push(bytes);
    this.scheduleFlush();
    if (this.pending.length >= EVENT_BATCH_SIZE) {
      await this.flush();
    }
  }

  public flush(): Promise<void> {
    this.clearFlushTimer();
    this.flushChain = this.flushChain.then(async () => {
      if (this.failure !== undefined) {
        throw this.failure;
      }
      if (this.pending.length === 0) {
        return;
      }

      const batch = this.pending.splice(0, this.pending.length);
      const bytes = concatenate(batch);
      const persistedBytes = await this.readPersistedBytes();
      if (persistedBytes + bytes.byteLength > MAX_SESSION_EVENT_BYTES) {
        throw new AuditError(
          'storage_write_failed',
          '本次会话事件数据已达到 10 MiB 上限。',
          '请结束当前会话并导出已保存的数据。',
        );
      }

      try {
        await mkdir(dirname(this.path), { recursive: true });
        await appendFile(this.path, bytes);
        this.persistedBytes = persistedBytes + bytes.byteLength;
      } catch (error) {
        if (error instanceof AuditError) {
          throw error;
        }
        throw new AuditError(
          'storage_write_failed',
          '无法追加本地行为事件。',
          '请检查本机存储空间和权限后结束会话。',
          error,
        );
      }
    });

    const currentFlush = this.flushChain.catch((error: unknown) => {
      this.failure =
        error instanceof AuditError
          ? error
          : new AuditError(
              'storage_write_failed',
              '无法刷新本地行为事件。',
              '请检查本机存储后结束会话。',
              error,
            );
      throw this.failure;
    });
    this.flushChain = currentFlush;
    return currentFlush;
  }

  public async close(): Promise<void> {
    if (this.closed) {
      await this.flushChain;
      return;
    }
    this.closed = true;
    this.clearFlushTimer();
    await this.flush();
  }

  private assertWritable(): void {
    if (this.failure !== undefined) {
      throw this.failure;
    }
    if (this.closed) {
      throw new AuditError(
        'storage_write_failed',
        '事件写入器已经关闭。',
        '请开始或恢复会话后再记录事件。',
      );
    }
  }

  private scheduleFlush(): void {
    if (this.flushTimer !== undefined) {
      return;
    }
    this.flushTimer = setTimeout(() => {
      this.flushTimer = undefined;
      void this.flush().catch(() => undefined);
    }, EVENT_FLUSH_INTERVAL_MS);
  }

  private clearFlushTimer(): void {
    if (this.flushTimer !== undefined) {
      clearTimeout(this.flushTimer);
      this.flushTimer = undefined;
    }
  }

  private async readPersistedBytes(): Promise<number> {
    if (this.persistedBytes !== undefined) {
      return this.persistedBytes;
    }
    try {
      this.persistedBytes = (await stat(this.path)).size;
    } catch (error) {
      if (isNotFound(error)) {
        this.persistedBytes = 0;
      } else {
        throw new AuditError(
          'storage_unavailable',
          '无法读取行为事件文件大小。',
          '请检查本机存储权限后重试。',
          error,
        );
      }
    }
    return this.persistedBytes;
  }
}
