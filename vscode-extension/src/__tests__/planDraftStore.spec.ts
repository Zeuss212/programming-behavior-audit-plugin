import { describe, expect, it } from 'vitest';

import { emptyPlanDraft } from '../plans/planDraft';
import { PlanDraftStore, type DraftState } from '../plans/planDraftStore';

class MemoryState implements DraftState {
  public value: unknown;

  public get<T>(_key: string): T | undefined {
    void _key;
    return this.value as T | undefined;
  }

  public update(_key: string, value: unknown): Promise<void> {
    this.value = value;
    return Promise.resolve();
  }
}

describe('PlanDraftStore', () => {
  it('restores a valid draft and stamps its last save time', async () => {
    const state = new MemoryState();
    const store = new PlanDraftStore(state, () => new Date('2026-08-12T10:05:00.000Z'));
    await store.save({
      ...emptyPlanDraft('2026-08-12T10:00:00.000Z'),
      problemText: '长题目',
    });

    expect(store.load()).toMatchObject({
      problemText: '长题目',
      updatedAt: '2026-08-12T10:05:00.000Z',
    });
  });

  it('returns an empty draft for corrupt state and clears saved state', async () => {
    const state = new MemoryState();
    state.value = { schemaVersion: 99, problemText: '损坏' };
    const store = new PlanDraftStore(state, () => new Date('2026-08-12T10:05:00.000Z'));

    expect(store.load()).toEqual(emptyPlanDraft('2026-08-12T10:05:00.000Z'));
    await store.clear();
    expect(state.value).toBeUndefined();
  });
});
