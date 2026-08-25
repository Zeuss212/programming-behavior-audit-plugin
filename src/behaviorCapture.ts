import { INotebookTracker } from '@jupyterlab/notebook';
import { ServerConnection } from '@jupyterlab/services';

import { BehaviorEventUploader } from './behaviorEventUploader';
import { IBehaviorSegmentSink } from './behaviorSegments';
import { BehaviorTimelineBuilder } from './behaviorTimelineBuilder';
import { EditStateMachine, ITypingCompletedArgs } from './editState';
import { BehaviorEventLogger, IBehaviorContext } from './events';
import { IProfileReference } from './models/dimensionProfile';
import {
  ACTIVE_SESSION_STORAGE_KEY,
  ISessionFinalizeResponse,
  ISessionStartResponse,
  ISessionState,
  IUploadSnapshot
} from './models/session';
import { NotebookBehaviorMonitor } from './notebookMonitor';
import { PageStateMonitor } from './pageMonitor';
import {
  abandonSession,
  getSession,
  startSession
} from './services/sessionApi';

const CANONICAL_UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const PERSISTENCE_FAILURE_REASON = 'active_session_persistence_failed';

interface IBehaviorCaptureUploader extends IBehaviorSegmentSink {
  start(session: ISessionStartResponse): Promise<void>;
  resume(session: ISessionState): Promise<void>;
  finalize(requestAiAnalysis?: boolean): Promise<ISessionFinalizeResponse>;
  snapshot(): IUploadSnapshot;
  subscribe(listener: (snapshot: IUploadSnapshot) => void): () => void;
}

interface INotebookMonitorBoundary {
  start(): void;
  getCurrentContext(): IBehaviorContext;
  emitCodeInputCompleted(args: ITypingCompletedArgs): void;
}

interface IPageMonitorBoundary {
  start(): void;
}

type StartSession = typeof startSession;
type AbandonSession = typeof abandonSession;
type GetSession = typeof getSession;

export interface IBehaviorCaptureDependencies {
  storage: Storage;
  nowIso: () => string;
  startSession: StartSession;
  abandonSession: AbandonSession;
  getSession: GetSession;
  createUploader(
    serverSettings: ServerConnection.ISettings
  ): IBehaviorCaptureUploader;
  createNotebookMonitor(
    notebookTracker: INotebookTracker,
    logger: BehaviorEventLogger,
    editState: EditStateMachine
  ): INotebookMonitorBoundary;
  createPageMonitor(
    logger: BehaviorEventLogger,
    editState: EditStateMachine,
    notebookMonitor: INotebookMonitorBoundary
  ): IPageMonitorBoundary;
}

export interface IBehaviorCaptureController {
  logger: BehaviorEventLogger;
  isEnabled(): boolean;
  snapshot(): IUploadSnapshot;
  start(profile: IProfileReference): Promise<void>;
  resume(session: ISessionState): Promise<void>;
  stop(requestAiAnalysis?: boolean): Promise<ISessionFinalizeResponse>;
  subscribe(listener: (snapshot: IUploadSnapshot) => void): () => void;
}

export function readActiveSessionId(
  storage: Storage = localStorage
): string | null {
  let stored: string | null;
  try {
    stored = storage.getItem(ACTIVE_SESSION_STORAGE_KEY);
  } catch {
    return null;
  }
  if (stored === null) {
    return null;
  }
  if (!CANONICAL_UUID_PATTERN.test(stored)) {
    try {
      storage.removeItem(ACTIVE_SESSION_STORAGE_KEY);
    } catch {
      // Invalid state is absent even when browser policy prevents cleanup.
    }
    return null;
  }
  return stored;
}

export async function getStoredActiveSession(
  serverSettings: ServerConnection.ISettings,
  storage: Storage = localStorage,
  getSessionRequest: GetSession = getSession
): Promise<ISessionState | null> {
  const sessionId = readActiveSessionId(storage);
  if (sessionId === null) {
    return null;
  }
  return getSessionRequest(serverSettings, sessionId);
}

