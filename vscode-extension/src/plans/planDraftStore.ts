import { emptyPlanDraft, parsePlanDraft, type PlanDraft } from './planDraft';

const DRAFT_KEY = 'behaviorAudit.planDraft.v1';

export interface DraftState {
  get<T>(key: string): T | undefined;
  update(key: string, value: unknown): PromiseLike<void>;
}

export class PlanDraftStore {
  public constructor(
    private readonly state: DraftState,
    private readonly now: () => Date,
  ) {}

  public load(): PlanDraft {
    return parsePlanDraft(this.state.get<unknown>(DRAFT_KEY)) ?? emptyPlanDraft(this.now().toISOString());
  }

  public async save(draft: PlanDraft): Promise<void> {
    await this.state.update(DRAFT_KEY, {
      ...draft,
      updatedAt: this.now().toISOString(),
    });
  }

  public async clear(): Promise<void> {
    await this.state.update(DRAFT_KEY, undefined);
  }
}
