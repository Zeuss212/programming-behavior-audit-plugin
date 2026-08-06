import { ServerConnection } from '@jupyterlab/services';

import { ApiError } from '../models/apiError';
import {
  IDimensionProfileDraft,
  IDimensionProfileVersion,
  IProfileDraftInput
} from '../models/dimensionProfile';
import {
  createProfile,
  publishProfile,
  updateProfileDraft
} from '../services/profileApi';

const SAVE_DELAY_MS = 500;
const CONFLICT_MESSAGE = '草稿已在其他页面更新，请重新载入后再保存';

type DraftFactory = (generatedCode?: string) => IProfileDraftInput | null;

interface IActiveDrain {
  epoch: number;
  promise: Promise<boolean>;
}

export class GuidedProfileAutosave {
  private saveTimer: ReturnType<typeof setTimeout> | null = null;
  private draftEpoch = 0;
  private profileId: string | null = null;
  private revision: number | null = null;
  private generatedCode: string | undefined;
  private conflicted = false;
  private disposed = false;
  private dirty = false;
  private saveReady = false;
  private saveFailed = false;
  private draftFactory: DraftFactory | null = null;
  private activeDrain: IActiveDrain | null = null;
  private publishPromise: Promise<IDimensionProfileVersion | null> | null =
    null;
  private publishedProfile: IDimensionProfileVersion | null = null;

  constructor(
    private readonly settings: ServerConnection.ISettings,
    private readonly onStatus: (status: string) => void,
    private readonly onSaved?: (draft: IDimensionProfileDraft) => void
  ) {}

  get code(): string | undefined {
    return this.generatedCode;
  }

  beginDraft(seedCode?: string): void {
    this.clearSaveTimer();
    this.draftEpoch += 1;
    this.profileId = null;
    this.revision = null;
    this.generatedCode = seedCode;
    this.conflicted = false;
    this.dirty = false;
    this.saveReady = false;
    this.saveFailed = false;
    this.draftFactory = null;
    this.activeDrain = null;
    this.publishPromise = null;
    this.publishedProfile = null;
    this.onStatus('');
  }

  markChanged(factory: DraftFactory): void {
    if (this.disposed || this.conflicted) {
      return;
    }
    this.draftFactory = factory;
    this.dirty = true;
    this.saveReady = false;
    this.onStatus('有尚未保存的修改');
    this.clearSaveTimer();
    const epoch = this.draftEpoch;
    this.saveTimer = setTimeout(() => {
      this.saveTimer = null;
      if (this.disposed || epoch !== this.draftEpoch) {
        return;
      }
      this.saveReady = true;
      void this.ensureDrain(epoch);
    }, SAVE_DELAY_MS);
  }

  async flushAndWait(): Promise<boolean> {
    if (this.disposed || this.conflicted) {
      return false;
    }
    this.clearSaveTimer();
    const epoch = this.draftEpoch;
    if (this.dirty) {
      this.saveReady = true;
    }
    await this.ensureDrain(epoch);
    return (
      !this.disposed &&
      epoch === this.draftEpoch &&
      !this.conflicted &&
      !this.saveFailed &&
      !this.dirty &&
      this.activeDrain === null &&
      this.profileId !== null &&
      this.revision !== null
    );
  }

  publish(): Promise<IDimensionProfileVersion | null> {
    if (this.publishedProfile) {
      return Promise.resolve(this.publishedProfile);
    }
    if (this.publishPromise) {
      return this.publishPromise;
    }
    const epoch = this.draftEpoch;
    const operation = this.publishOnce(epoch);
    this.publishPromise = operation;
    void operation.finally(() => {
      if (epoch === this.draftEpoch && this.publishPromise === operation) {
        this.publishPromise = null;
      }
    });
    return operation;
  }

  dispose(): void {
    this.clearSaveTimer();
    this.disposed = true;
    this.draftEpoch += 1;
    this.activeDrain = null;
    this.publishPromise = null;
  }

  private async publishOnce(
    epoch: number
  ): Promise<IDimensionProfileVersion | null> {
    const saved = await this.flushAndWait();
    if (!saved || this.disposed || epoch !== this.draftEpoch) {
      return null;
    }
    const profileId = this.profileId;
    if (!profileId) {
      return null;
    }
    this.onStatus('正在发布试点方案…');
    try {
      const profile = await publishProfile(this.settings, profileId);
      if (this.disposed || epoch !== this.draftEpoch) {
        return null;
      }
      this.publishedProfile = profile;
      this.onStatus('试点方案已发布');
      return profile;
    } catch {
      if (!this.disposed && epoch === this.draftEpoch) {
        this.onStatus('发布失败，请稍后重试');
      }
      return null;
    }
  }

  private ensureDrain(epoch: number): Promise<boolean> {
    const current = this.activeDrain;
    if (current && current.epoch === epoch) {
      return current.promise;
    }
    const active: IActiveDrain = {
      epoch,
      promise: Promise.resolve(false)
    };
    active.promise = this.drainLoop(epoch).finally(() => {
      if (this.activeDrain === active) {
        this.activeDrain = null;
      }
    });
    this.activeDrain = active;
    return active.promise;
  }

  private async drainLoop(epoch: number): Promise<boolean> {
    while (
      !this.disposed &&
      epoch === this.draftEpoch &&
      !this.conflicted &&
      this.dirty &&
      this.saveReady
    ) {
      const payload = this.draftFactory?.(this.generatedCode) ?? null;
      if (!payload) {
        this.saveReady = false;
        this.saveFailed = true;
        this.onStatus('请先完成必填内容');
        return false;
      }
      this.dirty = false;
      this.saveReady = false;
      this.saveFailed = false;
      this.onStatus('正在保存草稿…');
      try {
        const saved =
          this.profileId === null || this.revision === null
            ? await createProfile(this.settings, payload)
            : await updateProfileDraft(
                this.settings,
                this.profileId,
                this.revision,
                payload
              );
        if (this.disposed || epoch !== this.draftEpoch) {
          return false;
        }
        this.commit(saved);
        this.onStatus('草稿已保存');
      } catch (error) {
        if (this.disposed || epoch !== this.draftEpoch) {
          return false;
        }
        this.dirty = true;
        this.saveReady = false;
        this.saveFailed = true;
        this.handleSaveError(error);
        return false;
      }
    }
    return (
      !this.disposed &&
      epoch === this.draftEpoch &&
      !this.conflicted &&
      !this.saveFailed &&
      !this.dirty
    );
  }

  private commit(saved: IDimensionProfileDraft): void {
    this.profileId = saved.profile_id;
    this.revision = saved.revision;
    this.generatedCode = saved.dimensions[0]?.code ?? this.generatedCode;
    this.onSaved?.(saved);
  }

  private handleSaveError(error: unknown): void {
    if (error instanceof ApiError && error.status === 409) {
      this.conflicted = true;
      this.onStatus(CONFLICT_MESSAGE);
      return;
    }
    this.onStatus('草稿保存失败，请稍后重试');
  }

  private clearSaveTimer(): void {
    if (this.saveTimer !== null) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
  }
}
