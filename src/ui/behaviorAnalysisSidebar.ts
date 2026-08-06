import { ServerConnection } from '@jupyterlab/services';
import { inspectorIcon } from '@jupyterlab/ui-components';
import { Widget } from '@lumino/widgets';

import { ACTIVE_IDLE_THRESHOLD_MS } from '../signalConfig';
import type {
  IBehaviorCaptureController,
  getStoredActiveSession
} from '../behaviorCapture';
import { ApiError } from '../models/apiError';
import { IAnalysisResult, IReviewPayload } from '../models/analysisResult';
import {
  IDimensionProfileVersion,
  IProfileReference
} from '../models/dimensionProfile';
import {
  IAnalysisJob,
  ISessionState,
  IUploadSnapshot
} from '../models/session';
import {
  getAnalysisJob,
  getSessionAnalysis,
  retryAnalysisJob,
  reviewDimension
} from '../services/analysisApi';
import { getProfileVersion, listProfiles } from '../services/profileApi';
import { abandonSession, deleteSession } from '../services/sessionApi';
import { openLogFolder } from '../services/logFolderApi';
import { ISessionLogFile, listSessionLogs } from '../services/sessionLogApi';
import { requestAPI } from '../request';
import { renderAnalysisResult } from './analysisResultView';

interface IAIConfigResponse {
  status: 'success' | 'error';
  base_url?: string;
  model?: string;
  api_key_configured?: boolean;
  api_key_preview?: string;
}

interface ILogFile {
  label: string;
  path: string;
  contents_path?: string | null;
}

interface ILatestAnalysisResponse {
  log_groups?: Array<{ category: string; files: ILogFile[] }>;
}

type TimerHandle = ReturnType<typeof setTimeout>;

export interface IBehaviorAnalysisSidebarDependencies {
  settings: ServerConnection.ISettings;
  capture: IBehaviorCaptureController;
  listProfiles: typeof listProfiles;
  getProfileVersion: typeof getProfileVersion;
  getAnalysisJob: typeof getAnalysisJob;
  getSessionAnalysis: typeof getSessionAnalysis;
  reviewDimension: typeof reviewDimension;
  retryAnalysisJob: typeof retryAnalysisJob;
  getStoredActiveSession: typeof getStoredActiveSession;
  abandonSession: typeof abandonSession;
  deleteSession: typeof deleteSession;
  openLogFolder: typeof openLogFolder;
  listSessionLogs: typeof listSessionLogs;
  openSessionLog: (sessionId: string, log: ISessionLogFile) => Promise<void>;
  downloadSessionLog: (
    sessionId: string,
    log: ISessionLogFile
  ) => Promise<void>;
  openProfileEditor: () => void;
  openDataFile: (path: string) => Promise<void>;
  confirmClearAIKey: () => Promise<boolean>;
  requestAIConfig: (init?: RequestInit) => Promise<IAIConfigResponse>;
  requestLatestAnalysis: () => Promise<ILatestAnalysisResponse>;
  setTimer: (handler: () => void, timeout: number) => TimerHandle;
  clearTimer: (handle: TimerHandle) => void;
  now: () => number;
  isDocumentActive: () => boolean;
  storage: Storage;
}

export function sidebarDependencies(
  settings: ServerConnection.ISettings,
  capture: IBehaviorCaptureController,
  actions: {
    openProfileEditor: () => void;
    openDataFile: (path: string) => Promise<void>;
    confirmClearAIKey: () => Promise<boolean>;
    getStoredActiveSession: typeof getStoredActiveSession;
    openLogFolder: typeof openLogFolder;
    openSessionLog: (sessionId: string, log: ISessionLogFile) => Promise<void>;
    downloadSessionLog: (
      sessionId: string,
      log: ISessionLogFile
    ) => Promise<void>;
  }
): IBehaviorAnalysisSidebarDependencies {
  return {
    settings,
    capture,
    listProfiles,
    getProfileVersion,
    getAnalysisJob,
    getSessionAnalysis,
    reviewDimension,
    retryAnalysisJob,
    getStoredActiveSession: actions.getStoredActiveSession,
    abandonSession,
    deleteSession,
    openLogFolder: actions.openLogFolder,
    listSessionLogs,
    openSessionLog: actions.openSessionLog,
    downloadSessionLog: actions.downloadSessionLog,
    openProfileEditor: actions.openProfileEditor,
    openDataFile: actions.openDataFile,
    confirmClearAIKey: actions.confirmClearAIKey,
    requestAIConfig: init =>
      requestAPI<IAIConfigResponse>('ai-config', settings, init),
    requestLatestAnalysis: () =>
      requestAPI<ILatestAnalysisResponse>('latest-analysis', settings),
    setTimer: (handler, timeout) => setTimeout(handler, timeout),
    clearTimer: handle => clearTimeout(handle),
    now: Date.now,
    isDocumentActive: () =>
      document.visibilityState === 'visible' && document.hasFocus(),
    storage: localStorage
  };
}

function node<K extends keyof HTMLElementTagNameMap>(
  name: K,
  className?: string
): HTMLElementTagNameMap[K] {
  const value = document.createElement(name);
  if (className) value.className = className;
  return value;
}

function compactProfileTitle(title: string): string {
  const characters = Array.from(title.trim());
  const maximum = 24;
  return characters.length <= maximum
    ? characters.join('')
    : `${characters.slice(0, maximum - 1).join('')}…`;
}

function button(text: string, primary = false): HTMLButtonElement {
  const value = node('button', 'jp-BehaviorAudit-button');
  value.type = 'button';
  value.textContent = text;
  if (primary) value.classList.add('jp-BehaviorAudit-button-primary');
  return value;
}

function aiConfigErrorDetails(error: unknown): {
  field: string;
  reason: string;
} | null {
  if (!(error instanceof ApiError)) return null;
  const details = error.details;
  if (typeof details !== 'object' || details === null) return null;
  const candidate = details as Record<string, unknown>;
  return typeof candidate.field === 'string' &&
    typeof candidate.reason === 'string'
    ? { field: candidate.field, reason: candidate.reason }
    : null;
}

function aiBaseUrlError(reason: string): string {
  if (reason === 'insecure_url') {
    return 'Base URL 必须使用 HTTPS；仅本机回环地址可以使用 HTTP。';
  }
  if (reason === 'credentials_not_allowed') {
    return 'Base URL 不能包含用户名或密码。';
  }
  if (reason === 'missing_url') {
    return '请输入 Base URL。';
  }
  return '请输入有效的 Base URL。';
}

function actionForError(code: string | null): string {
  if (code === 'ai_not_configured' || code === 'ai_analysis_failed') {
    return '请检查“AI 服务配置”，然后重试分析。';
  }
  if (code === 'ai_analysis_timeout') {
    return '分析超时，请重试；如反复出现，请减少维度或事件量，或将分析超时设置调高（60–180 秒）。';
  }
  if (code === 'ai_provider_network_error') {
    return '无法连接 AI 服务，请检查网络、DNS 或 TLS/代理配置后重试。';
  }
  if (code === 'ai_provider_rate_limited') {
    return 'AI 服务限流或额度不足，请稍后重试并检查额度或并发限制。';
  }
  if (code === 'ai_provider_auth_failed') {
    return 'AI 服务鉴权失败，请检查 API Key 和模型权限后重试。';
  }
  if (code === 'ai_provider_request_rejected') {
    return 'AI 请求被拒绝，请检查 Base URL 和模型名是否匹配服务配置。';
  }
  if (code === 'ai_provider_unavailable') {
    return 'AI 服务暂不可用，请稍后重试。';
  }
  if (code === 'ai_response_truncated') {
    return 'AI 输出过长且二次请求仍被截断，请减少维度或事件量后重试。';
  }
  if (code === 'ai_response_invalid') {
    return 'AI 输出格式无效，请重试；如反复出现，请检查模型是否支持结构化 JSON 输出。';
  }
  if (code === 'model_timeout') return '模型响应超时，可以重试分析。';
  if (code === 'analysis_output_invalid' || code === 'invalid_profile') {
    return '请重试并检查维度定义。';
  }
  if (code === 'session_not_finalized') {
    return '请重试上传/结束，确保完整会话已提交。';
  }
  if (code === 'input_snapshot_mismatch' || code === 'analysis_input_invalid')
    return '请保留数据并联系管理员；当前结果不可用。';
  if (
    code === 'analysis_artifact_write_failed' ||
    code === 'analysis_commit_failed' ||
    code === 'analysis_worker_failed'
  ) {
    return '服务器分析失败，请刷新状态或重试分析。';
  }
  return '服务器暂时未完成分析，可刷新状态或重试。';
}

