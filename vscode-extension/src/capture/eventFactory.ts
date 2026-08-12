import {
  AUDIT_EVENT_SCHEMA_VERSION,
  type AuditEvent,
} from '../domain/types';
import type { AuditEventInput } from './captureController';

export class SessionEventFactory {
  private nextSequence: number;

  public constructor(
    private readonly sessionId: string,
    lastSequence: number,
    private readonly now: () => Date,
    private readonly monotonicNow: () => number,
  ) {
    this.nextSequence = lastSequence + 1;
  }

  public create(input: AuditEventInput): AuditEvent {
    const sequence = this.nextSequence;
    this.nextSequence += 1;
    return {
      schema_version: AUDIT_EVENT_SCHEMA_VERSION,
      event_id: `${this.sessionId}:${String(sequence)}`,
      session_id: this.sessionId,
      session_seq: sequence,
      occurred_at: this.now().toISOString(),
      monotonic_ms: this.monotonicNow(),
      kind: input.kind,
      ...(input.document === undefined ? {} : { document: input.document }),
      payload: input.payload,
    };
  }
}
