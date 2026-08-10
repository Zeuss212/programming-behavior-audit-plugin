import { IQueuedBehaviorSegment } from './models/session';
import { canonicalStringify } from './utils/canonicalJson';

const DATABASE_NAME = 'myextension-behavior-audit';
const DATABASE_VERSION = 1;
const STORE_NAME = 'unconfirmed-segments';

export type DurableStorageErrorCode =
  | 'durable_storage_unavailable'
  | 'durable_storage_invalid';

export class DurableStorageError extends Error {
  constructor(
    readonly code: DurableStorageErrorCode,
    message: string
  ) {
    super(message);
    this.name = 'DurableStorageError';
  }
}

export interface IDurableSegmentStore {
  load(sessionId: string): Promise<IQueuedBehaviorSegment[]>;
  append(sessionId: string, segment: IQueuedBehaviorSegment): Promise<void>;
  removeThrough(sessionId: string, sessionSequence: number): Promise<void>;
  clear(sessionId: string): Promise<void>;
}

interface IStoredSegment {
  session_id: string;
  session_seq: number;
  segment: IQueuedBehaviorSegment;
}

function invalid(message: string): DurableStorageError {
  return new DurableStorageError('durable_storage_invalid', message);
}

function unavailable(): DurableStorageError {
  return new DurableStorageError(
    'durable_storage_unavailable',
    'Durable behavior storage is unavailable.'
  );
}

function validateSessionId(sessionId: string): void {
  if (typeof sessionId !== 'string' || sessionId.length === 0) {
    throw invalid('Durable behavior session id is invalid.');
  }
}

function validateSequence(
  sequence: number,
  options: { allowZero: boolean }
): void {
  const minimum = options.allowZero ? 0 : 1;
  if (!Number.isSafeInteger(sequence) || sequence < minimum) {
    throw invalid('Durable behavior sequence is invalid.');
  }
}

function validateSegment(
  sessionId: string,
  segment: IQueuedBehaviorSegment
): void {
  validateSessionId(sessionId);
  if (segment === null || typeof segment !== 'object') {
    throw invalid('Durable behavior segment is invalid.');
  }
  validateSequence(segment.session_seq, { allowZero: false });
  if (segment.event_id !== `${sessionId}:${segment.session_seq}`) {
    throw invalid('Durable behavior event identity is invalid.');
  }
  try {
    canonicalStringify(segment);
  } catch {
    throw invalid('Durable behavior segment is not canonical JSON.');
  }
}

export class IndexedDbDurableSegmentStore implements IDurableSegmentStore {
  private readonly factory: IDBFactory | null;
  private databasePromise: Promise<IDBDatabase> | null = null;

  constructor(factory: IDBFactory | null = globalThis.indexedDB ?? null) {
    this.factory = factory;
  }

  async load(sessionId: string): Promise<IQueuedBehaviorSegment[]> {
    validateSessionId(sessionId);
    const rows: IStoredSegment[] = [];
    await this.withCursor('readonly', cursor => {
      const row = cursor.value as IStoredSegment;
      if (row.session_id === sessionId) {
        rows.push(row);
      }
    });
    rows.sort((left, right) => left.session_seq - right.session_seq);
    return rows.map(row => row.segment);
  }

  async append(
    sessionId: string,
    segment: IQueuedBehaviorSegment
  ): Promise<void> {
    validateSegment(sessionId, segment);
    const database = await this.openDatabase();
    await new Promise<void>((resolve, reject) => {
      let transaction: IDBTransaction;
      try {
        transaction = database.transaction(STORE_NAME, 'readwrite');
      } catch {
        reject(unavailable());
        return;
      }
      const objectStore = transaction.objectStore(STORE_NAME);
      let operationError: DurableStorageError | null = null;
      const request = objectStore.get([sessionId, segment.session_seq]);
      request.onsuccess = () => {
        const existing = request.result as IStoredSegment | undefined;
        if (existing !== undefined) {
          let existingCanonical: string;
          let incomingCanonical: string;
          try {
            existingCanonical = canonicalStringify(existing.segment);
            incomingCanonical = canonicalStringify(segment);
          } catch {
            operationError = invalid(
              'Durable behavior replay content is invalid.'
            );
            transaction.abort();
            return;
          }
          if (existingCanonical !== incomingCanonical) {
            operationError = invalid(
              'Durable behavior sequence already has different content.'
            );
            transaction.abort();
          }
          return;
        }
        objectStore.add({
          session_id: sessionId,
          session_seq: segment.session_seq,
          segment
        } satisfies IStoredSegment);
      };
      request.onerror = () => {
        operationError = unavailable();
      };
      transaction.oncomplete = () => resolve();
      transaction.onabort = () => reject(operationError ?? unavailable());
      transaction.onerror = () => {
        operationError ??= unavailable();
      };
    });
  }

  async removeThrough(
    sessionId: string,
    sessionSequence: number
  ): Promise<void> {
    validateSessionId(sessionId);
    validateSequence(sessionSequence, { allowZero: true });
    await this.withCursor('readwrite', cursor => {
      const row = cursor.value as IStoredSegment;
      if (row.session_id === sessionId && row.session_seq <= sessionSequence) {
        cursor.delete();
      }
    });
  }

  async clear(sessionId: string): Promise<void> {
    validateSessionId(sessionId);
    await this.withCursor('readwrite', cursor => {
      const row = cursor.value as IStoredSegment;
      if (row.session_id === sessionId) {
        cursor.delete();
      }
    });
  }

  private async withCursor(
    mode: IDBTransactionMode,
    visit: (cursor: IDBCursorWithValue) => void
  ): Promise<void> {
    const database = await this.openDatabase();
    await new Promise<void>((resolve, reject) => {
      let transaction: IDBTransaction;
      try {
        transaction = database.transaction(STORE_NAME, mode);
      } catch {
        reject(unavailable());
        return;
      }
      const request = transaction.objectStore(STORE_NAME).openCursor();
      request.onsuccess = () => {
        const cursor = request.result;
        if (cursor === null) {
          return;
        }
        try {
          visit(cursor);
          cursor.continue();
        } catch {
          transaction.abort();
        }
      };
      transaction.oncomplete = () => resolve();
      transaction.onabort = () => reject(unavailable());
      transaction.onerror = () => reject(unavailable());
    });
  }

  private openDatabase(): Promise<IDBDatabase> {
    if (this.factory === null) {
      return Promise.reject(unavailable());
    }
    if (this.databasePromise !== null) {
      return this.databasePromise;
    }
    this.databasePromise = new Promise<IDBDatabase>((resolve, reject) => {
      let request: IDBOpenDBRequest;
      try {
        request = this.factory!.open(DATABASE_NAME, DATABASE_VERSION);
      } catch {
        reject(unavailable());
        return;
      }
      request.onupgradeneeded = () => {
        const database = request.result;
        if (!database.objectStoreNames.contains(STORE_NAME)) {
          database.createObjectStore(STORE_NAME, {
            keyPath: ['session_id', 'session_seq']
          });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(unavailable());
      request.onblocked = () => reject(unavailable());
    });
    return this.databasePromise;
  }
}
