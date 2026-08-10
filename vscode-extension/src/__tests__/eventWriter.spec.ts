import { mkdtemp, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { afterEach, describe, expect, it, vi } from 'vitest';

import { AUDIT_EVENT_SCHEMA_VERSION, type AuditEvent } from '../domain/types';
import {
  EVENT_BATCH_SIZE,
  EVENT_FLUSH_INTERVAL_MS,
  MAX_SESSION_EVENT_BYTES,
  OrderedEventWriter,
} from '../storage/eventWriter';

function event(sequence: number): AuditEvent {
  return {
    schema_version: AUDIT_EVENT_SCHEMA_VERSION,
    event_id: `session-1:${String(sequence)}`,
    session_id: 'session-1',
    session_seq: sequence,
    occurred_at: `2026-08-10T00:00:${String(sequence).padStart(2, '0')}.000Z`,
    monotonic_ms: sequence * 1000,
    kind: 'edit',
    payload: { change_count: 1 },
  };
}

async function readJsonl(path: string): Promise<AuditEvent[]> {
  const text = await readFile(path, 'utf8');
  return text
    .trim()
    .split('\n')
    .filter(Boolean)
    .map((line) => JSON.parse(line) as AuditEvent);
}

afterEach(() => {
  vi.useRealTimers();
});

describe('OrderedEventWriter', () => {
  it('preserves enqueue order across concurrent append calls', async () => {
    const root = await mkdtemp(join(tmpdir(), 'behavior-audit-events-'));
    const path = join(root, 'events.jsonl');
    const writer = new OrderedEventWriter(path);

    await Promise.all([writer.append(event(1)), writer.append(event(2)), writer.append(event(3))]);
    await writer.flush();

    expect((await readJsonl(path)).map((row) => row.session_seq)).toEqual([1, 2, 3]);
    await writer.close();
  });

  it('flushes automatically at the exact batch size', async () => {
    const root = await mkdtemp(join(tmpdir(), 'behavior-audit-events-'));
    const path = join(root, 'events.jsonl');
    const writer = new OrderedEventWriter(path);

    await Promise.all(
      Array.from({ length: EVENT_BATCH_SIZE }, (_, index) => writer.append(event(index + 1))),
    );

    expect(await readJsonl(path)).toHaveLength(EVENT_BATCH_SIZE);
    await writer.close();
  });

  it('flushes a partial batch after one fake-timer second', async () => {
    vi.useFakeTimers();
    const root = await mkdtemp(join(tmpdir(), 'behavior-audit-events-'));
    const path = join(root, 'events.jsonl');
    const writer = new OrderedEventWriter(path);

    await writer.append(event(1));
    await vi.advanceTimersByTimeAsync(EVENT_FLUSH_INTERVAL_MS);
    expect(writer.pendingCount).toBe(0);
    await writer.flush();

    expect((await readJsonl(path)).map((row) => row.session_seq)).toEqual([1]);
    await writer.close();
  });

  it('rejects writes beyond the per-session byte limit', async () => {
    const root = await mkdtemp(join(tmpdir(), 'behavior-audit-events-'));
    const path = join(root, 'events.jsonl');
    await writeFile(path, new Uint8Array(MAX_SESSION_EVENT_BYTES));
    const writer = new OrderedEventWriter(path);

    await writer.append(event(1));
    await expect(writer.flush()).rejects.toMatchObject({ code: 'storage_write_failed' });
  });
});
