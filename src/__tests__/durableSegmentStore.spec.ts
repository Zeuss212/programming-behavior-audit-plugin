import { IDBFactory } from 'fake-indexeddb';

import {
  IndexedDbDurableSegmentStore,
  IDurableSegmentStore
} from '../durableSegmentStore';
import { IQueuedBehaviorSegment } from '../models/session';

const SESSION_ID = '123e4567-e89b-42d3-a456-426614174000';
const OTHER_SESSION_ID = '223e4567-e89b-42d3-a456-426614174000';

function queued(
  sequence: number,
  sessionId: string = SESSION_ID
): IQueuedBehaviorSegment {
  return {
    event_id: `${sessionId}:${sequence}`,
    session_seq: sequence,
    segment_type: 'code_writing',
    started_at: `2026-08-10T09:00:${String(sequence).padStart(2, '0')}Z`,
    ended_at: `2026-08-10T09:00:${String(sequence + 1).padStart(2, '0')}Z`,
    duration_ms: 1000,
    inserted_char_count: sequence
  };
}

async function appendAll(
  store: IDurableSegmentStore,
  sessionId: string,
  segments: IQueuedBehaviorSegment[]
): Promise<void> {
  for (const segment of segments) {
    await store.append(sessionId, segment);
  }
}

describe('IndexedDbDurableSegmentStore', () => {
  let store: IndexedDbDurableSegmentStore;

  beforeAll(() => {
    Object.defineProperty(globalThis, 'structuredClone', {
      configurable: true,
      value: <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T
    });
  });

  beforeEach(() => {
    store = new IndexedDbDurableSegmentStore(new IDBFactory());
  });

  it('returns queued segments in sequence order and keeps sessions isolated', async () => {
    await store.append(SESSION_ID, queued(2));
    await store.append(OTHER_SESSION_ID, queued(1, OTHER_SESSION_ID));
    await store.append(SESSION_ID, queued(1));

    await expect(store.load(SESSION_ID)).resolves.toEqual([
      queued(1),
      queued(2)
    ]);
    await expect(store.load(OTHER_SESSION_ID)).resolves.toEqual([
      queued(1, OTHER_SESSION_ID)
    ]);
  });

  it('removes only server-confirmed sequences', async () => {
    await appendAll(store, SESSION_ID, [queued(1), queued(2), queued(3)]);
    await store.append(OTHER_SESSION_ID, queued(1, OTHER_SESSION_ID));

    await store.removeThrough(SESSION_ID, 2);

    await expect(store.load(SESSION_ID)).resolves.toEqual([queued(3)]);
    await expect(store.load(OTHER_SESSION_ID)).resolves.toEqual([
      queued(1, OTHER_SESSION_ID)
    ]);
  });

  it('clears only the requested session', async () => {
    await store.append(SESSION_ID, queued(1));
    await store.append(OTHER_SESSION_ID, queued(1, OTHER_SESSION_ID));

    await store.clear(SESSION_ID);

    await expect(store.load(SESSION_ID)).resolves.toEqual([]);
    await expect(store.load(OTHER_SESSION_ID)).resolves.toEqual([
      queued(1, OTHER_SESSION_ID)
    ]);
  });

  it('rejects an event id from another session', async () => {
    await expect(
      store.append(SESSION_ID, {
        ...queued(1),
        event_id: `${OTHER_SESSION_ID}:1`
      })
    ).rejects.toMatchObject({ code: 'durable_storage_invalid' });
  });

  it('is idempotent for an exact replay and rejects changed content', async () => {
    await store.append(SESSION_ID, queued(1));
    await store.append(SESSION_ID, queued(1));

    await expect(
      store.append(SESSION_ID, { ...queued(1), inserted_char_count: 999 })
    ).rejects.toMatchObject({ code: 'durable_storage_invalid' });
    await expect(store.load(SESSION_ID)).resolves.toEqual([queued(1)]);
  });

  it('returns a stable error when IndexedDB is unavailable', async () => {
    const unavailable = new IndexedDbDurableSegmentStore(null);

    await expect(unavailable.load(SESSION_ID)).rejects.toMatchObject({
      code: 'durable_storage_unavailable'
    });
  });
});