function requiredObservationDurationMs(
  profile: IDimensionProfileVersion | null
): number | null {
  if (!profile) return null;
  let requiredMs = 0;
  for (const dimension of profile.dimensions) {
    const candidate =
      dimension.analysis_config.minimum_observation
        .valid_observation_duration_ms;
    if (
      typeof candidate === 'number' &&
      Number.isFinite(candidate) &&
      candidate > requiredMs
    ) {
      requiredMs = candidate;
    }
  }
  return requiredMs > 0 ? requiredMs : null;
}

function analysisStatusLabel(status: IAnalysisJob['status']): string {
  switch (status) {
    case 'queued':
      return '已排队';
    case 'running':
      return '分析中';
    case 'ready':
      return '分析完成';
    case 'partial':
      return '分析完成（部分结果）';
    case 'error':
      return '分析失败';
  }
}

function analysisHasUsableConclusion(
  analysis: IAnalysisResult | null
): boolean {
  return Boolean(
    analysis?.dimension_results.some(
      value =>
        value.decision.status === 'resolved' ||
        (value.ai_result !== null && value.ai_result !== undefined)
    )
  );
}

function formatDurationMs(durationMs: number): string {
  return `${(Math.max(0, durationMs) / 1000).toFixed(1)} 秒`;
}

function formatFileSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
}

function emptySessionLogs(): [
  ISessionLogFile,
  ISessionLogFile,
  ISessionLogFile
] {
  return [
    {
      kind: 'operation',
      filename: 'operation_log.json',
      label: '操作日志',
      description: '用户输入、删除、粘贴、运行成功/失败及输出。',
      status: 'pending',
      media_type: 'application/json; charset=utf-8',
      size_bytes: null,
      generated_at: null,
      error_code: null
    },
    {
      kind: 'process',
      filename: 'process_log.md',
      label: '过程日志',
      description: '按时间顺序整理输入、修改、动作间停顿和运行结果。',
      status: 'pending',
      media_type: 'text/markdown; charset=utf-8',
      size_bytes: null,
      generated_at: null,
      error_code: null
    },
    {
      kind: 'analysis',
      filename: 'analysis_log.json',
      label: 'AI 分析日志',
      description: '维度结论、数据质量、行为证据与分析来源。',
      status: 'pending',
      media_type: 'application/json; charset=utf-8',
      size_bytes: null,
      generated_at: null,
      error_code: null
    }
  ];
}

const EMPTY_UPLOAD: IUploadSnapshot = {
  sessionId: null,
  uploadState: 'idle',
  eventCount: 0,
  queuedCount: 0,
  lastSequence: 0,
  lastServerSequence: 0,
  validObservationDurationMs: 0,
  pageAwayDurationMs: 0,
  observationAnchorAt: null
};

export class BehaviorAnalysisSidebar extends Widget {
  private profiles: IDimensionProfileVersion[] = [];
  private selectedProfileId = '';
  private consent = false;
  private upload: IUploadSnapshot;
  private job: IAnalysisJob | null = null;
  private analysis: IAnalysisResult | null = null;
  private analysisProfile: IDimensionProfileVersion | null = null;
  private currentSessionId: string | null = null;
  private pollTimer: TimerHandle | null = null;
  private observationTimer: TimerHandle | null = null;
  private pollStartedAt: number | null = null;
  private pollIndex = 0;
  private unsubscribe: (() => void) | null = null;
  private notice = '';
  private noticeTone: 'error' | 'info' = 'info';
  private pendingSession: ISessionState | null = null;
  private actionPending = false;
  private stopFailed = false;
  private insufficientStopWarning = false;
  private refreshInFlight: Promise<void> | null = null;
  private generation = 0;
  private openDetails = new Set<string>();
  private interactiveValues = new Map<string, string>();
  private interactiveChecks = new Map<string, boolean>();
  private aiLoaded = false;
  private aiLoading: Promise<void> | null = null;
  private aiBaseUrl = '';
  private aiModel = '';
  private aiKeyConfigured = false;
  private aiStatus = '正在读取 AI 配置…';
  private deletedSessionIds = new Set<string>();
  private deleteFeedback = new Map<string, string>();
  private aiSectionNode: HTMLDetailsElement | null = null;
  private aiSaveInFlight: Promise<IAIConfigResponse> | null = null;
  private aiClearInFlight: Promise<void> | null = null;
  private renderedResultNode: HTMLElement | null = null;
  private renderedResultAnalysis: IAnalysisResult | null = null;
  private renderedResultProfile: IDimensionProfileVersion | null = null;
  private renderedResultSessionId: string | null = null;
  private renderedResultOwner: object | null = null;
  private retryInFlight: {
    promise: Promise<IAnalysisJob>;
    generation: number;
    sessionId: string;
    jobId: string;
  } | null = null;
  private deleteInFlight: {
    promise: ReturnType<typeof deleteSession>;
    generation: number;
    sessionId: string;
  } | null = null;
  private abandonInFlight: {
    promise: Promise<ISessionState>;
    generation: number;
    sessionId: string;
  } | null = null;
  private logFolderOpenRequest: object | null = null;
  private logFolderFeedback: {
    message: string;
    tone: 'info' | 'error';
  } | null = null;
  private logFolderOpenButton: HTMLButtonElement | null = null;
  private logFolderStatusNode: HTMLParagraphElement | null = null;
  private sessionLogs = emptySessionLogs();
  private sessionLogsSessionId: string | null = null;
  private sessionLogsRequest = 0;
  private sessionLogsFeedback = '';

  constructor(private readonly deps: IBehaviorAnalysisSidebarDependencies) {
    const root = node('section', 'jp-BehaviorAudit-sidebar');
    super({ node: root });
    this.id = 'myextension-behavior-analysis';
    this.title.icon = inspectorIcon;
    this.title.label = '行为分析';
    this.title.caption = '编程行为分析';
    this.title.className = 'jp-BehaviorAudit-sidebarTab';
    this.upload = deps.capture.snapshot();
    this.currentSessionId = this.upload.sessionId;
    this.unsubscribe = deps.capture.subscribe(snapshot => {
      if (!this.isDisposed) {
        if (
          snapshot.sessionId !== null &&
          this.deletedSessionIds.has(snapshot.sessionId)
        ) {
          this.upload = { ...EMPTY_UPLOAD };
          this.render();
          return;
        }
        this.upload = snapshot;
        if (snapshot.sessionId) this.currentSessionId = snapshot.sessionId;
        if (!this.isObservationInsufficient()) {
          this.insufficientStopWarning = false;
        }
        if (snapshot.uploadState === 'draining') {
          this.notice = '正在上传剩余记录…';
        } else if (snapshot.uploadState === 'finalizing') {
          this.notice = '正在提交完整会话…';
        }
        this.render();
      }
    });
    this.render();
    if (this.currentSessionId) void this.refreshSessionLogs();
    void this.loadAIConfig();
    void this.refreshProfiles();
    void this.restoreStoredSession();
  }

  override dispose(): void {
    this.generation += 1;
    this.renderedResultOwner = null;
    this.stopPolling();
    this.stopObservationTimer();
    this.unsubscribe?.();
    this.unsubscribe = null;
    super.dispose();
  }

  async refreshProfiles(): Promise<void> {
    this.notice = '正在读取已发布方案…';
    this.noticeTone = 'info';
    this.render();
    try {
      const selected = this.selectedProfileId;
      this.profiles = (await this.deps.listProfiles(this.deps.settings)).filter(
        profile => profile.deployment_status === 'pilot'
      );
      this.selectedProfileId = this.profiles.some(
        profile => this.profileKey(profile) === selected
      )
        ? selected
        : '';
      this.notice = this.profiles.length === 0 ? '还没有已发布方案' : '';
    } catch {
      this.notice = '方案读取失败，请重试。';
      this.noticeTone = 'error';
    }
    this.render();
  }

