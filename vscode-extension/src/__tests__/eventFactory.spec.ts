import { describe, expect, it } from 'vitest';

import { SessionEventFactory } from '../capture/eventFactory';

describe('SessionEventFactory', () => {
  it('creates contiguous deterministic event IDs from the persisted sequence', () => {
    let elapsed = 100;
    const factory = new SessionEventFactory(
      'session-001',
      4,
      () => new Date('2026-08-10T01:02:03.000Z'),
      () => {
        elapsed += 10;
        return elapsed;
      },
    );

    const first = factory.create({ kind: 'save', payload: {} });
    const second = factory.create({ kind: 'window_focus', payload: { focused: true } });

    expect(first).toMatchObject({
      event_id: 'session-001:5',
      session_id: 'session-001',
      session_seq: 5,
      occurred_at: '2026-08-10T01:02:03.000Z',
      monotonic_ms: 110,
    });
    expect(second).toMatchObject({ event_id: 'session-001:6', session_seq: 6, monotonic_ms: 120 });
  });
});