export function startBehaviorCapture(
  notebookTracker: INotebookTracker,
  serverSettings: ServerConnection.ISettings,
  dependencyOverrides: Partial<IBehaviorCaptureDependencies> = {}
): IBehaviorCaptureController {
  const dependencies: IBehaviorCaptureDependencies = {
    storage: localStorage,
    nowIso: () => new Date().toISOString(),
    startSession,
    abandonSession,
    getSession,
    createUploader: settings => new BehaviorEventUploader(settings),
    createNotebookMonitor: (tracker, logger, editState) =>
      new NotebookBehaviorMonitor(tracker, logger, editState),
    createPageMonitor: (logger, editState, notebookMonitor) =>
      new PageStateMonitor(
        logger,
        editState,
        notebookMonitor as NotebookBehaviorMonitor
      ),
    ...dependencyOverrides
  };
  const uploader = dependencies.createUploader(serverSettings);
  const timelineBuilder = new BehaviorTimelineBuilder(uploader);
  const logger = new BehaviorEventLogger(timelineBuilder);
  logger.setEnabled(false);
  let notebookMonitor: INotebookMonitorBoundary | null = null;
  let activationPromise: Promise<void> | null = null;

  const editState = new EditStateMachine(
    logger,
    () => notebookMonitor?.getCurrentContext() ?? {},
    args => {
      notebookMonitor?.emitCodeInputCompleted(args);
    }
  );

  notebookMonitor = dependencies.createNotebookMonitor(
    notebookTracker,
    logger,
    editState
  );
  const pageMonitor = dependencies.createPageMonitor(
    logger,
    editState,
    notebookMonitor
  );
  notebookMonitor.start();
  pageMonitor.start();
  timelineBuilder.reset();
  editState.reset();

  const rollbackServerSession = async (sessionId: string): Promise<void> => {
    try {
      dependencies.storage.removeItem(ACTIVE_SESSION_STORAGE_KEY);
    } catch {
      // The server rollback remains authoritative when local cleanup is denied.
    }
    try {
      await dependencies.abandonSession(
        serverSettings,
        sessionId,
        PERSISTENCE_FAILURE_REASON
      );
    } catch {
      // Best effort: preserve the original transaction failure for the caller.
    }
  };

  const runStart = async (profile: IProfileReference): Promise<void> => {
    const current = uploader.snapshot();
    if (
      current.sessionId !== null &&
      current.uploadState !== 'idle' &&
      current.uploadState !== 'finalized'
    ) {
      throw new Error('An upload session is already active.');
    }

    const session = await dependencies.startSession(serverSettings, profile);
    if (!CANONICAL_UUID_PATTERN.test(session.session_id)) {
      throw new Error('Server session ID must be a canonical lowercase UUID.');
    }

    try {
      dependencies.storage.setItem(
        ACTIVE_SESSION_STORAGE_KEY,
        session.session_id
      );
    } catch (error) {
      await rollbackServerSession(session.session_id);
      throw error;
    }

    try {
      await uploader.start(session);
    } catch (error) {
      await rollbackServerSession(session.session_id);
      throw error;
    }
    timelineBuilder.reset();
    editState.reset();
    logger.setEnabled(true);
  };

  const runResume = async (session: ISessionState): Promise<void> => {
    if (session.status !== 'collecting') {
      throw new Error('Only a collecting capture session can be resumed.');
    }
    if (!CANONICAL_UUID_PATTERN.test(session.session_id)) {
      throw new Error('Server session ID must be a canonical lowercase UUID.');
    }
    if (readActiveSessionId(dependencies.storage) !== session.session_id) {
      throw new Error(
        'Stored active session does not match the server session.'
      );
    }

    await uploader.resume(session);
    timelineBuilder.reset();
    editState.reset();
    logger.setEnabled(true);
  };

  const activate = (operation: Promise<void>): Promise<void> => {
    activationPromise = operation;
    operation.then(
      () => {
        if (activationPromise === operation) {
          activationPromise = null;
        }
      },
      () => {
        if (activationPromise === operation) {
          activationPromise = null;
        }
      }
    );
    return operation;
  };

  const startCapture = (profile: IProfileReference): Promise<void> => {
    if (activationPromise !== null) {
      return Promise.reject(
        new Error('A capture session is already starting or active.')
      );
    }
    return activate(runStart(profile));
  };

  const resumeCapture = (session: ISessionState): Promise<void> => {
    if (activationPromise !== null) {
      return Promise.reject(
        new Error('A capture session is already starting or active.')
      );
    }
    return activate(runResume(session));
  };

  return {
    logger,
    isEnabled: () => logger.isEnabled(),
    snapshot: () => uploader.snapshot(),
    subscribe: listener => uploader.subscribe(listener),
    start: startCapture,
    resume: resumeCapture,
    stop: async (requestAiAnalysis = true) => {
      const pendingActivation = activationPromise;
      if (pendingActivation !== null) {
        await pendingActivation;
      }
      editState.close('context_change');
      timelineBuilder.closeObservation(
        dependencies.nowIso(),
        notebookMonitor?.getCurrentContext() ?? {}
      );
      const response = await uploader.finalize(requestAiAnalysis);
      logger.setEnabled(false);
      timelineBuilder.reset();
      editState.reset();
      try {
        dependencies.storage.removeItem(ACTIVE_SESSION_STORAGE_KEY);
      } catch {
        // Finalized server state is authoritative; stale local state is recoverable.
      }
      return response;
    }
  };
}