  async startMonitoring(): Promise<void> {
    const profile = this.selectedProfile();
    if (
      !profile ||
      !this.consent ||
      this.actionPending ||
      this.hasUnfinishedPendingSession()
    )
      return;
    const previousSessionId = this.currentSessionId;
    const generation = ++this.generation;
    this.stopPolling();
    this.actionPending = true;
    this.notice = '正在开始监控…';
    this.noticeTone = 'info';
    this.render();
    try {
      const reference: IProfileReference = {
        problem_id: profile.problem_id,
        profile_id: profile.profile_id,
        profile_version: profile.version,
        profile_content_hash: profile.content_hash
      };
      await this.deps.capture.start(reference);
      if (!this.isCurrentGeneration(generation)) return;
      const started = this.deps.capture.snapshot();
      this.currentSessionId = started.sessionId;
      this.upload = started;
      this.job = null;
      this.analysis = null;
      this.analysisProfile = null;
      this.sessionLogs = emptySessionLogs();
      this.sessionLogsSessionId = started.sessionId;
      this.sessionLogsFeedback = '';
      this.pendingSession = null;
      this.stopFailed = false;
      this.insufficientStopWarning = false;
      this.interactiveValues.delete('delete-session-confirmation');
      if (previousSessionId)
        this.clearSessionInteractiveState(previousSessionId);
      this.consent = false;
      this.notice = '监控已开始。';
    } catch {
      if (!this.isCurrentGeneration(generation)) return;
      this.notice = '开始监控失败，请重试。';
      this.noticeTone = 'error';
    } finally {
      this.actionPending = false;
      if (this.isCurrentGeneration(generation)) {
        this.upload = this.deps.capture.snapshot();
      }
      this.render();
    }
  }

  async stopMonitoring(force = false): Promise<void> {
    if (this.actionPending) return;
    if (
      !force &&
      !this.stopFailed &&
      this.deps.capture.isEnabled() &&
      this.isObservationInsufficient()
    ) {
      this.insufficientStopWarning = true;
      this.render();
      return;
    }
    this.actionPending = true;
    this.stopFailed = false;
    this.insufficientStopWarning = false;
    this.notice = '正在上传剩余记录…';
    this.noticeTone = 'info';
    this.render();
    try {
      const finalized = await this.deps.capture.stop();
      if (this.isDisposed) return;
      if (
        this.currentSessionId !== null &&
        finalized.session_id !== this.currentSessionId
      ) {
        throw new Error(
          'Finalized session does not match the current session.'
        );
      }
      this.currentSessionId = finalized.session_id;
      this.job = {
        schema_version: 1,
        request_id: finalized.request_id,
        job_id: finalized.analysis_job_id,
        session_id: finalized.session_id,
        status: 'queued',
        active_attempt_id: null,
        attempt_ids: [],
        analysis_id: null,
        error_code: null
      };
      this.notice = '分析已排队。';
      void this.refreshSessionLogs(finalized.session_id, this.generation);
      this.startPolling();
    } catch {
      this.stopFailed = true;
      this.notice = '上传或结束失败，请重试上传/结束。';
      this.noticeTone = 'error';
    } finally {
      this.actionPending = false;
      this.upload = this.deps.capture.snapshot();
      this.render();
    }
  }

  async refreshAnalysis(): Promise<void> {
    if (this.refreshInFlight) return this.refreshInFlight;
    if (!this.job || this.actionPending || this.isDisposed) return;
    const operation = this.runRefreshAnalysis(this.generation);
    this.refreshInFlight = operation;
    this.render();
    operation.finally(() => {
      if (this.refreshInFlight === operation) {
        this.refreshInFlight = null;
        this.render();
      }
    });
    return operation;
  }

  private async runRefreshAnalysis(generation: number): Promise<void> {
    const expectedJob = this.job;
    if (!expectedJob) return;
    try {
      const fresh = await this.deps.getAnalysisJob(
        this.deps.settings,
        expectedJob.job_id
      );
      if (
        !this.isCurrentGeneration(generation) ||
        fresh.job_id !== expectedJob.job_id ||
        fresh.session_id !== this.currentSessionId
      ) {
        return;
      }
      this.job = fresh;
      this.stopFailed = false;
      if (fresh.status === 'ready' || fresh.status === 'partial') {
        await this.loadResult(fresh.session_id, generation);
        if (!this.isCurrentGeneration(generation)) return;
        if (fresh.error_code) {
          this.notice = actionForError(fresh.error_code);
          this.noticeTone = 'error';
        }
      } else if (fresh.status === 'error') {
        this.stopPolling();
        this.notice = actionForError(fresh.error_code);
        this.noticeTone = 'error';
      }
      await this.refreshSessionLogs(fresh.session_id, generation);
      if (
        fresh.status === 'partial' ||
        (fresh.status === 'ready' && !this.analysisLogIsGenerating())
      ) {
        this.stopPolling();
      }
    } catch {
      if (!this.isCurrentGeneration(generation)) return;
      this.notice = '状态刷新失败，请重试。';
      this.noticeTone = 'error';
    }
  }

  private isCurrentGeneration(generation: number): boolean {
    return !this.isDisposed && generation === this.generation;
  }

  private hasUnfinishedPendingSession(): boolean {
    return (
      this.pendingSession?.status === 'collecting' ||
      this.pendingSession?.status === 'finalizing'
    );
  }

  private profileKey(profile: IDimensionProfileVersion): string {
    return `${profile.profile_id}:${profile.version}`;
  }

  private selectedProfile(): IDimensionProfileVersion | null {
    return (
      this.profiles.find(
        profile => this.profileKey(profile) === this.selectedProfileId
      ) ?? null
    );
  }

  private displayedValidObservationDurationMs(): number {
    const confirmedMs = Math.max(0, this.upload.validObservationDurationMs);
    if (
      !this.deps.capture.isEnabled() ||
      !this.deps.isDocumentActive() ||
      !this.upload.observationAnchorAt
    ) {
      return confirmedMs;
    }
    const anchorMs = Date.parse(this.upload.observationAnchorAt);
    if (!Number.isFinite(anchorMs)) return confirmedMs;
    const provisionalMs = Math.max(0, this.deps.now() - anchorMs);
    return provisionalMs >= ACTIVE_IDLE_THRESHOLD_MS
      ? confirmedMs + provisionalMs
      : confirmedMs;
  }

  private isObservationInsufficient(): boolean {
    const requiredMs = requiredObservationDurationMs(this.selectedProfile());
    return (
      requiredMs !== null &&
      this.displayedValidObservationDurationMs() < requiredMs
    );
  }

  private startPolling(): void {
    if (this.pollTimer !== null || !this.job || this.isDisposed) return;
    this.pollStartedAt = this.deps.now();
    this.pollIndex = 1;
    this.schedulePoll(1000);
  }

  private schedulePoll(delay: number): void {
    if (this.isDisposed || this.pollTimer !== null) return;
    this.pollTimer = this.deps.setTimer(() => {
      this.pollTimer = null;
      void this.pollOnce();
    }, delay);
  }

  private async pollOnce(): Promise<void> {
    if (this.isDisposed || this.pollStartedAt === null) return;
    if (this.deps.now() - this.pollStartedAt >= 5 * 60 * 1000) {
      this.notice = '等待时间较长，请手动刷新状态。';
      this.noticeTone = 'info';
      this.render();
      return;
    }
    await this.refreshAnalysis();
    if (
      this.pollStartedAt !== null &&
      this.deps.now() - this.pollStartedAt < 5 * 60 * 1000 &&
      !this.isDisposed &&
      this.job &&
      (this.job.status === 'queued' ||
        this.job.status === 'running' ||
        (this.job.status === 'ready' && this.analysisLogIsGenerating()))
    ) {
      const delays = [1000, 2000, 4000, 8000];
      const delay = delays[this.pollIndex] ?? 10000;
      this.pollIndex += 1;
      this.schedulePoll(delay);
    } else if (
      this.pollStartedAt !== null &&
      this.deps.now() - this.pollStartedAt >= 5 * 60 * 1000
    ) {
      this.notice = '等待时间较长，请手动刷新状态。';
      this.noticeTone = 'info';
      this.stopPolling();
      this.render();
    }
  }

  private stopPolling(): void {
    if (this.pollTimer !== null) {
      this.deps.clearTimer(this.pollTimer);
      this.pollTimer = null;
    }
    this.pollStartedAt = null;
  }

  private stopObservationTimer(): void {
    if (this.observationTimer !== null) {
      this.deps.clearTimer(this.observationTimer);
      this.observationTimer = null;
    }
  }

  private syncObservationTimer(): void {
    const shouldRun =
      this.deps.capture.isEnabled() &&
      requiredObservationDurationMs(this.selectedProfile()) !== null;
    if (!shouldRun) {
      this.stopObservationTimer();
      return;
    }
    if (this.observationTimer !== null || this.isDisposed) return;
    this.observationTimer = this.deps.setTimer(() => {
      this.observationTimer = null;
      if (!this.isDisposed) {
        if (!this.isObservationInsufficient()) {
          this.insufficientStopWarning = false;
        }
        this.render();
      }
    }, 1000);
  }

