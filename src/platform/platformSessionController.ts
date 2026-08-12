import { ServerConnection } from '@jupyterlab/services';

import { ACTIVE_SESSION_STORAGE_KEY, ISessionState } from '../models/session';
import {
  bootstrapPlatformCapture,
  IPlatformCaptureBootstrapResponse,
  IPlatformContext
} from './contextApi';

export interface IPlatformCaptureBoundary {
  resume(session: ISessionState): Promise<void>;
}

export interface IPlatformSessionControllerDependencies {
  storage: Storage;
  bootstrap: (
    settings: ServerConnection.ISettings
  ) => Promise<IPlatformCaptureBootstrapResponse>;
}

export type PlatformSessionBootstrapResult =
  | {
      outcome: 'created' | 'resumed';
      session: ISessionState;
    }
  | {
      outcome: 'terminal';
      session: null;
    };

/** Restore a fixed classroom MonitorSession without trusting browser state. */
export class PlatformSessionController {
  private readonly dependencies: IPlatformSessionControllerDependencies;

  constructor(
    private readonly settings: ServerConnection.ISettings,
    private readonly context: IPlatformContext,
    private readonly capture: IPlatformCaptureBoundary,
    dependencies: Partial<IPlatformSessionControllerDependencies> = {}
  ) {
    this.dependencies = {
      storage: localStorage,
      bootstrap: bootstrapPlatformCapture,
      ...dependencies
    };
  }

  async bootstrap(): Promise<PlatformSessionBootstrapResult> {
    const classroom = this.context.classroom_session;
    if (this.context.mode !== 'student' || classroom === null) {
      throw new Error('Platform session bootstrap requires a student context.');
    }

    const response = await this.dependencies.bootstrap(this.settings);
    this.validateResponse(response, classroom);
    if (response.outcome === 'terminal') {
      return { outcome: 'terminal', session: null };
    }
    if (response.session === null || response.session.status !== 'collecting') {
      throw new Error(
        'Platform capture bootstrap did not return a collecting session.'
      );
    }

    const session: ISessionState = {
      ...response.session,
      schema_version: 1,
      request_id: response.request_id
    };
    try {
      this.dependencies.storage.setItem(
        ACTIVE_SESSION_STORAGE_KEY,
        session.session_id
      );
      await this.capture.resume(session);
    } catch (error) {
      try {
        this.dependencies.storage.removeItem(ACTIVE_SESSION_STORAGE_KEY);
      } catch {
        // The persisted server session stays recoverable even if cleanup is blocked.
      }
      throw error;
    }
    return { outcome: response.outcome, session };
  }

  private validateResponse(
    response: IPlatformCaptureBootstrapResponse,
    classroom: NonNullable<IPlatformContext['classroom_session']>
  ): void {
    if (
      response.assignment_id !== classroom.assignment_id ||
      response.plan_id !== classroom.plan_id ||
      response.plan_version !== classroom.plan_version
    ) {
      throw new Error(
        'Platform capture bootstrap assignment does not match context.'
      );
    }
    if (response.outcome === 'terminal') return;
    if (
      response.session === null ||
      response.session.session_id !== classroom.session_id ||
      response.session.problem_id !== classroom.profile.problem_id ||
      response.session.profile_id !== classroom.profile.profile_id ||
      response.session.profile_version !== classroom.profile.version ||
      response.session.profile_content_hash !== classroom.profile.content_hash
    ) {
      throw new Error(
        'Platform capture bootstrap session does not match context.'
      );
    }
  }
}