  private async loadResult(
    sessionId: string,
    generation = this.generation
  ): Promise<boolean> {
    try {
      if (sessionId !== this.currentSessionId) return false;
      const analysis = await this.deps.getSessionAnalysis(
        this.deps.settings,
        sessionId
      );
      if (
        !this.isCurrentGeneration(generation) ||
        analysis.session_id !== this.currentSessionId
      ) {
        return false;
      }
      const profile = await this.deps.getProfileVersion(
        this.deps.settings,
        analysis.profile_id,
        analysis.profile_version
      );
      if (
        !this.isCurrentGeneration(generation) ||
        analysis.session_id !== this.currentSessionId
      ) {
        return false;
      }
      if (profile.content_hash !== analysis.profile_content_hash) {
        this.notice = '绑定方案校验失败；当前结果不可用。';
        this.noticeTone = 'error';
        return false;
      }
      this.analysis = { ...analysis };
      this.analysisProfile = profile;
      this.notice = '';
      return true;
    } catch {
      if (!this.isCurrentGeneration(generation)) return false;
      this.notice = '结果读取失败，请刷新状态。';
      this.noticeTone = 'error';
      return false;
    }
  }

  private async restoreStoredSession(): Promise<void> {
    const initialGeneration = this.generation;
    const initialSessionId = this.currentSessionId;
    let generation = initialGeneration;
    try {
      const session = await this.deps.getStoredActiveSession(
        this.deps.settings,
        this.deps.storage
      );
      if (
        !session ||
        !this.isCurrentGeneration(initialGeneration) ||
        this.currentSessionId !== initialSessionId
      )
        return;
      generation = ++this.generation;
      this.currentSessionId = session.session_id;
      this.pendingSession = session;
      await this.refreshSessionLogs(session.session_id, generation);
      if (session.status === 'finalized' && session.analysis_job_id) {
        try {
          this.deps.storage.removeItem('myextension:active-session');
        } catch {
          // Finalized server state remains authoritative.
        }
        this.job = await this.deps.getAnalysisJob(
          this.deps.settings,
          session.analysis_job_id
        );
        if (!this.isCurrentGeneration(generation)) return;
        if (this.job.status === 'ready' || this.job.status === 'partial') {
          await this.loadResult(session.session_id, generation);
          if (this.job.status === 'ready' && this.analysisLogIsGenerating()) {
            this.startPolling();
          }
        } else {
          this.startPolling();
        }
      } else if (session.status === 'abandoned') {
        try {
          this.deps.storage.removeItem('myextension:active-session');
        } catch {
          // Abandoned server state remains authoritative.
        }
        this.currentSessionId = null;
        this.pendingSession = null;
        this.sessionLogs = emptySessionLogs();
        this.sessionLogsSessionId = null;
        this.sessionLogsFeedback = '';
        this.notice = '检测到已放弃会话；该会话不会自动分析。';
      } else {
        this.notice = `检测到未完成会话（${session.session_id.slice(0, 8)}…），已收到 ${session.received_event_count} 条事件。`;
      }
    } catch {
      if (!this.isCurrentGeneration(generation)) return;
      this.notice = '未完成会话状态读取失败，可刷新状态。';
      this.noticeTone = 'error';
    }
    this.render();
  }

  private async abandonPending(): Promise<void> {
    const session = this.pendingSession;
    if (!session || this.abandonInFlight || !this.hasUnfinishedPendingSession())
      return;
    const generation = ++this.generation;
    const promise = this.deps.abandonSession(
      this.deps.settings,
      session.session_id,
      'teacher_abandoned_local_session'
    );
    const operation = {
      promise,
      generation,
      sessionId: session.session_id
    };
    this.abandonInFlight = operation;
    this.notice = '正在放弃未完成会话…';
    this.noticeTone = 'info';
    this.render();
    try {
      const abandoned = await promise;
      if (
        this.abandonInFlight !== operation ||
        !this.isCurrentGeneration(generation) ||
        this.currentSessionId !== session.session_id ||
        this.pendingSession?.session_id !== session.session_id
      )
        return;
      if (
        abandoned.session_id !== session.session_id ||
        abandoned.status !== 'abandoned'
      ) {
        throw new Error('Abandonment receipt does not match the session.');
      }
      try {
        this.deps.storage.removeItem('myextension:active-session');
      } catch {
        // Abandonment is server-authoritative.
      }
      this.clearSessionInteractiveState(session.session_id);
      this.currentSessionId = null;
      this.pendingSession = null;
      this.sessionLogs = emptySessionLogs();
      this.sessionLogsSessionId = null;
      this.notice = '未完成会话已放弃。';
    } catch {
      if (
        this.abandonInFlight !== operation ||
        !this.isCurrentGeneration(generation)
      )
        return;
      this.notice = '放弃未完成会话失败，请重试。';
      this.noticeTone = 'error';
    } finally {
      if (this.abandonInFlight === operation) {
        this.abandonInFlight = null;
        if (this.isCurrentGeneration(generation)) this.render();
      }
    }
  }

  private async submitReview(
    code: string,
    payload: IReviewPayload
  ): Promise<void> {
    if (
      !this.analysis ||
      this.analysis.session_id !== this.currentSessionId ||
      !this.analysisProfile
    )
      return;
    const generation = this.generation;
    const sessionId = this.analysis.session_id;
    try {
      const replacement = await this.deps.reviewDimension(
        this.deps.settings,
        sessionId,
        code,
        payload
      );
      if (
        !this.isCurrentGeneration(generation) ||
        this.analysis?.session_id !== sessionId
      )
        return;
      this.analysis = {
        ...this.analysis,
        dimension_results: this.analysis.dimension_results.map(value =>
          value.dimension_code === code ? replacement : value
        )
      };
      this.notice = '复核结果已更新。';
      this.render();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        const refreshed = await this.loadResult(sessionId, generation);
        if (!this.isCurrentGeneration(generation)) return;
        if (!refreshed) {
          this.render();
          throw error;
        }
        this.notice = '复核结果已刷新，请确认后再次提交。';
        this.noticeTone = 'error';
        this.render();
        return;
      }
      throw error;
    }
  }

  private render(): void {
    if (this.isDisposed) return;
    this.captureInteractiveState();
    this.node.textContent = '';
    const heading = node('h1');
    heading.textContent = '编程行为分析';
    const status = node('div', 'jp-BehaviorAudit-sidebarStatus');
    status.setAttribute('aria-live', 'polite');
    status.textContent = this.notice;
    if (this.noticeTone === 'error')
      status.classList.add('jp-BehaviorAudit-state-error');
    this.node.append(
      heading,
      this.profileSection(),
      this.pilotNotice(),
      this.captureSection(),
      status
    );
    if (
      this.pendingSession?.status === 'collecting' ||
      this.pendingSession?.status === 'finalizing'
    ) {
      const abandon = button('放弃未完成会话');
      abandon.disabled = this.abandonInFlight !== null;
      if (this.abandonInFlight) abandon.setAttribute('aria-busy', 'true');
      abandon.addEventListener('click', () => void this.abandonPending());
      this.node.appendChild(abandon);
    }
    this.node.append(
      this.progressSection(),
      this.resultSection(),
      this.trainingLogsSection(),
      this.aiSection(),
      this.advancedSection()
    );
    this.restoreInteractiveState();
    this.syncObservationTimer();
  }

  private captureInteractiveState(): void {
    for (const details of Array.from(
      this.node.querySelectorAll<HTMLDetailsElement>('details[data-state-key]')
    )) {
      const key = details.dataset.stateKey;
      if (!key) continue;
      if (details.open) this.openDetails.add(key);
      else this.openDetails.delete(key);
    }
    for (const input of Array.from(
      this.node.querySelectorAll<
        HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
      >('input, textarea, select')
    )) {
      const key = this.interactiveKey(input);
      if (!key) continue;
      if (input instanceof HTMLInputElement && input.type === 'radio') {
        this.interactiveChecks.set(key, input.checked);
      } else if (
        input instanceof HTMLInputElement &&
        input.type === 'checkbox'
      ) {
        this.interactiveChecks.set(key, input.checked);
      } else {
        this.interactiveValues.set(key, input.value);
      }
    }
  }

  private restoreInteractiveState(): void {
    for (const details of Array.from(
      this.node.querySelectorAll<HTMLDetailsElement>('details[data-state-key]')
    )) {
      details.open = this.openDetails.has(details.dataset.stateKey ?? '');
    }
    for (const input of Array.from(
      this.node.querySelectorAll<
        HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
      >('input, textarea, select')
    )) {
      const key = this.interactiveKey(input);
      if (!key) continue;
      if (
        input instanceof HTMLInputElement &&
        (input.type === 'radio' || input.type === 'checkbox')
      ) {
        const checked = this.interactiveChecks.get(key);
        if (checked !== undefined) input.checked = checked;
      } else {
        const value = this.interactiveValues.get(key);
        if (value !== undefined) input.value = value;
      }
    }
  }

  private interactiveKey(
    input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
  ): string | null {
    if (input.id === 'behavior-analysis-consent') return null;
    const reviewForm = input.closest<HTMLFormElement>(
      '.jp-BehaviorAudit-reviewForm'
    );
    if (reviewForm) {
      const result = input.closest<HTMLElement>('.jp-BehaviorAudit-results');
      const sessionId = result?.dataset.sessionId;
      if (!sessionId || sessionId !== this.currentSessionId) return null;
      const dimension = reviewForm.dataset.reviewDimension;
      const revision = reviewForm.dataset.reviewRevision;
      if (!dimension || revision === undefined) return null;
      const control =
        input.id ||
        `${input.getAttribute('name') ?? ''}:${
          input.getAttribute('value') ?? ''
        }`;
      return `review:${sessionId}:${dimension}:${revision}:${control}`;
    }
    const deleteContainer = input.closest<HTMLElement>(
      '.jp-BehaviorAudit-deleteSession'
    );
    if (deleteContainer) {
      const sessionId = deleteContainer.dataset.sessionId;
      if (!sessionId || sessionId !== this.currentSessionId) return null;
      return `delete:${sessionId}:${input.id || input.name}`;
    }
    return (
      input.id ||
      `${input.getAttribute('name') ?? ''}:${input.getAttribute('value') ?? ''}`
    );
  }

  private clearSessionInteractiveState(sessionId: string): void {
    this.deleteFeedback.delete(sessionId);
    const prefixes = [`review:${sessionId}:`, `delete:${sessionId}:`];
    for (const key of Array.from(this.interactiveValues.keys())) {
      if (prefixes.some(prefix => key.startsWith(prefix))) {
        this.interactiveValues.delete(key);
      }
    }
    for (const key of Array.from(this.interactiveChecks.keys())) {
      if (prefixes.some(prefix => key.startsWith(prefix))) {
        this.interactiveChecks.delete(key);
      }
    }
    for (const key of Array.from(this.openDetails)) {
      if (key.startsWith(`review:${sessionId}:`)) {
        this.openDetails.delete(key);
      }
    }
  }

  private profileSection(): HTMLElement {
    const section = node('section', 'jp-BehaviorAudit-sidebarSection');
    const label = node('label', 'jp-BehaviorAudit-label');
    label.htmlFor = 'behavior-analysis-profile';
    label.textContent = '题目与分析方案';
    const select = node('select', 'jp-BehaviorAudit-input');
    select.id = 'behavior-analysis-profile';
    const empty = node('option') as HTMLOptionElement;
    empty.value = '';
    empty.textContent = this.profiles.length
      ? '请选择已发布方案'
      : '还没有已发布方案';
    select.appendChild(empty);
    for (const profile of this.profiles) {
      const option = node('option') as HTMLOptionElement;
      option.value = this.profileKey(profile);
      const assessmentSummary =
        profile.schema_version === 2
          ? ` · ${profile.knowledge_points.length}点/${profile.assessment_tests.length}测`
          : '';
      option.textContent = `${compactProfileTitle(profile.title)} · v${profile.version}${assessmentSummary}`;
      option.title =
        `${profile.problem_id} · ${profile.title} · v${profile.version}` +
        (profile.schema_version === 2
          ? ` · ${profile.knowledge_points.length} 个知识点 / ${profile.assessment_tests.length} 个测试`
          : '');
      select.appendChild(option);
    }
    select.value = this.selectedProfileId;
    select.disabled = this.deps.capture.isEnabled();
    select.addEventListener('change', () => {
      this.selectedProfileId = select.value;
      this.consent = false;
      this.insufficientStopWarning = false;
      this.render();
    });
    section.append(label, select);
    if (!this.profiles.length) {
      const create = button('创建题目考核方案');
      create.addEventListener('click', this.deps.openProfileEditor);
      section.appendChild(create);
    }
    return section;
  }

  private pilotNotice(): HTMLElement {
    const notice = node('p', 'jp-BehaviorAudit-pilotDisclaimer');
    notice.textContent =
      'Pilot 试点：结果仅辅助观察，不用于成绩、处分或能力诊断。配置外部 AI 时，分析所需的脱敏片段可能发送给该服务。';
    return notice;
  }

  private captureSection(): HTMLElement {
    const section = node('section', 'jp-BehaviorAudit-sidebarSection');
    const active = this.deps.capture.isEnabled();
    const state = node('p', 'jp-BehaviorAudit-captureState');
    state.textContent = `监控状态：${active ? '进行中' : '已停止'}`;
    const profile = this.selectedProfile();
    if (!active && profile) {
      const consentLabel = node('label', 'jp-BehaviorAudit-checkboxField');
      const consent = node('input') as HTMLInputElement;
      consent.id = 'behavior-analysis-consent';
      consent.type = 'checkbox';
      consent.checked = this.consent;
      consent.addEventListener('change', () => {
        this.consent = consent.checked;
        this.render();
      });
      consentLabel.append(
        consent,
        document.createTextNode('我已了解本次采集和试点用途')
      );
      const explanation = node('p', 'jp-BehaviorAudit-notice');
      explanation.textContent =
        '会采集编辑、运行、报错、停顿等行为；停止后才统一分析。';
      section.append(consentLabel, explanation);
    }
    const unfinished = this.hasUnfinishedPendingSession();
    const primary = button(
      this.stopFailed
        ? '重试上传/结束'
        : active
          ? '停止监控'
          : unfinished
            ? '请先放弃未完成会话'
            : '开始监控',
      true
    );
    primary.disabled =
      this.actionPending ||
      (!active && (unfinished || !profile || !this.consent));
    primary.addEventListener(
      'click',
      () =>
        void (active || this.stopFailed
          ? this.stopMonitoring()
          : this.startMonitoring())
    );
    const counts = node('p', 'jp-BehaviorAudit-counts');
    counts.textContent = `已采集事件数：${this.upload.eventCount}；待上传数：${this.upload.queuedCount}`;
    section.append(state);
    const observationProgress = this.observationProgress();
    if (observationProgress) section.appendChild(observationProgress);
    section.append(primary, counts);
    if (this.insufficientStopWarning) {
      section.appendChild(this.stopWarning());
    }
    return section;
  }

  private observationProgress(): HTMLElement | null {
    const requiredMs = requiredObservationDurationMs(this.selectedProfile());
    if (requiredMs === null) return null;
    const validMs = this.displayedValidObservationDurationMs();
    const container = node('div', 'jp-BehaviorAudit-observationProgress');
    const summary = node('div', 'jp-BehaviorAudit-observationProgressSummary');
    const label = node('span');
    label.textContent = '有效观察时长（证据覆盖）';
    const value = node('span');
    value.textContent =
      `${(validMs / 1000).toFixed(1)} / ` + formatDurationMs(requiredMs);
    summary.append(label, value);
    const progress = node('progress');
    progress.max = requiredMs;
    progress.value = Math.min(validMs, requiredMs);
    progress.setAttribute('aria-label', '有效观察时长（证据覆盖）进度');
    const pageAway = node('p', 'jp-BehaviorAudit-observationProgressMeta');
    pageAway.textContent = `页面离开：${formatDurationMs(this.upload.pageAwayDurationMs)}（不计入有效观察）`;
    const definition = node('p', 'jp-BehaviorAudit-observationProgressMeta');
    definition.textContent =
      '统计监控期间的代码输入、删除、粘贴及页面活动时的动作间停顿。页面离开不计入；运行事件会写入日志，但运行耗时不计入该时长。';
    const threshold = node('p', 'jp-BehaviorAudit-observationProgressMeta');
    threshold.textContent =
      '达到门槛只表示行为证据覆盖足够，与日志生成或 AI 分析等待无关。';
    container.append(summary, progress, pageAway, definition, threshold);
    if (validMs >= requiredMs) {
      const complete = node(
        'p',
        'jp-BehaviorAudit-observationProgressComplete'
      );
      complete.textContent = '已达到最低要求';
      container.appendChild(complete);
    }
    return container;
  }

  private stopWarning(): HTMLElement {
    const requiredMs =
      requiredObservationDurationMs(this.selectedProfile()) ?? 0;
    const validMs = this.displayedValidObservationDurationMs();
    const alert = node('div', 'jp-BehaviorAudit-stopWarning');
    alert.setAttribute('role', 'alert');
    const text = node('p');
    text.textContent =
      `当前有效观察 ${(validMs / 1000).toFixed(1)} / ${formatDurationMs(requiredMs)}。` +
      '现在停止将得到“数据不足”。页面离开时间不计入有效观察。';
    const actions = node('div', 'jp-BehaviorAudit-inlineActions');
    const keepMonitoring = button('继续监控');
    keepMonitoring.addEventListener('click', () => {
      this.insufficientStopWarning = false;
      this.render();
    });
    const stopAnyway = button('仍要停止');
    stopAnyway.classList.add('jp-BehaviorAudit-button-danger');
    stopAnyway.addEventListener('click', () => void this.stopMonitoring(true));
    actions.append(keepMonitoring, stopAnyway);
    alert.append(text, actions);
    return alert;
  }

  private progressSection(): HTMLElement {
    const section = node('section', 'jp-BehaviorAudit-sidebarSection');
    const text = node('p');
    const failedWithoutConclusion =
      this.job?.status === 'partial' &&
      this.job.error_code === 'ai_analysis_failed' &&
      !analysisHasUsableConclusion(
        this.analysis?.session_id === this.currentSessionId
          ? this.analysis
          : null
      );
    text.textContent = this.job
      ? `分析任务：${
          failedWithoutConclusion
            ? 'AI 分析未完成'
            : analysisStatusLabel(this.job.status)
        }`
      : '分析任务：暂无';
    const refresh = button('刷新状态');
    refresh.disabled =
      !this.job || this.actionPending || this.refreshInFlight !== null;
    refresh.addEventListener('click', () => void this.refreshAnalysis());
    section.append(text, refresh);
    if (
      this.job?.status === 'error' ||
      (this.job?.status === 'partial' && this.job.error_code)
    ) {
      const integrityFailure =
        this.job.error_code === 'input_snapshot_mismatch' ||
        this.job.error_code === 'analysis_input_invalid';
      if (!integrityFailure) {
        const retry = button('重试分析');
        retry.disabled = this.retryInFlight !== null;
        if (this.retryInFlight) retry.setAttribute('aria-busy', 'true');
        retry.addEventListener('click', () => void this.retryJob());
        section.appendChild(retry);
      }
    }
    return section;
  }

  private resultSection(): HTMLElement {
    if (!this.analysis || this.analysis.session_id !== this.currentSessionId) {
      this.clearRenderedResult();
      const empty = node('section', 'jp-BehaviorAudit-resultEmpty');
      empty.setAttribute('aria-live', 'polite');
      empty.textContent = this.job
        ? '结果尚未就绪。'
        : '开始并结束一次会话后，可在这里查看结果。';
      return empty;
    }
    const profile = this.analysisProfile;
    if (!profile) {
      this.clearRenderedResult();
      const missing = node('section', 'jp-BehaviorAudit-resultEmpty');
      missing.textContent = '找不到绑定方案，无法显示教学建议。';
      return missing;
    }
    if (
      this.renderedResultNode &&
      this.renderedResultAnalysis === this.analysis &&
      this.renderedResultProfile === profile &&
      this.renderedResultSessionId === this.currentSessionId
    ) {
      return this.renderedResultNode;
    }
    const analysis = this.analysis;
    const sessionId = this.currentSessionId;
    const owner = {};
    const rendered = renderAnalysisResult(
      analysis,
      profile,
      (code, payload) => this.submitReview(code, payload),
      () =>
        !this.isDisposed &&
        this.renderedResultOwner === owner &&
        this.analysis === analysis &&
        this.analysisProfile === profile &&
        this.currentSessionId === sessionId &&
        this.node.contains(rendered)
    );
    this.renderedResultNode = rendered;
    this.renderedResultAnalysis = analysis;
    this.renderedResultProfile = profile;
    this.renderedResultSessionId = sessionId;
    this.renderedResultOwner = owner;
    return rendered;
  }

  private clearRenderedResult(): void {
    this.renderedResultOwner = null;
    this.renderedResultNode = null;
    this.renderedResultAnalysis = null;
    this.renderedResultProfile = null;
    this.renderedResultSessionId = null;
  }

  async refreshSessionLogs(
    sessionId = this.currentSessionId,
    generation = this.generation
  ): Promise<void> {
    if (!sessionId || this.isDisposed) return;
    const request = ++this.sessionLogsRequest;
    try {
      const response = await this.deps.listSessionLogs(
        sessionId,
        this.deps.settings
      );
      if (
        request !== this.sessionLogsRequest ||
        !this.isCurrentGeneration(generation) ||
        this.currentSessionId !== sessionId ||
        response.session_id !== sessionId
      ) {
        return;
      }
      this.sessionLogs = [...response.logs] as [
        ISessionLogFile,
        ISessionLogFile,
        ISessionLogFile
      ];
      this.sessionLogsSessionId = sessionId;
      this.sessionLogsFeedback = '';
    } catch {
      if (
        request === this.sessionLogsRequest &&
        this.isCurrentGeneration(generation) &&
        this.currentSessionId === sessionId
      ) {
        this.sessionLogsFeedback = '日志状态读取失败，可随分析状态刷新。';
      }
    }
    if (
      request === this.sessionLogsRequest &&
      this.isCurrentGeneration(generation)
    ) {
      this.render();
    }
  }

  private analysisLogIsGenerating(): boolean {
    return (
      this.sessionLogsSessionId === this.currentSessionId &&
      this.sessionLogs.some(
        log => log.kind === 'analysis' && log.status === 'generating'
      )
    );
  }

  private trainingLogsSection(): HTMLElement {
    const section = node('section', 'jp-BehaviorAudit-sidebarSection');
    const heading = node('h2');
    heading.textContent = '本次日志';
    const description = node('p', 'jp-BehaviorAudit-notice');
    description.textContent =
      '停止监控后会先生成操作日志和过程日志，AI 分析日志完成后再显示。';
    const list = node('div', 'jp-BehaviorAudit-sessionLogs');
    const logs =
      this.sessionLogsSessionId === this.currentSessionId
        ? this.sessionLogs
        : emptySessionLogs();
    for (const log of logs) list.appendChild(this.sessionLogRow(log));
    const feedback = node(
      'p',
      this.sessionLogsFeedback
        ? 'jp-BehaviorAudit-fieldError'
        : 'jp-BehaviorAudit-notice'
    );
    feedback.setAttribute('role', 'status');
    feedback.setAttribute('aria-live', 'polite');
    feedback.textContent = this.sessionLogsFeedback;
    section.append(heading, description, list, feedback);
    return section;
  }

  private sessionLogRow(log: ISessionLogFile): HTMLElement {
    const row = node('article', 'jp-BehaviorAudit-sessionLogRow');
    row.dataset.sessionLogKind = log.kind;
    const header = node('div', 'jp-BehaviorAudit-sessionLogHeader');
    const identity = node('div', 'jp-BehaviorAudit-sessionLogIdentity');
    const title = node('strong');
    title.textContent = log.label;
    const filename = button(log.filename);
    filename.classList.add('jp-BehaviorAudit-sessionLogFilename');
    filename.dataset.sessionLogFilename = '';
    filename.disabled = log.status !== 'ready' || !this.currentSessionId;
    if (!filename.disabled) {
      filename.addEventListener('click', () => void this.openSessionLog(log));
    }
    identity.append(title, filename);
    const status = node('span', 'jp-BehaviorAudit-sessionLogStatus');
    status.dataset.sessionLogStatus = '';
    status.setAttribute('aria-live', 'polite');
    status.textContent = this.sessionLogStatus(log);
    status.classList.add(`jp-BehaviorAudit-sessionLogStatus-${log.status}`);
    header.append(identity, status);
    const description = node('p', 'jp-BehaviorAudit-notice');
    description.textContent = log.description;
    row.append(header, description);
    if (log.status === 'ready' && this.currentSessionId) {
      const actions = node('div', 'jp-BehaviorAudit-inlineActions');
      const view = button('查看');
      view.dataset.sessionLogAction = 'view';
      view.addEventListener('click', () => void this.openSessionLog(log));
      const download = button('下载');
      download.dataset.sessionLogAction = 'download';
      download.addEventListener(
        'click',
        () => void this.downloadSessionLog(log)
      );
      actions.append(view, download);
      row.appendChild(actions);
    }
    return row;
  }

  private sessionLogStatus(log: ISessionLogFile): string {
    if (log.status === 'ready') {
      return log.size_bytes === null
        ? '已生成'
        : `已生成 · ${formatFileSize(log.size_bytes)}`;
    }
    if (log.status === 'error') {
      return log.kind === 'analysis' ? '分析未完成' : '生成失败';
    }
    if (log.status === 'generating') {
      return log.kind === 'analysis' ? '正在分析…' : '正在生成…';
    }
    if (!this.currentSessionId || this.deps.capture.isEnabled()) {
      return '等待监控结束';
    }
    return log.kind === 'analysis' && this.job ? '正在分析…' : '正在生成…';
  }

  private async openSessionLog(log: ISessionLogFile): Promise<void> {
    const sessionId = this.currentSessionId;
    if (!sessionId || log.status !== 'ready') return;
    try {
      await this.deps.openSessionLog(sessionId, log);
      this.sessionLogsFeedback = '';
    } catch {
      this.sessionLogsFeedback = '日志打开失败，请重试。';
      this.render();
    }
  }

  private async downloadSessionLog(log: ISessionLogFile): Promise<void> {
    const sessionId = this.currentSessionId;
    if (!sessionId || log.status !== 'ready') return;
    try {
      await this.deps.downloadSessionLog(sessionId, log);
      this.sessionLogsFeedback = '';
    } catch {
      this.sessionLogsFeedback = '日志下载失败，请重试。';
      this.render();
    }
  }

  private logFolderControl(): HTMLElement {
    const container = node('div', 'jp-BehaviorAudit-logFolderControl');
    const description = node('p', 'jp-BehaviorAudit-notice');
    description.textContent =
      '本地诊断文件仅用于故障排查；日常查看请使用上方“本次日志”。';
    const open = button('打开日志文件夹');
    open.disabled = this.logFolderOpenRequest !== null;
    if (this.logFolderOpenRequest !== null) {
      open.setAttribute('aria-busy', 'true');
    }
    open.addEventListener('click', () => this.openLogFolder());
    const feedback = node(
      'p',
      this.logFolderFeedback?.tone === 'error'
        ? 'jp-BehaviorAudit-fieldError'
        : 'jp-BehaviorAudit-notice'
    );
    feedback.dataset.logFolderStatus = '';
    feedback.setAttribute('role', 'status');
    feedback.setAttribute('aria-live', 'polite');
    feedback.setAttribute('aria-atomic', 'true');
    feedback.textContent = this.logFolderFeedback?.message ?? '';
    container.append(description, open, feedback);
    this.logFolderOpenButton = open;
    this.logFolderStatusNode = feedback;
    return container;
  }

  private openLogFolder(): void {
    if (this.logFolderOpenRequest !== null) return;
    const request = {};
    this.logFolderOpenRequest = request;
    this.logFolderFeedback = null;
    this.render();
    void this.deps.openLogFolder(this.deps.settings).then(
      () => this.finishLogFolderOpen(request, '已打开日志文件夹。', 'info'),
      () =>
        this.finishLogFolderOpen(
          request,
          '无法打开日志文件夹，请确认 JupyterLab 运行在本机。',
          'error'
        )
    );
  }

  private finishLogFolderOpen(
    request: object,
    message: string,
    tone: 'info' | 'error'
  ): void {
    if (this.isDisposed || this.logFolderOpenRequest !== request) return;
    this.logFolderOpenRequest = null;
    this.logFolderFeedback = { message, tone };
    if (this.logFolderOpenButton) {
      this.logFolderOpenButton.disabled = false;
      this.logFolderOpenButton.removeAttribute('aria-busy');
    }
    if (this.logFolderStatusNode) {
      this.logFolderStatusNode.className =
        tone === 'error'
          ? 'jp-BehaviorAudit-fieldError'
          : 'jp-BehaviorAudit-notice';
      this.logFolderStatusNode.textContent = message;
    }
  }

  private aiSection(): HTMLElement {
    if (this.aiSectionNode) {
      return this.aiSectionNode;
    }
    const details = node('details', 'jp-BehaviorAudit-aiConfig');
    details.dataset.stateKey = 'ai-config';
    const summary = node('summary');
    summary.textContent = 'AI 服务配置';
    const base = node('input', 'jp-BehaviorAudit-input') as HTMLInputElement;
    const model = node('input', 'jp-BehaviorAudit-input') as HTMLInputElement;
    const key = node('input', 'jp-BehaviorAudit-input') as HTMLInputElement;
    const baseLabel = node('label', 'jp-BehaviorAudit-label');
    const modelLabel = node('label', 'jp-BehaviorAudit-label');
    const keyLabel = node('label', 'jp-BehaviorAudit-label');
    base.id = 'behavior-analysis-ai-base-url';
    model.id = 'behavior-analysis-ai-model';
    key.id = 'behavior-analysis-ai-key';
    baseLabel.htmlFor = base.id;
    modelLabel.htmlFor = model.id;
    keyLabel.htmlFor = key.id;
    baseLabel.textContent = 'Base URL';
    modelLabel.textContent = '模型';
    keyLabel.textContent = 'API Key';
    base.placeholder = 'Base URL';
    model.placeholder = '模型';
    base.value = this.aiBaseUrl;
    model.value = this.aiModel;
    key.type = 'password';
    key.placeholder = 'API Key';
    const baseError = node('div', 'jp-BehaviorAudit-fieldError');
    baseError.id = `${base.id}-error`;
    baseError.setAttribute('aria-live', 'polite');
    base.setAttribute('aria-describedby', baseError.id);
    base.addEventListener('input', () => {
      base.removeAttribute('aria-invalid');
      baseError.textContent = '';
    });
    const state = node('div', 'jp-BehaviorAudit-notice');
    state.setAttribute('aria-live', 'polite');
    state.textContent = this.aiStatus;
    const save = button('保存 AI 配置');
    const clearKey = button('清除已保存 Key');
    clearKey.classList.add('jp-BehaviorAudit-button-danger');
    clearKey.hidden = !this.aiKeyConfigured;
    save.addEventListener('click', () => {
      if (this.aiSaveInFlight || this.aiClearInFlight) return;
      base.removeAttribute('aria-invalid');
      baseError.textContent = '';
      save.disabled = true;
      clearKey.disabled = true;
      save.setAttribute('aria-busy', 'true');
      state.textContent = '正在保存 AI 配置…';
      const operation = this.deps.requestAIConfig({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base_url: base.value,
          model: model.value,
          api_key: key.value
        })
      });
      this.aiSaveInFlight = operation;
      void operation.then(
        value => {
          if (this.isDisposed || this.aiSaveInFlight !== operation) return;
          this.aiSaveInFlight = null;
          key.value = '';
          this.aiBaseUrl = base.value;
          this.aiModel = model.value;
          this.aiKeyConfigured = Boolean(value.api_key_configured);
          key.placeholder = this.aiKeyConfigured ? 'API Key 已配置' : 'API Key';
          clearKey.hidden = !this.aiKeyConfigured;
          clearKey.disabled = false;
          state.textContent = 'AI 配置已保存。';
          save.disabled = false;
          save.removeAttribute('aria-busy');
        },
        error => {
          if (this.isDisposed || this.aiSaveInFlight !== operation) return;
          this.aiSaveInFlight = null;
          const details = aiConfigErrorDetails(error);
          if (
            error instanceof ApiError &&
            error.code === 'ai_config_validation_failed' &&
            details?.field === 'base_url'
          ) {
            base.setAttribute('aria-invalid', 'true');
            baseError.textContent = aiBaseUrlError(details.reason);
            state.textContent = 'AI 配置未保存，请检查标出的字段。';
          } else {
            state.textContent = 'AI 配置保存失败，请重试。';
          }
          save.disabled = false;
          clearKey.disabled = false;
          save.removeAttribute('aria-busy');
        }
      );
    });
    clearKey.addEventListener('click', () => {
      if (this.aiSaveInFlight || this.aiClearInFlight) return;
      save.disabled = true;
      clearKey.disabled = true;
      clearKey.setAttribute('aria-busy', 'true');
      state.textContent = '等待确认是否清除 API Key…';
      const operation = this.deps.confirmClearAIKey().then(async confirmed => {
        if (
          this.isDisposed ||
          this.aiClearInFlight !== operation ||
          !confirmed
        ) {
          return;
        }
        state.textContent = '正在清除已保存的 API Key…';
        const value = await this.deps.requestAIConfig({
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ clear_api_key: true })
        });
        if (this.isDisposed || this.aiClearInFlight !== operation) return;
        key.value = '';
        this.aiKeyConfigured = Boolean(value.api_key_configured);
        this.aiStatus = this.aiKeyConfigured
          ? 'AI 状态：已配置'
          : 'AI 状态：未配置';
      });
      this.aiClearInFlight = operation;
      void operation.then(
        () => {
          if (this.isDisposed || this.aiClearInFlight !== operation) return;
          this.aiClearInFlight = null;
          save.disabled = false;
          clearKey.disabled = false;
          clearKey.removeAttribute('aria-busy');
          this.syncAISection();
        },
        () => {
          if (this.isDisposed || this.aiClearInFlight !== operation) return;
          this.aiClearInFlight = null;
          save.disabled = false;
          clearKey.disabled = false;
          clearKey.removeAttribute('aria-busy');
          state.textContent = 'API Key 清除失败，请重试。';
        }
      );
    });
    details.append(
      summary,
      baseLabel,
      base,
      baseError,
      modelLabel,
      model,
      keyLabel,
      key,
      save,
      clearKey,
      state
    );
    this.aiSectionNode = details;
    return details;
  }

  private loadAIConfig(): Promise<void> {
    if (this.aiLoading) return this.aiLoading;
    if (this.aiLoaded) return Promise.resolve();
    const operation = this.deps.requestAIConfig().then(
      value => {
        if (this.isDisposed) return;
        this.aiLoaded = true;
        this.aiBaseUrl = value.base_url ?? '';
        this.aiModel = value.model ?? '';
        this.aiKeyConfigured = Boolean(value.api_key_configured);
        this.interactiveValues.delete('behavior-analysis-ai-base-url');
        this.interactiveValues.delete('behavior-analysis-ai-model');
        this.aiStatus = value.api_key_configured
          ? 'AI 状态：已配置'
          : 'AI 状态：未配置';
        this.syncAISection();
        this.render();
      },
      () => {
        if (this.isDisposed) return;
        this.aiLoaded = true;
        this.aiStatus = 'AI 配置读取失败。';
        this.syncAISection();
        this.render();
      }
    );
    this.aiLoading = operation;
    operation.finally(() => {
      if (this.aiLoading === operation) this.aiLoading = null;
    });
    return operation;
  }

  private syncAISection(): void {
    if (!this.aiSectionNode || this.aiSaveInFlight || this.aiClearInFlight)
      return;
    const base = this.aiSectionNode.querySelector<HTMLInputElement>(
      '#behavior-analysis-ai-base-url'
    );
    const model = this.aiSectionNode.querySelector<HTMLInputElement>(
      '#behavior-analysis-ai-model'
    );
    const key = this.aiSectionNode.querySelector<HTMLInputElement>(
      '#behavior-analysis-ai-key'
    );
    const state = this.aiSectionNode.querySelector<HTMLElement>(
      '.jp-BehaviorAudit-notice'
    );
    const clearKey = Array.from(
      this.aiSectionNode.querySelectorAll<HTMLButtonElement>('button')
    ).find(value => value.textContent === '清除已保存 Key');
    if (base) base.value = this.aiBaseUrl;
    if (model) model.value = this.aiModel;
    if (key)
      key.placeholder = this.aiKeyConfigured ? 'API Key 已配置' : 'API Key';
    if (clearKey) clearKey.hidden = !this.aiKeyConfigured;
    if (state) state.textContent = this.aiStatus;
  }

  private advancedSection(): HTMLElement {
    const details = node('details', 'jp-BehaviorAudit-advancedData');
    details.dataset.stateKey = 'advanced-data';
    const summary = node('summary');
    summary.textContent = '高级数据';
    const list = node('div');
    const refresh = button('刷新旧数据');
    refresh.addEventListener('click', () => {
      list.textContent = '正在读取旧数据…';
      void this.deps.requestLatestAnalysis().then(
        response => {
          list.textContent = '';
          for (const group of response.log_groups ?? []) {
            const heading = node('p');
            heading.textContent = group.category;
            list.appendChild(heading);
            for (const file of group.files) {
              const item = button(file.label);
              item.disabled = !file.contents_path;
              item.addEventListener('click', () => {
                if (file.contents_path)
                  void this.deps.openDataFile(file.contents_path);
              });
              list.appendChild(item);
            }
          }
          if (!list.childElementCount) list.textContent = '暂无旧数据。';
        },
        error => {
          list.textContent =
            error instanceof ApiError && error.status === 404
              ? '暂无旧数据。'
              : '旧数据读取失败，请重试。';
        }
      );
    });
    details.append(summary, this.logFolderControl(), refresh, list);
    const sessionId = this.currentSessionId;
    if (sessionId) details.appendChild(this.deleteControl(sessionId));
    return details;
  }

  private deleteControl(sessionId: string): HTMLElement {
    const container = node('div', 'jp-BehaviorAudit-deleteSession');
    container.dataset.sessionId = sessionId;
    const label = node('label', 'jp-BehaviorAudit-label');
    const id = 'delete-session-confirmation';
    label.htmlFor = id;
    label.textContent = '输入完整会话 ID 以删除本次会话';
    const input = node('input', 'jp-BehaviorAudit-input') as HTMLInputElement;
    input.id = id;
    const status = node('div', 'jp-BehaviorAudit-fieldError');
    status.setAttribute('aria-live', 'polite');
    status.textContent = this.deleteFeedback.get(sessionId) ?? '';
    const remove = button('删除本次会话');
    remove.classList.add('jp-BehaviorAudit-button-danger');
    remove.disabled = this.deleteInFlight !== null;
    if (this.deleteInFlight) remove.setAttribute('aria-busy', 'true');
    remove.addEventListener('click', () => {
      if (this.deleteInFlight) return;
      if (input.value !== sessionId) {
        status.textContent = '请输入完全一致的会话 ID。';
        return;
      }
      this.deleteFeedback.delete(sessionId);
      status.textContent = '';
      remove.disabled = true;
      remove.setAttribute('aria-busy', 'true');
      const generation = this.generation;
      const promise = this.deps.deleteSession(
        this.deps.settings,
        sessionId,
        'local_teacher',
        'teacher_requested_deletion'
      );
      const operation = { promise, generation, sessionId };
      this.deleteInFlight = operation;
      void promise
        .then(
          response => {
            if (this.deleteInFlight !== operation || this.isDisposed) return;
            if (response.deleted_session_id !== sessionId) {
              if (
                this.isCurrentGeneration(generation) &&
                this.currentSessionId === sessionId
              ) {
                this.deleteFeedback.set(
                  sessionId,
                  '删除确认不匹配，未清除当前状态。'
                );
              }
              return;
            }
            this.deletedSessionIds.add(sessionId);
            this.clearSessionInteractiveState(sessionId);
            if (
              !this.isCurrentGeneration(generation) ||
              this.currentSessionId !== sessionId
            )
              return;
            this.generation += 1;
            this.stopPolling();
            this.currentSessionId = null;
            this.upload = { ...EMPTY_UPLOAD };
            this.analysis = null;
            this.analysisProfile = null;
            this.job = null;
            this.pendingSession = null;
            this.stopFailed = false;
            this.sessionLogs = emptySessionLogs();
            this.sessionLogsSessionId = null;
            this.sessionLogsFeedback = '';
            try {
              this.deps.storage.removeItem('myextension:active-session');
            } catch {
              // Exact server receipt is authoritative.
            }
            this.notice = '本次会话已删除。';
            this.noticeTone = 'info';
            this.render();
          },
          () => {
            if (
              this.deleteInFlight !== operation ||
              !this.isCurrentGeneration(generation) ||
              this.currentSessionId !== sessionId
            )
              return;
            this.deleteFeedback.set(sessionId, '删除失败，当前状态未改变。');
          }
        )
        .finally(() => {
          if (this.deleteInFlight !== operation) return;
          this.deleteInFlight = null;
          if (!this.isDisposed) this.render();
        });
    });
    container.append(label, input, remove, status);
    return container;
  }

  private async retryJob(): Promise<void> {
    if (!this.job || this.retryInFlight || this.isDisposed) return;
    const expectedJob = this.job;
    const sessionId = expectedJob.session_id;
    const generation = this.generation;
    const promise = this.deps.retryAnalysisJob(
      this.deps.settings,
      expectedJob.job_id,
      'teacher_requested_retry'
    );
    const operation = {
      promise,
      generation,
      sessionId,
      jobId: expectedJob.job_id
    };
    this.retryInFlight = operation;
    this.render();
    try {
      const retried = await promise;
      if (
        this.retryInFlight !== operation ||
        !this.isCurrentGeneration(generation) ||
        this.deletedSessionIds.has(sessionId) ||
        this.currentSessionId !== sessionId ||
        this.job?.job_id !== expectedJob.job_id ||
        retried.session_id !== sessionId
      )
        return;
      this.job = retried;
      this.notice = '已请求重试分析。';
      this.noticeTone = 'info';
      this.startPolling();
    } catch {
      if (
        this.retryInFlight !== operation ||
        !this.isCurrentGeneration(generation) ||
        this.currentSessionId !== sessionId ||
        this.job?.job_id !== expectedJob.job_id
      )
        return;
      this.notice = '重试分析失败，请刷新状态。';
      this.noticeTone = 'error';
    } finally {
      if (this.retryInFlight === operation) {
        this.retryInFlight = null;
        if (this.isCurrentGeneration(generation)) this.render();
      }
    }
  }
}
