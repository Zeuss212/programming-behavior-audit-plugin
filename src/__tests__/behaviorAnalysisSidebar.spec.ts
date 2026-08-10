import { readFileSync } from 'node:fs';

import { ServerConnection } from '@jupyterlab/services';

import { IBehaviorCaptureController } from '../behaviorCapture';
import { IDimensionProfileVersion } from '../models/dimensionProfile';
import { IAnalysisResult } from '../models/analysisResult';
import { ApiError } from '../models/apiError';
import {
  IAnalysisJob,
  ISessionFinalizeResponse,
  ISessionState,
  IUploadSnapshot
} from '../models/session';
import { ILogFolderOpenResponse } from '../models/logFolder';
import {
  ISessionLogFile,
  ISessionLogListResponse
} from '../services/sessionLogApi';
import {
  BehaviorAnalysisSidebar,
  IBehaviorAnalysisSidebarDependencies,
  sidebarDependencies
} from '../ui/behaviorAnalysisSidebar';

jest.mock('@jupyterlab/ui-components', () => ({
  inspectorIcon: { name: 'ui-components:inspector' }
}));

const settings = {} as ServerConnection.ISettings;
const profile: IDimensionProfileVersion = {
  schema_version: 1,
  profile_id: '123e4567-e89b-42d3-a456-426614174000',
  problem_id: 'synthetic-problem',
  title: '合成调试题',
  version: 3,
  content_hash: 'a'.repeat(64),
  deployment_status: 'pilot',
  preview_status: 'pending_real_samples',
  dimensions: []
};
const observationProfile: IDimensionProfileVersion = {
  ...profile,
  profile_id: '123e4567-e89b-42d3-a456-426614174099',
  title: '有效观察进度题',
  dimensions: [
    {
      code: 'OBSERVE_SHORT',
      name: '短观察',
      question: '是否形成有效观察？',
      evidence_criteria: [],
      levels: [
        { code: 'possible', name: '可能', definition: '可能出现' },
        { code: 'clear', name: '明确', definition: '明确出现' }
      ],
      analysis_config: {
        mode: 'llm_evidence',
        minimum_observation: {
          valid_observation_duration_ms: 10_000
        }
      }
    },
    {
      code: 'OBSERVE_LONG',
      name: '长观察',
      question: '是否有充分观察？',
      evidence_criteria: [],
      levels: [
        { code: 'possible', name: '可能', definition: '可能出现' },
        { code: 'clear', name: '明确', definition: '明确出现' }
      ],
      analysis_config: {
        mode: 'llm_evidence',
        minimum_observation: {
          valid_observation_duration_ms: 30_000
        }
      }
    }
  ]
};
const assessmentProfile: IDimensionProfileVersion = {
  schema_version: 2,
  profile_id: '223e4567-e89b-42d3-a456-426614174001',
  problem_id: 'average-knowledge',
  title: '平均值知识点考核',
  version: 1,
  content_hash: 'f'.repeat(64),
  deployment_status: 'pilot',
  preview_status: 'pending_real_samples',
  problem_context: {
    statement: '实现 calculate_average(numbers)。',
    language: 'python',
    submission_contract: {
      kind: 'function',
      entrypoint: 'calculate_average'
    }
  },
  knowledge_points: [
    {
      id: 'KP_A1B2C3D4',
      name: '平均值计算',
      description: '使用总和除以元素数量。',
      source: 'teacher',
      order: 0
    }
  ],
  assessment_tests: [],
  confirmations: {
    knowledge_points_hash: 'b'.repeat(64),
    tests_hash: null
  },
  dimensions: []
};
const snapshot: IUploadSnapshot = {
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

const job: IAnalysisJob = {
  schema_version: 1,
  request_id: 'synthetic-job-request',
  job_id: '323e4567-e89b-42d3-a456-426614174000',
  session_id: '223e4567-e89b-42d3-a456-426614174000',
  status: 'queued',
  active_attempt_id: null,
  attempt_ids: [],
  analysis_id: null,
  error_code: null
};

const analysis: IAnalysisResult = {
  schema_version: 1,
  request_id: 'synthetic-analysis-request',
  analysis_id: '423e4567-e89b-42d3-a456-426614174000',
  job_id: job.job_id,
  attempt_id: '523e4567-e89b-42d3-a456-426614174000',
  session_id: job.session_id,
  profile_id: profile.profile_id,
  profile_version: profile.version,
  profile_content_hash: profile.content_hash,
  status: 'ready',
  error_code: null,
  dimension_results: [],
  provenance: {
    analysis_pipeline_version: 'pilot-v1',
    feature_extractor_version: 'pilot-v1',
    signal_dictionary_version: 'pilot-v1',
    signal_dictionary_hash: 'b'.repeat(64),
    model_name: 'synthetic',
    model_version: null,
    model_parameters: { temperature: 0 },
    prompt_version: 'pilot-v1',
    prompt_content_hash: 'c'.repeat(64),
    provider_request_id: null,
    raw_response_hash: 'd'.repeat(64),
    input_snapshot_hash: 'e'.repeat(64)
  }
};

const LOG_FOLDER_RESPONSE: ILogFolderOpenResponse = {
  schema_version: 1,
  request_id: '10000000-0000-4000-8000-000000000020',
  opened: true,
  platform: 'macos'
};

const sessionB = '623e4567-e89b-42d3-a456-426614174000';
const jobBId = '723e4567-e89b-42d3-a456-426614174000';

function reviewableAnalysis(
  sessionId: string,
  jobId: string,
  revision = 1
): IAnalysisResult {
  return {
    ...analysis,
    session_id: sessionId,
    job_id: jobId,
    dimension_results: [
      {
        dimension_code: 'SYNTHETIC_REVIEW',
        decision: {
          status: 'needs_review',
          final_evidence_status: null,
          final_level_code: null,
          display_label: 'synthetic review',
          source: 'coverage'
        },
        data_quality: {
          missing_required_signals: [],
          observation_opportunities: 0,
          reason_code: null,
          reason: null
        },
        ai_result: null,
        review: { revision, status: 'unreviewed' }
      }
    ]
  };
}

function findButton(
  sidebar: BehaviorAnalysisSidebar,
  label: string
): HTMLButtonElement {
  const found = Array.from(
    sidebar.node.querySelectorAll<HTMLButtonElement>('button')
  ).find(value => value.textContent === label);
  if (!found) throw new Error(`Missing button: ${label}`);
  return found;
}

function findLogFolderStatus(
  sidebar: BehaviorAnalysisSidebar
): HTMLParagraphElement {
  const found = sidebar.node.querySelector(
    'details[data-state-key="advanced-data"] [data-log-folder-status]'
  );
  if (!(found instanceof HTMLParagraphElement)) {
    throw new Error('Missing training-log status region');
  }
  return found;
}

function selectAndConsent(sidebar: BehaviorAnalysisSidebar): void {
  const select = sidebar.node.querySelector<HTMLSelectElement>('select')!;
  select.value = `${profile.profile_id}:${profile.version}`;
  select.dispatchEvent(new Event('change', { bubbles: true }));
  const consent = sidebar.node.querySelector<HTMLInputElement>(
    'input[type="checkbox"]'
  )!;
  consent.checked = true;
  consent.dispatchEvent(new Event('change', { bubbles: true }));
}

async function stopAndLoadReadyResult(
  sidebar: BehaviorAnalysisSidebar
): Promise<void> {
  findButton(sidebar, '停止监控').click();
  await flush();
  findButton(sidebar, '刷新状态').click();
  await flush();
}

async function flush(): Promise<void> {
  for (let index = 0; index < 10; index += 1) {
    await Promise.resolve();
  }
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (error: Error) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function createCapture(): jest.Mocked<IBehaviorCaptureController> {
  return {
    logger: {} as IBehaviorCaptureController['logger'],
    isEnabled: jest.fn(() => false),
    snapshot: jest.fn(() => snapshot),
    subscribe: jest.fn(
      (_listener: (value: IUploadSnapshot) => void) => () => undefined
    ),
    start: jest.fn(async _profile => undefined),
    resume: jest.fn(async _session => undefined),
    stop: jest.fn(
      async (): Promise<ISessionFinalizeResponse> => ({
        schema_version: 1,
        request_id: 'synthetic-request',
        session_id: '223e4567-e89b-42d3-a456-426614174000',
        status: 'finalized',
        last_contiguous_sequence: 1,
        analysis_job_id: '323e4567-e89b-42d3-a456-426614174000'
      })
    )
  };
}

function dependencies(
  capture: IBehaviorCaptureController,
  profiles: IDimensionProfileVersion[]
): IBehaviorAnalysisSidebarDependencies {
  return {
    settings,
    capture,
    listProfiles: jest.fn(async () => profiles),
    getProfileVersion: jest.fn(async () => profile),
    getAnalysisJob: jest.fn(),
    getSessionAnalysis: jest.fn(),
    reviewDimension: jest.fn(),
    retryAnalysisJob: jest.fn(),
    getStoredActiveSession: jest.fn(async () => null),
    abandonSession: jest.fn(),
    deleteSession: jest.fn(
      async (_settings, sessionId) =>
        ({
          schema_version: 1,
          request_id: 'synthetic-delete-request',
          deleted_session_id: sessionId
        }) as const
    ),
    openLogFolder: jest.fn(async () => LOG_FOLDER_RESPONSE),
    listSessionLogs: jest.fn(async sessionId => sessionLogs(sessionId)),
    openSessionLog: jest.fn(async () => undefined),
    downloadSessionLog: jest.fn(async () => undefined),
    openProfileEditor: jest.fn(),
    openDataFile: jest.fn(),
    confirmClearAIKey: jest.fn(async () => true),
    requestAIConfig: jest.fn(async () => ({ status: 'success' as const })),
    requestLatestAnalysis: jest.fn(async () => ({ log_groups: [] })),
    setTimer: setTimeout,
    clearTimer: clearTimeout,
    now: () => 0,
    isDocumentActive: () => true,
    storage: localStorage
  };
}

function sessionLogs(
  sessionId: string,
  analysisStatus: ISessionLogFile['status'] = 'generating'
): ISessionLogListResponse {
  const logs: ISessionLogListResponse['logs'] = [
    {
      kind: 'operation',
      filename: 'operation_log.json',
      label: '操作日志',
      description: '用户输入、删除、粘贴、运行成功/失败及输出。',
      status: 'ready',
      media_type: 'application/json; charset=utf-8',
      size_bytes: 1024,
      generated_at: '2026-08-04T04:00:00+00:00',
      error_code: null
    },
    {
      kind: 'process',
      filename: 'process_log.md',
      label: '过程日志',
      description: '按时间顺序整理输入、修改、动作间停顿和运行结果。',
      status: 'ready',
      media_type: 'text/markdown; charset=utf-8',
      size_bytes: 2048,
      generated_at: '2026-08-04T04:00:00+00:00',
      error_code: null
    },
    {
      kind: 'analysis',
      filename: 'analysis_log.json',
      label: 'AI 分析日志',
      description: '维度结论、数据质量、行为证据与分析来源。',
      status: analysisStatus,
      media_type: 'application/json; charset=utf-8',
      size_bytes: analysisStatus === 'ready' ? 4096 : null,
      generated_at:
        analysisStatus === 'ready' ? '2026-08-04T04:00:10+00:00' : null,
      error_code: analysisStatus === 'error' ? 'ai_analysis_failed' : null
    }
  ];
  return {
    schema_version: 1,
    request_id: 'session-log-request',
    session_id: sessionId,
    logs
  };
}

describe('BehaviorAnalysisSidebar', () => {
  it('shows the three session logs in fixed order and keeps the folder action under advanced data', async () => {
    const sidebar = new BehaviorAnalysisSidebar(
      dependencies(createCapture(), [profile])
    );
    await flush();

    const heading = Array.from(sidebar.node.querySelectorAll('h2')).find(
      value => value.textContent === '本次日志'
    );
    const section = heading?.closest('section');
    expect(heading).toBeDefined();
    expect(section?.querySelector('p')?.textContent).toBe(
      '停止监控后会先生成操作日志和过程日志，AI 分析日志完成后再显示。'
    );
    expect(
      Array.from(
        section?.querySelectorAll<HTMLElement>('[data-session-log-kind]') ?? []
      ).map(value => value.dataset.sessionLogKind)
    ).toEqual(['operation', 'process', 'analysis']);
    expect(
      Array.from(
        section?.querySelectorAll<HTMLElement>('[data-session-log-filename]') ??
          []
      ).map(value => value.textContent)
    ).toEqual(['operation_log.json', 'process_log.md', 'analysis_log.json']);
    expect(
      Array.from(
        section?.querySelectorAll('[data-session-log-status]') ?? []
      ).map(value => value.textContent)
    ).toEqual(['等待监控结束', '等待监控结束', '等待监控结束']);
    expect(section?.textContent).not.toContain('打开日志文件夹');
    expect(
      sidebar.node.querySelector('details[data-state-key="advanced-data"]')
        ?.textContent
    ).toContain('打开日志文件夹');
    sidebar.dispose();
  });

  it('opens ready files from the filename or view action and downloads them', async () => {
    const capture = createCapture();
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id
    });
    const deps = dependencies(capture, [profile]);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();

    const rows = Array.from(
      sidebar.node.querySelectorAll<HTMLElement>('[data-session-log-kind]')
    );
    expect(rows.map(value => value.dataset.sessionLogKind)).toEqual([
      'operation',
      'process',
      'analysis'
    ]);
    expect(
      rows[0].querySelector('[data-session-log-status]')?.textContent
    ).toBe('已生成 · 1.0 KB');
    expect(
      rows[2].querySelector('[data-session-log-status]')?.textContent
    ).toBe('正在分析…');

    rows[0]
      .querySelector<HTMLButtonElement>('[data-session-log-filename]')
      ?.click();
    rows[1]
      .querySelector<HTMLButtonElement>('[data-session-log-action="view"]')
      ?.click();
    rows[0]
      .querySelector<HTMLButtonElement>('[data-session-log-action="download"]')
      ?.click();
    await flush();

    expect(deps.openSessionLog).toHaveBeenNthCalledWith(
      1,
      job.session_id,
      expect.objectContaining({ kind: 'operation' })
    );
    expect(deps.openSessionLog).toHaveBeenNthCalledWith(
      2,
      job.session_id,
      expect.objectContaining({ kind: 'process' })
    );
    expect(deps.downloadSessionLog).toHaveBeenCalledWith(
      job.session_id,
      expect.objectContaining({ kind: 'operation' })
    );
    expect(
      rows[2].querySelector('[data-session-log-action="view"]')
    ).toBeNull();
    expect(
      rows[2].querySelector('[data-session-log-action="download"]')
    ).toBeNull();
    sidebar.dispose();
  });

  it('changes the AI log from analysing to ready or incomplete without showing a false success', async () => {
    const capture = createCapture();
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id
    });
    const deps = dependencies(capture, [profile]);
    const list = deps.listSessionLogs as jest.MockedFunction<
      IBehaviorAnalysisSidebarDependencies['listSessionLogs']
    >;
    list
      .mockResolvedValueOnce(sessionLogs(job.session_id, 'generating'))
      .mockResolvedValueOnce(sessionLogs(job.session_id, 'ready'))
      .mockResolvedValueOnce(sessionLogs(job.session_id, 'error'));
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();

    expect(
      sidebar.node.querySelector(
        '[data-session-log-kind="analysis"] [data-session-log-status]'
      )?.textContent
    ).toBe('正在分析…');

    await (
      sidebar as unknown as { refreshSessionLogs(): Promise<void> }
    ).refreshSessionLogs();
    expect(
      sidebar.node.querySelector(
        '[data-session-log-kind="analysis"] [data-session-log-status]'
      )?.textContent
    ).toBe('已生成 · 4.0 KB');
    expect(
      sidebar.node.querySelector(
        '[data-session-log-kind="analysis"] [data-session-log-action="view"]'
      )
    ).not.toBeNull();

    await (
      sidebar as unknown as { refreshSessionLogs(): Promise<void> }
    ).refreshSessionLogs();
    expect(
      sidebar.node.querySelector(
        '[data-session-log-kind="analysis"] [data-session-log-status]'
      )?.textContent
    ).toBe('分析未完成');
    expect(
      sidebar.node.querySelector(
        '[data-session-log-kind="analysis"] [data-session-log-action="view"]'
      )
    ).toBeNull();
    sidebar.dispose();
  });

  it('keeps bounded polling when the AI job is ready before its log artifact', async () => {
    const capture = createCapture();
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id
    });
    const deps = dependencies(capture, [profile]);
    deps.getAnalysisJob = jest.fn(async () => ({
      ...job,
      status: 'ready' as const
    }));
    deps.getSessionAnalysis = jest.fn(async () => analysis);
    deps.getProfileVersion = jest.fn(async () => profile);
    deps.listSessionLogs = jest.fn(async sessionId =>
      sessionLogs(sessionId, 'generating')
    );
    deps.now = () => 1000;
    deps.setTimer = jest.fn(
      () => 17 as unknown as ReturnType<typeof setTimeout>
    );
    deps.clearTimer = jest.fn();
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    const internal = sidebar as unknown as {
      job: IAnalysisJob;
      pollStartedAt: number | null;
      pollTimer: ReturnType<typeof setTimeout> | null;
      pollOnce(): Promise<void>;
    };
    internal.job = { ...job };
    internal.pollStartedAt = 0;
    internal.pollTimer = null;

    await internal.pollOnce();

    expect(deps.setTimer).toHaveBeenCalled();
    expect(internal.pollStartedAt).toBe(0);
    sidebar.dispose();
  });

  it('disables one in-flight open request and ignores a second click', async () => {
    const pending = deferred<ILogFolderOpenResponse>();
    const deps = dependencies(createCapture(), [profile]);
    deps.openLogFolder = jest.fn(() => pending.promise);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    document.body.appendChild(sidebar.node);
    await flush();

    const open = findButton(sidebar, '打开日志文件夹');
    open.click();
    open.click();

    expect(deps.openLogFolder).toHaveBeenCalledTimes(1);
    expect(deps.openLogFolder).toHaveBeenCalledWith(settings);
    expect(findButton(sidebar, '打开日志文件夹').disabled).toBe(true);
    expect(
      findButton(sidebar, '打开日志文件夹').getAttribute('aria-busy')
    ).toBe('true');
    const status = findLogFolderStatus(sidebar);
    expect(status.isConnected).toBe(true);
    expect(status.textContent).toBe('');
    expect(status.getAttribute('aria-live')).toBe('polite');
    expect(status.getAttribute('aria-atomic')).toBe('true');
    sidebar.dispose();
    sidebar.node.remove();
  });

  it('restores the button and announces success after opening the folder', async () => {
    const pending = deferred<ILogFolderOpenResponse>();
    const deps = dependencies(createCapture(), [profile]);
    deps.openLogFolder = jest.fn(() => pending.promise);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    document.body.appendChild(sidebar.node);
    await flush();

    findButton(sidebar, '打开日志文件夹').click();
    const pendingStatus = findLogFolderStatus(sidebar);
    expect(pendingStatus.isConnected).toBe(true);
    expect(pendingStatus.textContent).toBe('');
    pending.resolve(LOG_FOLDER_RESPONSE);
    await flush();

    const open = findButton(sidebar, '打开日志文件夹');
    const feedback = findLogFolderStatus(sidebar);
    expect(open.disabled).toBe(false);
    expect(open.hasAttribute('aria-busy')).toBe(false);
    expect(feedback).toBe(pendingStatus);
    expect(feedback.isConnected).toBe(true);
    expect(feedback.textContent).toBe('已打开日志文件夹。');
    sidebar.dispose();
    sidebar.node.remove();
  });

  it('restores the button and announces a fixed safe failure message', async () => {
    const pending = deferred<ILogFolderOpenResponse>();
    const deps = dependencies(createCapture(), [profile]);
    deps.openLogFolder = jest.fn(() => pending.promise);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    document.body.appendChild(sidebar.node);
    await flush();

    findButton(sidebar, '打开日志文件夹').click();
    const pendingStatus = findLogFolderStatus(sidebar);
    expect(pendingStatus.isConnected).toBe(true);
    expect(pendingStatus.textContent).toBe('');
    pending.reject(new Error('/Users/teacher/private/training-record.json'));
    await flush();

    expect(findButton(sidebar, '打开日志文件夹').disabled).toBe(false);
    const feedback = findLogFolderStatus(sidebar);
    expect(feedback).toBe(pendingStatus);
    expect(feedback.isConnected).toBe(true);
    expect(feedback.textContent).toBe(
      '无法打开日志文件夹，请确认 JupyterLab 运行在本机。'
    );
    expect(sidebar.node.textContent).not.toContain('/Users/teacher');
    expect(sidebar.node.textContent).not.toContain('training-record.json');
    sidebar.dispose();
    sidebar.node.remove();
  });

  it('ignores late open-folder resolve and reject outcomes after dispose', async () => {
    const resolved = deferred<ILogFolderOpenResponse>();
    const rejected = deferred<ILogFolderOpenResponse>();
    const firstDeps = dependencies(createCapture(), [profile]);
    const secondDeps = dependencies(createCapture(), [profile]);
    firstDeps.openLogFolder = jest.fn(() => resolved.promise);
    secondDeps.openLogFolder = jest.fn(() => rejected.promise);
    const first = new BehaviorAnalysisSidebar(firstDeps);
    const second = new BehaviorAnalysisSidebar(secondDeps);
    await flush();
    findButton(first, '打开日志文件夹').click();
    findButton(second, '打开日志文件夹').click();
    first.dispose();
    second.dispose();
    const firstContent = first.node.textContent;
    const secondContent = second.node.textContent;

    resolved.resolve(LOG_FOLDER_RESPONSE);
    rejected.reject(new Error('/Users/teacher/private/training-record.json'));
    await flush();

    expect(first.node.textContent).toBe(firstContent);
    expect(second.node.textContent).toBe(secondContent);
  });
  it('shows the maximum published observation threshold and foreground provisional progress', async () => {
    const capture = createCapture();
    capture.isEnabled.mockReturnValue(true);
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id,
      uploadState: 'collecting',
      validObservationDurationMs: 5_000,
      pageAwayDurationMs: 3_000,
      observationAnchorAt: '2026-07-30T08:00:00.000Z'
    });
    const deps = dependencies(capture, [observationProfile]);
    deps.now = () => Date.parse('2026-07-30T08:00:10.000Z');
    deps.setTimer = jest.fn(() => 1) as unknown as typeof setTimeout;
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    const select = sidebar.node.querySelector<HTMLSelectElement>(
      '#behavior-analysis-profile'
    )!;
    select.value = `${observationProfile.profile_id}:${observationProfile.version}`;
    select.dispatchEvent(new Event('change', { bubbles: true }));

    expect(sidebar.node.textContent).toContain('有效观察时长（证据覆盖）');
    expect(sidebar.node.textContent).toContain('15.0 / 30.0 秒');
    expect(sidebar.node.textContent).toContain(
      '页面离开：3.0 秒（不计入有效观察）'
    );
    expect(sidebar.node.textContent).toContain(
      '统计监控期间的代码输入、删除、粘贴及页面活动时的动作间停顿。页面离开不计入；运行事件会写入日志，但运行耗时不计入该时长。'
    );
    expect(sidebar.node.textContent).toContain(
      '达到门槛只表示行为证据覆盖足够，与日志生成或 AI 分析等待无关。'
    );
    const progress =
      sidebar.node.querySelector<HTMLProgressElement>('progress');
    expect(progress?.max).toBe(30_000);
    expect(progress?.value).toBe(15_000);
    expect(progress?.getAttribute('aria-label')).toBe(
      '有效观察时长（证据覆盖）进度'
    );
    sidebar.dispose();
  });

  it('does not advance provisional progress while the document is inactive', async () => {
    const capture = createCapture();
    capture.isEnabled.mockReturnValue(true);
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id,
      uploadState: 'collecting',
      validObservationDurationMs: 5_000,
      observationAnchorAt: '2026-07-30T08:00:00.000Z'
    });
    const deps = dependencies(capture, [observationProfile]);
    deps.now = () => Date.parse('2026-07-30T08:00:10.000Z');
    deps.isDocumentActive = () => false;
    deps.setTimer = jest.fn(() => 1) as unknown as typeof setTimeout;
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    const select = sidebar.node.querySelector<HTMLSelectElement>(
      '#behavior-analysis-profile'
    )!;
    select.value = `${observationProfile.profile_id}:${observationProfile.version}`;
    select.dispatchEvent(new Event('change', { bubbles: true }));

    expect(sidebar.node.textContent).toContain('5.0 / 30.0 秒');
    expect(sidebar.node.textContent).not.toContain('15.0 / 30.0 秒');
    sidebar.dispose();
  });

  it('translates every analysis job state instead of exposing internal enums', async () => {
    const sidebar = new BehaviorAnalysisSidebar(
      dependencies(createCapture(), [profile])
    );
    await flush();
    const states: Array<[IAnalysisJob['status'], string]> = [
      ['queued', '已排队'],
      ['running', '分析中'],
      ['ready', '分析完成'],
      ['partial', '分析完成（部分结果）'],
      ['error', '分析失败']
    ];

    for (const [status, label] of states) {
      Object.assign(sidebar as unknown as Record<string, unknown>, {
        job: { ...job, status }
      });
      (
        sidebar as unknown as {
          render(): void;
        }
      ).render();
      expect(sidebar.node.textContent).toContain(`分析任务：${label}`);
    }
    sidebar.dispose();
  });

  it('does not label a completely failed AI analysis as partial results', async () => {
    const sidebar = new BehaviorAnalysisSidebar(
      dependencies(createCapture(), [profile])
    );
    await flush();
    Object.assign(sidebar as unknown as Record<string, unknown>, {
      currentSessionId: job.session_id,
      job: {
        ...job,
        status: 'partial',
        error_code: 'ai_analysis_failed'
      },
      analysis: {
        ...analysis,
        status: 'partial',
        error_code: 'ai_analysis_failed'
      },
      analysisProfile: profile
    });

    (sidebar as unknown as { render(): void }).render();

    expect(sidebar.node.textContent).toContain('分析任务：AI 分析未完成');
    expect(sidebar.node.textContent).not.toContain('部分结果');
    sidebar.dispose();
  });

  it('warns before stopping below the observation threshold and can continue', async () => {
    const capture = createCapture();
    capture.isEnabled.mockReturnValue(true);
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id,
      uploadState: 'collecting',
      validObservationDurationMs: 17_800
    });
    const deps = dependencies(capture, [observationProfile]);
    deps.setTimer = jest.fn(() => 1) as unknown as typeof setTimeout;
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    const select = sidebar.node.querySelector<HTMLSelectElement>(
      '#behavior-analysis-profile'
    )!;
    select.value = `${observationProfile.profile_id}:${observationProfile.version}`;
    select.dispatchEvent(new Event('change', { bubbles: true }));

    findButton(sidebar, '停止监控').click();

    expect(capture.stop).not.toHaveBeenCalled();
    expect(sidebar.node.querySelector('[role="alert"]')?.textContent).toContain(
      '当前有效观察 17.8 / 30.0 秒。现在停止将得到“数据不足”'
    );
    findButton(sidebar, '继续监控').click();
    expect(capture.stop).not.toHaveBeenCalled();
    expect(sidebar.node.querySelector('[role="alert"]')).toBeNull();
    sidebar.dispose();
  });

  it('clears the stop warning when foreground observation reaches the threshold', async () => {
    let now = Date.parse('2026-07-30T08:00:01.000Z');
    const timers: Array<() => void> = [];
    const capture = createCapture();
    capture.isEnabled.mockReturnValue(true);
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id,
      uploadState: 'collecting',
      validObservationDurationMs: 17_800,
      observationAnchorAt: '2026-07-30T08:00:00.000Z'
    });
    const deps = dependencies(capture, [observationProfile]);
    deps.now = () => now;
    deps.setTimer = ((callback: () => void) => {
      timers.push(callback);
      return timers.length;
    }) as unknown as typeof setTimeout;
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    const select = sidebar.node.querySelector<HTMLSelectElement>(
      '#behavior-analysis-profile'
    )!;
    select.value = `${observationProfile.profile_id}:${observationProfile.version}`;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    findButton(sidebar, '停止监控').click();
    expect(sidebar.node.querySelector('[role="alert"]')).not.toBeNull();

    now = Date.parse('2026-07-30T08:00:13.000Z');
    timers.shift()?.();

    expect(sidebar.node.querySelector('[role="alert"]')).toBeNull();
    expect(sidebar.node.textContent).toContain('已达到最低要求');
    sidebar.dispose();
  });

  it('stops only after explicit confirmation when observation is insufficient', async () => {
    const capture = createCapture();
    capture.isEnabled.mockReturnValue(true);
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id,
      uploadState: 'collecting',
      validObservationDurationMs: 17_800
    });
    const deps = dependencies(capture, [observationProfile]);
    deps.setTimer = jest.fn(() => 1) as unknown as typeof setTimeout;
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    const select = sidebar.node.querySelector<HTMLSelectElement>(
      '#behavior-analysis-profile'
    )!;
    select.value = `${observationProfile.profile_id}:${observationProfile.version}`;
    select.dispatchEvent(new Event('change', { bubbles: true }));

    findButton(sidebar, '停止监控').click();
    findButton(sidebar, '仍要停止').click();
    await flush();

    expect(capture.stop).toHaveBeenCalledTimes(1);
    sidebar.dispose();
  });

  it('keeps the activity-bar icon and upright label metadata', () => {
    const sidebar = new BehaviorAnalysisSidebar(
      dependencies(createCapture(), [])
    );

    expect(sidebar.title.icon).toBeDefined();
    expect(sidebar.title.label).toBe('行为分析');
    expect(sidebar.title.caption).toBe('编程行为分析');
    expect(sidebar.title.className.split(/\s+/)).toContain(
      'jp-BehaviorAudit-sidebarTab'
    );
    sidebar.dispose();
  });

  it('keeps this left Chinese tab upright without changing other tabs', () => {
    const style = document.createElement('style');
    style.textContent = `
      .jp-SideBar.lm-TabBar[data-orientation='vertical'] .lm-TabBar-tabLabel {
        writing-mode: vertical-rl;
      }
      .jp-SideBar.lm-TabBar.jp-mod-left .lm-TabBar-tabLabel {
        transform: rotate(180deg);
      }
      ${readFileSync('style/base.css', 'utf8')}
    `;
    const sideBar = document.createElement('div');
    sideBar.className = 'jp-SideBar lm-TabBar jp-mod-left';
    sideBar.dataset.orientation = 'vertical';
    const pluginTab = document.createElement('div');
    pluginTab.className = 'lm-TabBar-tab jp-BehaviorAudit-sidebarTab';
    const pluginLabel = document.createElement('div');
    pluginLabel.className = 'lm-TabBar-tabLabel';
    const otherTab = document.createElement('div');
    otherTab.className = 'lm-TabBar-tab';
    const otherLabel = document.createElement('div');
    otherLabel.className = 'lm-TabBar-tabLabel';
    pluginTab.appendChild(pluginLabel);
    otherTab.appendChild(otherLabel);
    sideBar.append(pluginTab, otherTab);
    document.head.appendChild(style);
    document.body.appendChild(sideBar);

    expect(
      getComputedStyle(pluginLabel).getPropertyValue('text-orientation')
    ).toBe('upright');
    expect(getComputedStyle(pluginLabel).transform).toBe('none');
    expect(getComputedStyle(otherLabel).transform).toBe('rotate(180deg)');

    sideBar.remove();
    style.remove();
  });

  it('wraps browser timers so dependency method calls do not rebind them', () => {
    const receivers: unknown[] = [];
    const timer = jest
      .spyOn(globalThis, 'setTimeout')
      .mockImplementation(function (this: unknown) {
        receivers.push(this);
        return 1 as unknown as ReturnType<typeof setTimeout>;
      });
    const deps = sidebarDependencies(settings, createCapture(), {
      openProfileEditor: jest.fn(),
      openDataFile: jest.fn(),
      confirmClearAIKey: jest.fn(async () => true),
      getStoredActiveSession: jest.fn(async () => null),
      openLogFolder: jest.fn(async () => LOG_FOLDER_RESPONSE),
      openSessionLog: jest.fn(async () => undefined),
      downloadSessionLog: jest.fn(async () => undefined)
    });

    deps.setTimer(() => undefined, 0);

    expect(receivers[0]).not.toBe(deps);
    timer.mockRestore();
  });

  it('keeps monitoring disabled until a published profile is chosen and confirmed', async () => {
    const capture = createCapture();
    const deps = dependencies(capture, [profile]);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    const select = sidebar.node.querySelector<HTMLSelectElement>('select');
    let button = Array.from(
      sidebar.node.querySelectorAll<HTMLButtonElement>('button')
    ).find(value => value.textContent === '开始监控');

    expect(select?.value).toBe('');
    expect(button?.disabled).toBe(true);
    if (!select) throw new Error('profile selector missing');
    select.value = `${profile.profile_id}:${profile.version}`;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    button = Array.from(
      sidebar.node.querySelectorAll<HTMLButtonElement>('button')
    ).find(value => value.textContent === '开始监控');
    expect(button?.disabled).toBe(true);
    const consent = sidebar.node.querySelector<HTMLInputElement>(
      'input[type="checkbox"]'
    );
    if (!consent) throw new Error('consent checkbox missing');
    consent.checked = true;
    consent.dispatchEvent(new Event('change', { bubbles: true }));
    button = Array.from(
      sidebar.node.querySelectorAll<HTMLButtonElement>('button')
    ).find(value => value.textContent === '开始监控');
    button?.click();
    await flush();

    expect(capture.start).toHaveBeenCalledWith({
      problem_id: 'synthetic-problem',
      profile_id: profile.profile_id,
      profile_version: 3,
      profile_content_hash: profile.content_hash
    });
    sidebar.dispose();
  });

  it('lists and starts both Profile v1 and Profile v2 through the same capture contract', async () => {
    const capture = createCapture();
    const sidebar = new BehaviorAnalysisSidebar(
      dependencies(capture, [profile, assessmentProfile])
    );
    await flush();
    const select = sidebar.node.querySelector<HTMLSelectElement>(
      '#behavior-analysis-profile'
    );
    if (!select) throw new Error('profile selector missing');
    expect(
      Array.from(select.options).map(option => option.textContent)
    ).toEqual(
      expect.arrayContaining([
        '合成调试题 · v3',
        '平均值知识点考核 · v1 · 1点/0测'
      ])
    );

    select.value = `${assessmentProfile.profile_id}:${assessmentProfile.version}`;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    const consent = sidebar.node.querySelector<HTMLInputElement>(
      '#behavior-analysis-consent'
    );
    if (!consent) throw new Error('consent checkbox missing');
    consent.checked = true;
    consent.dispatchEvent(new Event('change', { bubbles: true }));
    findButton(sidebar, '开始监控').click();
    await flush();

    expect(capture.start).toHaveBeenCalledWith({
      problem_id: assessmentProfile.problem_id,
      profile_id: assessmentProfile.profile_id,
      profile_version: assessmentProfile.version,
      profile_content_hash: assessmentProfile.content_hash
    });
    sidebar.dispose();
  });

  it('shortens long profile titles in the narrow selector while preserving the full tooltip', async () => {
    const longTitle =
      '这是一道标题非常长需要在狭窄侧栏中缩短显示的平均值知识点综合考核题';
    const longProfile = {
      ...assessmentProfile,
      title: longTitle
    };
    const sidebar = new BehaviorAnalysisSidebar(
      dependencies(createCapture(), [longProfile])
    );
    await flush();
    const option = Array.from(
      sidebar.node.querySelectorAll<HTMLOptionElement>(
        '#behavior-analysis-profile option'
      )
    ).find(item => item.value !== '');

    expect(option?.textContent).toContain('… · v1 · 1点/0测');
    expect(option?.textContent).not.toContain(longTitle);
    expect(option?.title).toContain(longTitle);
    sidebar.dispose();
  });

  it('completes the public pilot workflow from consented selection through review', async () => {
    const workflowProfile: IDimensionProfileVersion = {
      ...profile,
      dimensions: [
        {
          code: 'SYNTHETIC_WORKFLOW',
          name: '失败后修改并验证',
          question: '失败后是否修改并再次运行？',
          evidence_criteria: [
            {
              id: 'support-1',
              direction: 'support',
              statement: '失败后修改并再次运行'
            },
            {
              id: 'exclude-1',
              direction: 'exclude',
              statement: '只改注释不计入'
            }
          ],
          levels: [
            {
              code: 'possible',
              name: '可能出现',
              definition: '存在一次相关行为'
            },
            {
              code: 'clear',
              name: '明显出现',
              definition: '多次持续出现相关行为'
            }
          ],
          teaching_actions: {
            possible: '询问本次修改思路',
            clear: '安排修改后立即验证练习'
          },
          analysis_config: {
            mode: 'llm_evidence',
            minimum_observation: { edit_event_count: 1 }
          }
        }
      ]
    };
    const workflowResult: IAnalysisResult = {
      ...analysis,
      dimension_results: [
        {
          dimension_code: 'SYNTHETIC_WORKFLOW',
          decision: {
            status: 'resolved',
            final_evidence_status: 'observed',
            final_level_code: 'possible',
            display_label: '可能出现',
            source: 'llm_evidence'
          },
          data_quality: {
            missing_required_signals: [],
            observation_opportunities: 1,
            reason_code: null,
            reason: null
          },
          ai_result: {
            confidence: 0.8,
            evidence_claims: [
              {
                event_id: `${job.session_id}:2`,
                criterion_id: 'support-1',
                direction: 'support',
                claim: '运行失败后修改并成功运行'
              }
            ],
            explanation: '只根据本次合成会话中的行为记录。'
          },
          review: { revision: 0, status: 'unreviewed' }
        }
      ],
      provenance: {
        ...analysis.provenance,
        provider_request_id: 'synthetic-private-provider-request',
        prompt_content_hash: 'f'.repeat(64),
        raw_response_hash: '9'.repeat(64),
        input_snapshot_hash: '8'.repeat(64)
      }
    };
    let active = false;
    let current = { ...snapshot };
    let listener: ((value: IUploadSnapshot) => void) | undefined;
    const capture = createCapture();
    capture.isEnabled.mockImplementation(() => active);
    capture.snapshot.mockImplementation(() => current);
    capture.subscribe.mockImplementation(value => {
      listener = value;
      return () => undefined;
    });
    capture.start.mockImplementation(async () => {
      active = true;
      current = {
        ...snapshot,
        sessionId: job.session_id,
        uploadState: 'collecting'
      };
    });
    capture.stop.mockImplementation(async () => {
      active = false;
      current = {
        ...current,
        uploadState: 'finalized',
        queuedCount: 0,
        lastServerSequence: 4
      };
      return {
        schema_version: 1,
        request_id: 'synthetic-workflow-finalize',
        session_id: job.session_id,
        status: 'finalized',
        last_contiguous_sequence: 4,
        analysis_job_id: job.job_id
      };
    });
    const deps = dependencies(capture, [workflowProfile]);
    deps.setTimer = jest.fn(() => 1) as unknown as typeof setTimeout;
    deps.clearTimer = jest.fn() as unknown as typeof clearTimeout;
    deps.getAnalysisJob = jest
      .fn()
      .mockResolvedValueOnce({ ...job, status: 'queued' as const })
      .mockResolvedValueOnce({
        ...job,
        status: 'running' as const,
        active_attempt_id: workflowResult.attempt_id,
        attempt_ids: [workflowResult.attempt_id]
      })
      .mockResolvedValueOnce({
        ...job,
        status: 'ready' as const,
        active_attempt_id: workflowResult.attempt_id,
        attempt_ids: [workflowResult.attempt_id],
        analysis_id: workflowResult.analysis_id
      });
    deps.getSessionAnalysis = jest.fn(async () => workflowResult);
    deps.getProfileVersion = jest.fn(async () => workflowProfile);
    deps.reviewDimension = jest.fn(async (_settings, _session, _code) => ({
      ...workflowResult.dimension_results[0],
      decision: {
        ...workflowResult.dimension_results[0].decision,
        final_level_code: 'clear' as const,
        display_label: '明显出现'
      },
      review: { revision: 1, status: 'reviewed' as const }
    }));
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();

    selectAndConsent(sidebar);
    findButton(sidebar, '开始监控').click();
    await flush();
    expect(capture.start).toHaveBeenCalledWith({
      problem_id: workflowProfile.problem_id,
      profile_id: workflowProfile.profile_id,
      profile_version: workflowProfile.version,
      profile_content_hash: workflowProfile.content_hash
    });

    current = {
      ...current,
      eventCount: 4,
      queuedCount: 2,
      lastSequence: 4,
      lastServerSequence: 2
    };
    listener?.(current);
    expect(sidebar.node.textContent).toContain('已采集事件数：4；待上传数：2');

    findButton(sidebar, '停止监控').click();
    await flush();
    expect(sidebar.node.textContent).toContain('分析任务：已排队');

    findButton(sidebar, '刷新状态').click();
    await flush();
    expect(sidebar.node.textContent).toContain('分析任务：已排队');
    findButton(sidebar, '刷新状态').click();
    await flush();
    expect(sidebar.node.textContent).toContain('分析任务：分析中');
    findButton(sidebar, '刷新状态').click();
    await flush();

    expect(sidebar.node.textContent).toContain('本次会话结果');
    expect(sidebar.node.textContent).toContain('失败后修改并验证');
    expect(sidebar.node.textContent).toContain('可能出现');
    const technicalDetails = sidebar.node.querySelector<HTMLDetailsElement>(
      '.jp-BehaviorAudit-analysisDetails'
    )!;
    expect(technicalDetails.open).toBe(false);
    expect(sidebar.node.textContent).not.toContain(
      'synthetic-private-provider-request'
    );
    expect(sidebar.node.textContent).not.toContain('f'.repeat(64));
    expect(sidebar.node.textContent).not.toContain('准确率');
    expect(sidebar.node.textContent).not.toContain('支持度');

    const form = sidebar.node.querySelector<HTMLFormElement>(
      '.jp-BehaviorAudit-reviewForm'
    )!;
    form.querySelector<HTMLInputElement>('input[value="clear"]')!.checked =
      true;
    form.querySelector<HTMLTextAreaElement>('textarea')!.value =
      '固定合成教师修正';
    form.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true })
    );
    await flush();

    expect(deps.reviewDimension).toHaveBeenCalledWith(
      settings,
      job.session_id,
      'SYNTHETIC_WORKFLOW',
      {
        revision: 0,
        decision_status: 'resolved',
        evidence_status: 'observed',
        level_code: 'clear',
        evidence_event_ids: [`${job.session_id}:2`],
        reason_code: 'teacher_correction',
        comment: '固定合成教师修正'
      }
    );
    expect(sidebar.node.textContent).toContain('复核结果已更新');
    expect(sidebar.node.textContent).toContain('明显出现');
    sidebar.dispose();
  });

  it('shows an explicit empty state instead of starting capture without profiles', async () => {
    const sidebar = new BehaviorAnalysisSidebar(
      dependencies(createCapture(), [])
    );
    await flush();

    expect(sidebar.node.textContent).toContain('还没有已发布方案');
    expect(sidebar.node.textContent).toContain('创建题目考核方案');
    expect(
      Array.from(
        sidebar.node.querySelectorAll<HTMLButtonElement>('button')
      ).find(value => value.textContent === '开始监控')?.disabled
    ).toBe(true);
    sidebar.dispose();
  });

  it('preserves a still-published selection and updates capture counts from its subscription', async () => {
    let listener: ((value: IUploadSnapshot) => void) | undefined;
    const capture = createCapture();
    capture.subscribe.mockImplementation(value => {
      listener = value;
      return () => undefined;
    });
    const deps = dependencies(capture, [profile]);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    const select = sidebar.node.querySelector<HTMLSelectElement>('select')!;
    select.value = `${profile.profile_id}:${profile.version}`;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    await sidebar.refreshProfiles();
    listener?.({ ...snapshot, eventCount: 4, queuedCount: 2 });

    expect(sidebar.node.querySelector<HTMLSelectElement>('select')?.value).toBe(
      `${profile.profile_id}:${profile.version}`
    );
    expect(sidebar.node.textContent).toContain('已采集事件数：4；待上传数：2');
    sidebar.dispose();
  });

  it('makes upload and finalization phases observable before queuing analysis', async () => {
    let resolveStop: ((value: ISessionFinalizeResponse) => void) | undefined;
    let listener: ((value: IUploadSnapshot) => void) | undefined;
    const capture = createCapture();
    capture.isEnabled.mockReturnValue(true);
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id,
      uploadState: 'collecting'
    });
    capture.subscribe.mockImplementation(value => {
      listener = value;
      return () => undefined;
    });
    capture.stop.mockImplementation(
      () =>
        new Promise(resolve => {
          resolveStop = resolve;
        })
    );
    const deps = dependencies(capture, [profile]);
    deps.getAnalysisJob = jest.fn(async () => job);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    const stopping = sidebar.stopMonitoring();
    listener?.({
      ...snapshot,
      sessionId: job.session_id,
      uploadState: 'draining',
      queuedCount: 2
    });
    expect(sidebar.node.textContent).toContain('正在上传剩余记录…');
    listener?.({
      ...snapshot,
      sessionId: job.session_id,
      uploadState: 'finalizing'
    });
    expect(sidebar.node.textContent).toContain('正在提交完整会话…');
    resolveStop?.({
      schema_version: 1,
      request_id: 'synthetic-request',
      session_id: job.session_id,
      status: 'finalized',
      last_contiguous_sequence: 1,
      analysis_job_id: job.job_id
    });
    await stopping;
    expect(sidebar.node.textContent).toContain('分析已排队');
    sidebar.dispose();
  });

  it.each(['queued', 'running'] as const)(
    'explains bounded automatic retry while analysis is %s',
    async status => {
      const capture = createCapture();
      capture.snapshot.mockReturnValue({
        ...snapshot,
        sessionId: job.session_id
      });
      const deps = dependencies(capture, [profile]);
      deps.getAnalysisJob = jest.fn(async () => ({ ...job, status }));
      const sidebar = new BehaviorAnalysisSidebar(deps);
      await flush();
      (sidebar as unknown as { job: IAnalysisJob }).job = { ...job, status };
      await sidebar.refreshAnalysis();

      expect(sidebar.node.textContent).toContain(
        'AI 正在分析；响应较慢时会自动重试，最长约 180 秒。'
      );
      sidebar.dispose();
    }
  );

  it('maps job errors to action text and retries only on an explicit click', async () => {
    const capture = createCapture();
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id
    });
    const deps = dependencies(capture, [profile]);
    deps.getAnalysisJob = jest.fn(async () => ({
      ...job,
      status: 'error' as const,
      error_code: 'model_timeout'
    }));
    deps.retryAnalysisJob = jest.fn(async () => job);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    (sidebar as unknown as { job: IAnalysisJob }).job = job;
    await sidebar.refreshAnalysis();
    expect(sidebar.node.textContent).toContain('模型响应超时，可以重试分析。');
    Array.from(sidebar.node.querySelectorAll<HTMLButtonElement>('button'))
      .find(value => value.textContent === '重试分析')
      ?.click();
    await flush();
    expect(deps.retryAnalysisJob).toHaveBeenCalled();
    sidebar.dispose();
  });

  it('loads ready results and leaves collapsed AI and advanced sections keyboard-native', async () => {
    const capture = createCapture();
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id
    });
    const deps = dependencies(capture, [profile]);
    deps.getAnalysisJob = jest.fn(async () => ({
      ...job,
      status: 'ready' as const
    }));
    deps.getSessionAnalysis = jest.fn(async () => analysis);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    (sidebar as unknown as { job: IAnalysisJob }).job = job;
    await sidebar.refreshAnalysis();
    const collapsed = Array.from(sidebar.node.querySelectorAll('details'));

    expect(sidebar.node.textContent).toContain('本次会话结果');
    expect(collapsed.every(value => !value.open)).toBe(true);
    expect(
      sidebar.node.querySelector('label[for="behavior-analysis-ai-key"]')
    ).toBeTruthy();
    expect(sidebar.node.querySelector('[aria-live]')).toBeTruthy();
    expect(collapsed.every(value => value.querySelector('summary'))).toBe(true);
    sidebar.dispose();
  });

  it('treats a missing legacy analysis as an empty old-data list', async () => {
    const capture = createCapture();
    const deps = dependencies(capture, [profile]);
    deps.requestLatestAnalysis = jest.fn(async () => {
      throw new ApiError(404, 'http_error', 'synthetic missing data', false);
    });
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();

    const advanced = Array.from(sidebar.node.querySelectorAll('details')).find(
      value => value.querySelector('summary')?.textContent === '高级数据'
    )!;
    advanced.open = true;
    Array.from(advanced.querySelectorAll<HTMLButtonElement>('button'))
      .find(value => value.textContent === '刷新旧数据')!
      .click();
    await flush();

    expect(advanced.textContent).toContain('暂无旧数据。');
    expect(advanced.textContent).not.toContain('旧数据读取失败');
    sidebar.dispose();
  });

  it('does not start a stored collecting session and abandons it explicitly', async () => {
    const stored: ISessionState = {
      schema_version: 1,
      request_id: 'synthetic-stored-request',
      session_id: job.session_id,
      problem_id: profile.problem_id,
      profile_id: profile.profile_id,
      profile_version: profile.version,
      profile_content_hash: profile.content_hash,
      status: 'collecting',
      last_contiguous_sequence: 2,
      received_event_count: 3,
      analysis_job_id: null
    };
    const capture = createCapture();
    const deps = dependencies(capture, [profile]);
    deps.getStoredActiveSession = jest.fn(async () => stored);
    deps.abandonSession = jest.fn(async () => ({
      ...stored,
      status: 'abandoned' as const
    }));
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    expect(capture.start).not.toHaveBeenCalled();
    expect(sidebar.node.textContent).toContain('检测到未完成会话');
    Array.from(sidebar.node.querySelectorAll<HTMLButtonElement>('button'))
      .find(value => value.textContent === '放弃未完成会话')
      ?.click();
    await flush();
    expect(deps.abandonSession).toHaveBeenCalledWith(
      settings,
      stored.session_id,
      'teacher_abandoned_local_session'
    );
    sidebar.dispose();
  });

  it('polls a single loop at 1/2/4/8/10 seconds and stops on a terminal result or dispose', async () => {
    const timers: Array<{ delay: number; callback: () => void }> = [];
    const capture = createCapture();
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id
    });
    const deps = dependencies(capture, [profile]);
    deps.setTimer = ((callback: () => void, delay?: number) => {
      timers.push({ callback, delay: delay ?? 0 });
      return timers.length;
    }) as unknown as typeof setTimeout;
    deps.clearTimer = jest.fn() as unknown as typeof clearTimeout;
    deps.getAnalysisJob = jest.fn(async () => job);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    (sidebar as unknown as { job: IAnalysisJob }).job = job;
    (sidebar as unknown as { startPolling: () => void }).startPolling();
    expect(timers.map(value => value.delay)).toEqual([1000]);
    for (const expected of [2000, 4000, 8000, 10000]) {
      timers.shift()?.callback();
      await flush();
      expect(timers[0]?.delay).toBe(expected);
    }
    deps.getAnalysisJob = jest.fn(async () => ({
      ...job,
      status: 'ready' as const
    }));
    deps.getSessionAnalysis = jest.fn(async () => analysis);
    deps.listSessionLogs = jest.fn(async sessionId =>
      sessionLogs(sessionId, 'ready')
    );
    timers.shift()?.callback();
    await flush();
    expect(timers).toHaveLength(0);
    expect(sidebar.node.textContent).toContain('本次会话结果');
    (sidebar as unknown as { job: IAnalysisJob }).job = job;
    (sidebar as unknown as { startPolling: () => void }).startPolling();
    sidebar.dispose();
    expect(deps.clearTimer).toHaveBeenCalled();
  });

  it('caps polling after five minutes without creating a second loop', async () => {
    const timers: Array<{ delay: number; callback: () => void }> = [];
    let now = 0;
    const deps = dependencies(createCapture(), [profile]);
    deps.now = () => now;
    deps.setTimer = ((callback: () => void, delay?: number) => {
      timers.push({ callback, delay: delay ?? 0 });
      return timers.length;
    }) as unknown as typeof setTimeout;
    deps.getAnalysisJob = jest.fn(async () => job);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    (sidebar as unknown as { job: IAnalysisJob }).job = job;
    (sidebar as unknown as { startPolling: () => void }).startPolling();
    (sidebar as unknown as { startPolling: () => void }).startPolling();
    expect(timers).toHaveLength(1);
    now = 5 * 60 * 1000;
    timers.shift()?.callback();
    await flush();
    expect(sidebar.node.textContent).toContain('等待时间较长，请手动刷新状态');
    expect(timers).toHaveLength(0);
    sidebar.dispose();
  });

  it('restores a finalized stored session and clears an abandoned stale key without capture', async () => {
    const finalized: ISessionState = {
      schema_version: 1,
      request_id: 'synthetic-finalized-request',
      session_id: job.session_id,
      problem_id: profile.problem_id,
      profile_id: profile.profile_id,
      profile_version: profile.version,
      profile_content_hash: profile.content_hash,
      status: 'finalized',
      last_contiguous_sequence: 2,
      received_event_count: 3,
      analysis_job_id: job.job_id
    };
    const storage = {
      getItem: jest.fn(),
      setItem: jest.fn(),
      removeItem: jest.fn()
    } as unknown as Storage;
    const capture = createCapture();
    const deps = dependencies(capture, [profile]);
    deps.storage = storage;
    deps.getStoredActiveSession = jest.fn(async () => finalized);
    deps.getAnalysisJob = jest.fn(async () => ({
      ...job,
      status: 'partial' as const
    }));
    deps.getSessionAnalysis = jest.fn(async () => ({
      ...analysis,
      status: 'partial' as const,
      error_code: 'ai_not_configured' as const
    }));
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    await flush();
    expect(storage.removeItem).toHaveBeenCalledWith(
      'myextension:active-session'
    );
    expect(capture.start).not.toHaveBeenCalled();
    expect(sidebar.node.textContent).toContain('本次会话数据');
    expect(sidebar.node.textContent).toContain(
      '数据采集完成，尚未进行 AI 分析'
    );
    expect(
      sidebar.node.querySelector('.jp-BehaviorAudit-resultCard')
    ).toBeNull();
    sidebar.dispose();
  });

  it('keeps state until exact delete receipt and submits review conflicts as refreshed', async () => {
    let resolveDelete:
      | ((value: {
          schema_version: 1;
          request_id: string;
          deleted_session_id: string;
        }) => void)
      | undefined;
    const capture = createCapture();
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id
    });
    const deps = dependencies(capture, [profile]);
    deps.deleteSession = jest.fn(
      () =>
        new Promise(resolve => {
          resolveDelete = resolve;
        })
    );
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    (sidebar as unknown as { analysis: IAnalysisResult }).analysis = analysis;
    (
      sidebar as unknown as { analysisProfile: IDimensionProfileVersion }
    ).analysisProfile = profile;
    (sidebar as unknown as { render: () => void }).render();
    const input = sidebar.node.querySelector<HTMLInputElement>(
      '#delete-session-confirmation'
    )!;
    const remove = Array.from(
      sidebar.node.querySelectorAll<HTMLButtonElement>('button')
    ).find(value => value.textContent === '删除本次会话')!;
    input.value = 'wrong';
    remove.click();
    expect(deps.deleteSession).not.toHaveBeenCalled();
    input.value = analysis.session_id;
    remove.click();
    expect(sidebar.node.textContent).toContain('本次会话结果');
    resolveDelete?.({
      schema_version: 1,
      request_id: 'synthetic-delete',
      deleted_session_id: analysis.session_id
    });
    await flush();
    expect(sidebar.node.textContent).not.toContain('本次会话结果');
    sidebar.dispose();
  });

  it('uses the server review projection and refreshes a 409 without a generic failure', async () => {
    const reviewedAnalysis: IAnalysisResult = {
      ...analysis,
      dimension_results: [
        {
          dimension_code: 'SYNTHETIC',
          decision: {
            status: 'needs_review',
            final_evidence_status: null,
            final_level_code: null,
            display_label: 'synthetic',
            source: 'coverage'
          },
          data_quality: {
            missing_required_signals: [],
            observation_opportunities: 0,
            reason_code: null,
            reason: null
          },
          ai_result: null,
          review: { revision: 2, status: 'unreviewed' }
        }
      ]
    };
    const capture = createCapture();
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id
    });
    const deps = dependencies(capture, [profile]);
    deps.reviewDimension = jest.fn(async () => ({
      ...reviewedAnalysis.dimension_results[0],
      decision: {
        ...reviewedAnalysis.dimension_results[0].decision,
        status: 'resolved' as const,
        final_evidence_status: 'not_observed' as const
      }
    }));
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    (sidebar as unknown as { analysis: IAnalysisResult }).analysis =
      reviewedAnalysis;
    (
      sidebar as unknown as { analysisProfile: IDimensionProfileVersion }
    ).analysisProfile = profile;
    await (
      sidebar as unknown as {
        submitReview: (code: string, payload: object) => Promise<void>;
      }
    ).submitReview('SYNTHETIC', {
      revision: 2,
      decision_status: 'resolved',
      evidence_status: 'not_observed',
      level_code: null,
      evidence_event_ids: [],
      reason_code: 'teacher_correction',
      comment: 'synthetic review'
    });
    expect(
      (sidebar as unknown as { analysis: IAnalysisResult }).analysis
        .dimension_results[0].decision.final_evidence_status
    ).toBe('not_observed');
    deps.reviewDimension = jest.fn(async () => {
      throw new ApiError(409, 'revision_conflict', 'synthetic', false);
    });
    deps.getSessionAnalysis = jest.fn(async () => reviewedAnalysis);
    await expect(
      (
        sidebar as unknown as {
          submitReview: (code: string, payload: object) => Promise<void>;
        }
      ).submitReview('SYNTHETIC', {
        revision: 2,
        decision_status: 'resolved',
        evidence_status: 'not_observed',
        level_code: null,
        evidence_event_ids: [],
        reason_code: 'teacher_correction',
        comment: 'synthetic review'
      })
    ).resolves.toBeUndefined();
    expect(sidebar.node.textContent).toContain('复核结果已刷新');
    expect(sidebar.node.textContent).not.toContain('复核提交失败');
    sidebar.dispose();
  });

  it('clears session A result and binds deletion to session B after a new public start', async () => {
    const sessionB = '623e4567-e89b-42d3-a456-426614174000';
    const capture = createCapture();
    let current = { ...snapshot, sessionId: analysis.session_id };
    capture.snapshot.mockImplementation(() => current);
    capture.start.mockImplementation(async () => {
      current = {
        ...snapshot,
        sessionId: sessionB,
        uploadState: 'collecting'
      };
    });
    const deps = dependencies(capture, [profile]);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    (sidebar as unknown as { analysis: IAnalysisResult }).analysis = analysis;
    (
      sidebar as unknown as { analysisProfile: IDimensionProfileVersion }
    ).analysisProfile = profile;
    (sidebar as unknown as { render: () => void }).render();
    expect(sidebar.node.textContent).toContain('本次会话结果');

    const select = sidebar.node.querySelector<HTMLSelectElement>('select')!;
    select.value = `${profile.profile_id}:${profile.version}`;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    const consent = sidebar.node.querySelector<HTMLInputElement>(
      'input[type="checkbox"]'
    )!;
    consent.checked = true;
    consent.dispatchEvent(new Event('change', { bubbles: true }));
    Array.from(sidebar.node.querySelectorAll<HTMLButtonElement>('button'))
      .find(value => value.textContent === '开始监控')
      ?.click();
    await flush();

    expect(sidebar.node.textContent).not.toContain('本次会话结果');
    const deleteInput = sidebar.node.querySelector<HTMLInputElement>(
      '#delete-session-confirmation'
    )!;
    deleteInput.value = sessionB;
    Array.from(sidebar.node.querySelectorAll<HTMLButtonElement>('button'))
      .find(value => value.textContent === '删除本次会话')
      ?.click();
    await flush();
    expect(deps.deleteSession).toHaveBeenCalledWith(
      settings,
      sessionB,
      'local_teacher',
      'teacher_requested_deletion'
    );
    sidebar.dispose();
  });

  it('ignores a deferred stored-session A restore after an explicit public start of B', async () => {
    const sessionB = '623e4567-e89b-42d3-a456-426614174000';
    const storedSessionA: ISessionState = {
      schema_version: 1,
      request_id: 'synthetic-delayed-restore',
      session_id: analysis.session_id,
      problem_id: profile.problem_id,
      profile_id: profile.profile_id,
      profile_version: profile.version,
      profile_content_hash: profile.content_hash,
      status: 'finalized',
      last_contiguous_sequence: 1,
      received_event_count: 1,
      analysis_job_id: job.job_id
    };
    const restore = deferred<ISessionState | null>();
    const pendingDelete = deferred<{
      schema_version: 1;
      request_id: string;
      deleted_session_id: string;
    }>();
    const storage = {
      getItem: jest.fn(),
      setItem: jest.fn(),
      removeItem: jest.fn()
    } as unknown as Storage;
    const capture = createCapture();
    let current = { ...snapshot };
    capture.snapshot.mockImplementation(() => current);
    capture.start.mockImplementation(async () => {
      current = {
        ...snapshot,
        sessionId: sessionB,
        uploadState: 'collecting'
      };
    });
    const deps = dependencies(capture, [profile]);
    deps.storage = storage;
    deps.getStoredActiveSession = jest.fn(() => restore.promise);
    deps.deleteSession = jest.fn(() => pendingDelete.promise);
    deps.getAnalysisJob = jest.fn(async () => ({
      ...job,
      status: 'ready' as const
    }));
    deps.getSessionAnalysis = jest.fn(async () => analysis);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();

    const select = sidebar.node.querySelector<HTMLSelectElement>('select')!;
    select.value = `${profile.profile_id}:${profile.version}`;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    const consent = sidebar.node.querySelector<HTMLInputElement>(
      'input[type="checkbox"]'
    )!;
    consent.checked = true;
    consent.dispatchEvent(new Event('change', { bubbles: true }));
    Array.from(sidebar.node.querySelectorAll<HTMLButtonElement>('button'))
      .find(value => value.textContent === '开始监控')
      ?.click();
    await flush();

    restore.resolve(storedSessionA);
    await flush();

    expect(deps.getAnalysisJob).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
    expect(sidebar.node.textContent).not.toContain('本次会话结果');
    const deleteInput = sidebar.node.querySelector<HTMLInputElement>(
      '#delete-session-confirmation'
    )!;
    deleteInput.value = sessionB;
    Array.from(sidebar.node.querySelectorAll<HTMLButtonElement>('button'))
      .find(value => value.textContent === '删除本次会话')
      ?.click();
    expect(deps.deleteSession).toHaveBeenCalledWith(
      settings,
      sessionB,
      'local_teacher',
      'teacher_requested_deletion'
    );
    expect(storage.removeItem).not.toHaveBeenCalled();
    sidebar.dispose();
  });

  it('fetches and verifies the immutable bound profile version for a restored result', async () => {
    const versionOne = {
      ...profile,
      version: 1,
      content_hash: '1'.repeat(64),
      title: '合成旧版本'
    };
    const versionTwo = {
      ...profile,
      version: 2,
      content_hash: '2'.repeat(64),
      title: '合成新版本'
    };
    const oldAnalysis = {
      ...analysis,
      profile_version: 1,
      profile_content_hash: versionOne.content_hash
    };
    const restored: ISessionState = {
      schema_version: 1,
      request_id: 'synthetic-restore',
      session_id: analysis.session_id,
      problem_id: profile.problem_id,
      profile_id: profile.profile_id,
      profile_version: 1,
      profile_content_hash: versionOne.content_hash,
      status: 'finalized',
      last_contiguous_sequence: 1,
      received_event_count: 1,
      analysis_job_id: job.job_id
    };
    const deps = dependencies(createCapture(), [versionTwo]);
    deps.getStoredActiveSession = jest.fn(async () => restored);
    deps.getAnalysisJob = jest.fn(async () => ({
      ...job,
      session_id: restored.session_id,
      status: 'ready' as const
    }));
    deps.getSessionAnalysis = jest.fn(async () => oldAnalysis);
    deps.getProfileVersion = jest.fn(async () => versionOne);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();

    expect(deps.getProfileVersion).toHaveBeenCalledWith(
      settings,
      profile.profile_id,
      1
    );
    expect(sidebar.node.textContent).toContain('本次会话结果');
    expect(sidebar.node.textContent).not.toContain('找不到绑定方案');
    sidebar.dispose();
  });

  it('retains interactive AI state across capture renders and performs one config GET', async () => {
    let listener: ((value: IUploadSnapshot) => void) | undefined;
    const capture = createCapture();
    capture.subscribe.mockImplementation(value => {
      listener = value;
      return () => undefined;
    });
    const deps = dependencies(capture, [profile]);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    const details = sidebar.node.querySelector<HTMLDetailsElement>(
      '.jp-BehaviorAudit-aiConfig'
    )!;
    details.open = true;
    const base = details.querySelector<HTMLInputElement>(
      '#behavior-analysis-ai-base-url'
    )!;
    base.value = 'https://synthetic.invalid/v1';
    listener?.({ ...snapshot, eventCount: 3 });

    expect(
      sidebar.node.querySelector<HTMLDetailsElement>(
        '.jp-BehaviorAudit-aiConfig'
      )?.open
    ).toBe(true);
    expect(
      sidebar.node.querySelector<HTMLInputElement>(
        '#behavior-analysis-ai-base-url'
      )?.value
    ).toBe('https://synthetic.invalid/v1');
    expect(deps.requestAIConfig).toHaveBeenCalledTimes(1);
    sidebar.dispose();
  });

  it('keeps one live busy AI save across a capture render and updates that live control on completion', async () => {
    let listener: ((value: IUploadSnapshot) => void) | undefined;
    const save = deferred<{
      status: 'success';
      api_key_configured: boolean;
    }>();
    const capture = createCapture();
    capture.subscribe.mockImplementation(value => {
      listener = value;
      return () => undefined;
    });
    const deps = dependencies(capture, [profile]);
    deps.requestAIConfig = jest.fn(init =>
      init?.method === 'POST'
        ? save.promise
        : Promise.resolve({
            status: 'success' as const,
            api_key_configured: true
          })
    );
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    const details = sidebar.node.querySelector<HTMLDetailsElement>(
      '.jp-BehaviorAudit-aiConfig'
    )!;
    details.open = true;
    details.querySelector<HTMLInputElement>(
      '#behavior-analysis-ai-base-url'
    )!.value = 'https://synthetic.invalid/v1';
    details.querySelector<HTMLInputElement>(
      '#behavior-analysis-ai-model'
    )!.value = 'synthetic-model';
    details.querySelector<HTMLInputElement>(
      '#behavior-analysis-ai-key'
    )!.value = 'synthetic-key';
    expect(
      details.querySelector<HTMLInputElement>('#behavior-analysis-ai-key')
        ?.placeholder
    ).toBe('API Key 已配置');
    const originalSave = Array.from(
      details.querySelectorAll<HTMLButtonElement>('button')
    ).find(value => value.textContent === '保存 AI 配置')!;
    originalSave.click();

    expect(originalSave.disabled).toBe(true);
    expect(originalSave.getAttribute('aria-busy')).toBe('true');
    expect(details.textContent).toContain('正在保存 AI 配置');
    listener?.({ ...snapshot, eventCount: 7 });

    const liveDetails = sidebar.node.querySelector<HTMLDetailsElement>(
      '.jp-BehaviorAudit-aiConfig'
    )!;
    const liveSave = Array.from(
      liveDetails.querySelectorAll<HTMLButtonElement>('button')
    ).find(value => value.textContent === '保存 AI 配置')!;
    expect(liveSave).toBe(originalSave);
    expect(liveSave.disabled).toBe(true);
    expect(liveSave.getAttribute('aria-busy')).toBe('true');
    expect(liveDetails.textContent).toContain('正在保存 AI 配置');
    liveSave.click();
    expect(
      (deps.requestAIConfig as jest.Mock).mock.calls.filter(
        ([init]) => init?.method === 'POST'
      )
    ).toHaveLength(1);

    save.resolve({ status: 'success', api_key_configured: true });
    await flush();
    expect(liveSave.disabled).toBe(false);
    expect(liveSave.hasAttribute('aria-busy')).toBe(false);
    expect(liveDetails.textContent).toContain('AI 配置已保存');
    expect(
      liveDetails.querySelector<HTMLInputElement>('#behavior-analysis-ai-key')
        ?.value
    ).toBe('');
    sidebar.dispose();
  });

  it('clears a configured key after explicit confirmation', async () => {
    const deps = dependencies(createCapture(), [profile]);
    deps.requestAIConfig = jest.fn(init =>
      Promise.resolve(
        init?.method === 'POST'
          ? {
              status: 'success' as const,
              api_key_configured: false
            }
          : {
              status: 'success' as const,
              api_key_configured: true
            }
      )
    );
    const confirmClearAIKey = jest.fn(async () => true);
    (
      deps as IBehaviorAnalysisSidebarDependencies & {
        confirmClearAIKey: () => Promise<boolean>;
      }
    ).confirmClearAIKey = confirmClearAIKey;
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();

    findButton(sidebar, '清除已保存 Key').click();
    await flush();

    expect(confirmClearAIKey).toHaveBeenCalledTimes(1);
    expect(deps.requestAIConfig).toHaveBeenLastCalledWith(
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ clear_api_key: true })
      })
    );
    expect(sidebar.node.textContent).toContain('AI 状态：未配置');
    expect(
      Array.from(sidebar.node.querySelectorAll('button')).some(
        value => value.textContent === '清除已保存 Key' && !value.hidden
      )
    ).toBe(false);
    sidebar.dispose();
  });

  it('does not clear a configured key when confirmation is cancelled', async () => {
    const deps = dependencies(createCapture(), [profile]);
    deps.requestAIConfig = jest.fn(async () => ({
      status: 'success' as const,
      api_key_configured: true
    }));
    const confirmClearAIKey = jest.fn(async () => false);
    (
      deps as IBehaviorAnalysisSidebarDependencies & {
        confirmClearAIKey: () => Promise<boolean>;
      }
    ).confirmClearAIKey = confirmClearAIKey;
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();

    findButton(sidebar, '清除已保存 Key').click();
    await flush();

    expect(confirmClearAIKey).toHaveBeenCalledTimes(1);
    expect(deps.requestAIConfig).toHaveBeenCalledTimes(1);
    sidebar.dispose();
  });

  it('places an actionable AI config validation error beside its field', async () => {
    const deps = dependencies(createCapture(), [profile]);
    deps.requestAIConfig = jest.fn(init =>
      init?.method === 'POST'
        ? Promise.reject(
            new ApiError(
              400,
              'ai_config_validation_failed',
              'AI 配置格式不正确。',
              false,
              {
                field: 'base_url',
                reason: 'insecure_url'
              }
            )
          )
        : Promise.resolve({
            status: 'success' as const,
            api_key_configured: false
          })
    );
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    const details = sidebar.node.querySelector<HTMLDetailsElement>(
      '.jp-BehaviorAudit-aiConfig'
    )!;
    const base = details.querySelector<HTMLInputElement>(
      '#behavior-analysis-ai-base-url'
    )!;
    base.value = 'http://example.invalid';

    findButton(sidebar, '保存 AI 配置').click();
    await flush();

    expect(base.getAttribute('aria-invalid')).toBe('true');
    expect(
      details.querySelector('#behavior-analysis-ai-base-url-error')?.textContent
    ).toContain('HTTPS');
    expect(details.textContent).not.toContain('example.invalid');
    sidebar.dispose();
  });

  it('keeps one live busy review across a capture render and updates that live form on rejection', async () => {
    let listener: ((value: IUploadSnapshot) => void) | undefined;
    const review = deferred<never>();
    const dimension = {
      dimension_code: 'SYNTHETIC_PENDING_REVIEW',
      decision: {
        status: 'needs_review' as const,
        final_evidence_status: null,
        final_level_code: null,
        display_label: 'synthetic pending',
        source: 'coverage' as const
      },
      data_quality: {
        missing_required_signals: [],
        observation_opportunities: 0,
        reason_code: null,
        reason: null
      },
      ai_result: null,
      review: { revision: 1, status: 'unreviewed' as const }
    };
    const result = { ...analysis, dimension_results: [dimension] };
    const capture = createCapture();
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id
    });
    capture.subscribe.mockImplementation(value => {
      listener = value;
      return () => undefined;
    });
    const deps = dependencies(capture, [profile]);
    deps.reviewDimension = jest.fn(() => review.promise);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    (sidebar as unknown as { analysis: IAnalysisResult }).analysis = result;
    (
      sidebar as unknown as { analysisProfile: IDimensionProfileVersion }
    ).analysisProfile = profile;
    (sidebar as unknown as { render: () => void }).render();
    const originalForm = sidebar.node.querySelector<HTMLFormElement>(
      '.jp-BehaviorAudit-reviewForm'
    )!;
    originalForm.querySelector<HTMLTextAreaElement>('textarea')!.value =
      '合成待处理复核';
    originalForm.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true })
    );
    const originalSubmit =
      originalForm.querySelector<HTMLButtonElement>('button')!;
    expect(originalSubmit.disabled).toBe(true);
    expect(originalSubmit.getAttribute('aria-busy')).toBe('true');
    expect(originalForm.textContent).toContain('正在提交复核');

    listener?.({
      ...snapshot,
      sessionId: job.session_id,
      eventCount: 9
    });
    const liveForm = sidebar.node.querySelector<HTMLFormElement>(
      '.jp-BehaviorAudit-reviewForm'
    )!;
    const liveSubmit = liveForm.querySelector<HTMLButtonElement>('button')!;
    expect(liveForm).toBe(originalForm);
    expect(liveSubmit.disabled).toBe(true);
    expect(liveSubmit.getAttribute('aria-busy')).toBe('true');
    expect(liveForm.textContent).toContain('正在提交复核');
    liveForm.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true })
    );
    expect(deps.reviewDimension).toHaveBeenCalledTimes(1);

    review.reject(new Error('synthetic review failure'));
    await flush();
    expect(liveSubmit.disabled).toBe(false);
    expect(liveSubmit.hasAttribute('aria-busy')).toBe(false);
    expect(liveForm.textContent).toContain('复核提交失败，请重试');
    expect(liveForm.querySelector<HTMLTextAreaElement>('textarea')?.value).toBe(
      '合成待处理复核'
    );
    sidebar.dispose();
  });

  it('retains the queue and exposes retry upload/end after stop rejects', async () => {
    const capture = createCapture();
    capture.isEnabled.mockReturnValue(true);
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id,
      uploadState: 'error',
      queuedCount: 2
    });
    capture.stop.mockRejectedValue(new Error('synthetic stop failure'));
    const sidebar = new BehaviorAnalysisSidebar(
      dependencies(capture, [profile])
    );
    await flush();
    await sidebar.stopMonitoring();

    expect(sidebar.node.textContent).toContain('待上传数：2');
    expect(sidebar.node.textContent).toContain('重试上传/结束');
    sidebar.dispose();
  });

  it('clears a stale stop failure after an authoritative job refresh', async () => {
    const capture = createCapture();
    const deps = dependencies(capture, [profile]);
    deps.getAnalysisJob = jest.fn(async () => job);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    Object.assign(sidebar as unknown as Record<string, unknown>, {
      currentSessionId: job.session_id,
      job,
      stopFailed: true
    });

    await sidebar.refreshAnalysis();

    expect(sidebar.node.textContent).not.toContain('重试上传/结束');
    expect(sidebar.node.textContent).toContain('分析任务：已排队');
    sidebar.dispose();
  });

  it('serializes manual and automatic refresh and refuses scheduling after a deferred 300s crossing', async () => {
    jest.useFakeTimers();
    jest.setSystemTime(0);
    let resolveJob: ((value: IAnalysisJob) => void) | undefined;
    const capture = createCapture();
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id
    });
    const deps = dependencies(capture, [profile]);
    deps.setTimer = setTimeout;
    deps.clearTimer = clearTimeout;
    deps.now = Date.now;
    deps.getAnalysisJob = jest.fn(
      () =>
        new Promise(resolve => {
          resolveJob = resolve;
        })
    );
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    await sidebar.stopMonitoring();
    jest.advanceTimersByTime(1000);
    await flush();
    const refresh = Array.from(
      sidebar.node.querySelectorAll<HTMLButtonElement>('button')
    ).find(value => value.textContent === '刷新状态')!;
    expect(refresh.disabled).toBe(true);
    refresh.click();
    expect(deps.getAnalysisJob).toHaveBeenCalledTimes(1);
    jest.setSystemTime(300_001);
    resolveJob?.(job);
    await flush();
    expect(jest.getTimerCount()).toBe(0);
    sidebar.dispose();
    jest.useRealTimers();
  });

  it.each([
    ['ai_not_configured', 'AI 服务配置'],
    ['ai_analysis_failed', 'AI 服务配置'],
    ['ai_analysis_timeout', '分析超时'],
    ['ai_provider_network_error', '网络、DNS 或 TLS'],
    ['ai_provider_rate_limited', '额度或并发'],
    ['ai_provider_auth_failed', 'API Key 和模型权限'],
    ['ai_provider_request_rejected', 'Base URL 和模型名'],
    ['ai_provider_unavailable', '服务暂不可用'],
    ['ai_response_truncated', '输出过长'],
    ['ai_response_invalid', '输出格式'],
    ['model_timeout', '模型响应超时'],
    ['session_not_finalized', '重试上传/结束'],
    ['input_snapshot_mismatch', '联系管理员'],
    ['analysis_input_invalid', '当前结果不可用'],
    ['analysis_output_invalid', '检查维度定义'],
    ['analysis_artifact_write_failed', '服务器分析失败'],
    ['analysis_commit_failed', '服务器分析失败'],
    ['analysis_worker_failed', '服务器分析失败'],
    ['invalid_profile', '检查维度定义']
  ])(
    'maps backend error guidance %s to an actionable message',
    async (code, text) => {
      const capture = createCapture();
      capture.snapshot.mockReturnValue({
        ...snapshot,
        sessionId: job.session_id
      });
      const deps = dependencies(capture, [profile]);
      deps.getAnalysisJob = jest.fn(async () => ({
        ...job,
        status: 'error' as const,
        error_code: code
      }));
      const sidebar = new BehaviorAnalysisSidebar(deps);
      await flush();
      (sidebar as unknown as { job: IAnalysisJob }).job = job;
      await sidebar.refreshAnalysis();
      expect(sidebar.node.textContent).toContain(text);
      const hasRetry = Array.from(
        sidebar.node.querySelectorAll<HTMLButtonElement>('button')
      ).some(value => value.textContent === '重试分析');
      expect(hasRetry).toBe(
        code !== 'input_snapshot_mismatch' && code !== 'analysis_input_invalid'
      );
      sidebar.dispose();
    }
  );

  it('treats analysis_input_invalid as integrity failure without retry guidance', async () => {
    let active = true;
    const capture = createCapture();
    capture.isEnabled.mockImplementation(() => active);
    capture.snapshot.mockImplementation(() => ({
      ...snapshot,
      sessionId: job.session_id,
      uploadState: active ? 'collecting' : 'finalized'
    }));
    capture.stop.mockImplementation(async () => {
      active = false;
      return {
        schema_version: 1,
        request_id: 'synthetic-invalid-input-finalize',
        session_id: job.session_id,
        status: 'finalized',
        last_contiguous_sequence: 1,
        analysis_job_id: job.job_id
      };
    });
    const deps = dependencies(capture, [profile]);
    deps.getAnalysisJob = jest.fn(async () => ({
      ...job,
      status: 'error' as const,
      error_code: 'analysis_input_invalid'
    }));
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    findButton(sidebar, '停止监控').click();
    await flush();
    findButton(sidebar, '刷新状态').click();
    await flush();

    expect(sidebar.node.textContent).toContain('保留数据');
    expect(sidebar.node.textContent).toContain('联系管理员');
    expect(sidebar.node.textContent).toContain('当前结果不可用');
    expect(sidebar.node.textContent).not.toContain('可刷新状态或重试');
    expect(
      Array.from(
        sidebar.node.querySelectorAll<HTMLButtonElement>('button')
      ).some(value => value.textContent === '重试分析')
    ).toBe(false);
    sidebar.dispose();
  });

  it('keeps actionable partial error guidance after loading the partial result', async () => {
    const capture = createCapture();
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id
    });
    const deps = dependencies(capture, [profile]);
    deps.getAnalysisJob = jest.fn(async () => ({
      ...job,
      status: 'partial' as const,
      error_code: 'ai_not_configured'
    }));
    deps.getSessionAnalysis = jest.fn(async () => ({
      ...analysis,
      status: 'partial' as const
    }));
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    (sidebar as unknown as { job: IAnalysisJob }).job = job;
    await sidebar.refreshAnalysis();

    expect(sidebar.node.textContent).toContain('本次会话结果');
    expect(sidebar.node.textContent).toContain('AI 服务配置');
    expect(sidebar.node.textContent).toContain('重试分析');
    sidebar.dispose();
  });

  it('treats exact deletion as authoritative, clears upload projection, and ignores a late refresh', async () => {
    let resolveRefresh: ((value: IAnalysisJob) => void) | undefined;
    const capture = createCapture();
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id,
      uploadState: 'finalized',
      eventCount: 8,
      queuedCount: 1
    });
    const storage = {
      getItem: jest.fn(),
      setItem: jest.fn(),
      removeItem: jest.fn(() => {
        throw new Error('synthetic storage denial');
      })
    } as unknown as Storage;
    const deps = dependencies(capture, [profile]);
    deps.storage = storage;
    deps.getAnalysisJob = jest.fn(
      () =>
        new Promise(resolve => {
          resolveRefresh = resolve;
        })
    );
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    (sidebar as unknown as { job: IAnalysisJob }).job = job;
    (sidebar as unknown as { analysis: IAnalysisResult }).analysis = analysis;
    (
      sidebar as unknown as { analysisProfile: IDimensionProfileVersion }
    ).analysisProfile = profile;
    (sidebar as unknown as { render: () => void }).render();
    const inFlight = sidebar.refreshAnalysis();
    const confirmation = sidebar.node.querySelector<HTMLInputElement>(
      '#delete-session-confirmation'
    )!;
    confirmation.value = job.session_id;
    Array.from(sidebar.node.querySelectorAll<HTMLButtonElement>('button'))
      .find(value => value.textContent === '删除本次会话')
      ?.click();
    await flush();

    expect(sidebar.node.textContent).toContain('已采集事件数：0；待上传数：0');
    expect(
      sidebar.node.querySelector('#delete-session-confirmation')
    ).toBeNull();
    resolveRefresh?.({ ...job, status: 'ready' });
    await inFlight;
    await flush();
    expect(sidebar.node.textContent).not.toContain('本次会话结果');
    expect(
      sidebar.node.querySelector('#delete-session-confirmation')
    ).toBeNull();
    const listener = capture.subscribe.mock.calls[0][0];
    listener({
      ...snapshot,
      sessionId: job.session_id,
      uploadState: 'finalized',
      eventCount: 99
    });
    expect(sidebar.node.textContent).toContain('已采集事件数：0；待上传数：0');
    expect(
      sidebar.node.querySelector('#delete-session-confirmation')
    ).toBeNull();
    sidebar.dispose();
  });

  it('does not mutate DOM after dispose when a refresh completes late', async () => {
    let resolveRefresh: ((value: IAnalysisJob) => void) | undefined;
    const capture = createCapture();
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id
    });
    const deps = dependencies(capture, [profile]);
    deps.getAnalysisJob = jest.fn(
      () =>
        new Promise(resolve => {
          resolveRefresh = resolve;
        })
    );
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    (sidebar as unknown as { job: IAnalysisJob }).job = job;
    const operation = sidebar.refreshAnalysis();
    sidebar.dispose();
    const before = sidebar.node.innerHTML;
    resolveRefresh?.({
      ...job,
      status: 'error',
      error_code: 'analysis_worker_failed'
    });
    await operation;
    await flush();
    expect(sidebar.node.innerHTML).toBe(before);
  });

  it('retains session UI for mismatched delete receipts and delete rejection', async () => {
    const capture = createCapture();
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id
    });
    const deps = dependencies(capture, [profile]);
    deps.deleteSession = jest
      .fn()
      .mockResolvedValueOnce({
        schema_version: 1,
        request_id: 'synthetic-mismatch',
        deleted_session_id: '723e4567-e89b-42d3-a456-426614174000'
      })
      .mockRejectedValueOnce(new Error('synthetic delete failure'));
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    (sidebar as unknown as { analysis: IAnalysisResult }).analysis = analysis;
    (
      sidebar as unknown as { analysisProfile: IDimensionProfileVersion }
    ).analysisProfile = profile;
    (sidebar as unknown as { render: () => void }).render();
    const submit = async (): Promise<void> => {
      sidebar.node.querySelector<HTMLInputElement>(
        '#delete-session-confirmation'
      )!.value = job.session_id;
      Array.from(sidebar.node.querySelectorAll<HTMLButtonElement>('button'))
        .find(value => value.textContent === '删除本次会话')
        ?.click();
      await flush();
    };
    await submit();
    expect(sidebar.node.textContent).toContain('删除确认不匹配');
    expect(sidebar.node.textContent).toContain('本次会话结果');
    await submit();
    expect(sidebar.node.textContent).toContain('删除失败');
    expect(sidebar.node.textContent).toContain('本次会话结果');
    sidebar.dispose();
  });

  it('handles a real review form 409 by reloading and preserving the teacher input', async () => {
    const dimension = {
      dimension_code: 'SYNTHETIC',
      decision: {
        status: 'needs_review' as const,
        final_evidence_status: null,
        final_level_code: null,
        display_label: 'synthetic',
        source: 'coverage' as const
      },
      data_quality: {
        missing_required_signals: [],
        observation_opportunities: 0,
        reason_code: null,
        reason: null
      },
      ai_result: null,
      review: { revision: 1, status: 'unreviewed' as const }
    };
    const withDimension = { ...analysis, dimension_results: [dimension] };
    const capture = createCapture();
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id
    });
    const deps = dependencies(capture, [profile]);
    deps.reviewDimension = jest.fn(async () => {
      throw new ApiError(409, 'revision_conflict', 'synthetic', false);
    });
    deps.getSessionAnalysis = jest.fn(async () => withDimension);
    deps.getProfileVersion = jest.fn(async () => profile);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    (sidebar as unknown as { analysis: IAnalysisResult }).analysis =
      withDimension;
    (
      sidebar as unknown as { analysisProfile: IDimensionProfileVersion }
    ).analysisProfile = profile;
    (sidebar as unknown as { render: () => void }).render();
    const textarea = sidebar.node.querySelector<HTMLTextAreaElement>(
      '#review-comment-SYNTHETIC'
    )!;
    textarea.value = '合成冲突复核说明';
    textarea
      .closest('form')!
      .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    await flush();

    expect(deps.reviewDimension).toHaveBeenCalled();
    expect(deps.getSessionAnalysis).toHaveBeenCalledWith(
      settings,
      job.session_id
    );
    expect(sidebar.node.textContent).toContain('复核结果已刷新');
    expect(
      sidebar.node.querySelector<HTMLTextAreaElement>(
        '#review-comment-SYNTHETIC'
      )?.value
    ).toBe('合成冲突复核说明');
    sidebar.dispose();
  });

  it('does not resurrect a deleted session when its deferred retry completes', async () => {
    let active = true;
    const timers = jest.fn(
      (_callback: () => void, _delay?: number) => 1
    ) as unknown as typeof setTimeout;
    const retry = deferred<IAnalysisJob>();
    const capture = createCapture();
    capture.isEnabled.mockImplementation(() => active);
    capture.snapshot.mockImplementation(() => ({
      ...snapshot,
      sessionId: job.session_id,
      uploadState: active ? 'collecting' : 'finalized'
    }));
    capture.stop.mockImplementation(async () => {
      active = false;
      return {
        schema_version: 1,
        request_id: 'synthetic-finalize-a',
        session_id: job.session_id,
        status: 'finalized',
        last_contiguous_sequence: 1,
        analysis_job_id: job.job_id
      };
    });
    const deps = dependencies(capture, [profile]);
    deps.setTimer = timers;
    deps.clearTimer = jest.fn() as unknown as typeof clearTimeout;
    deps.getAnalysisJob = jest.fn(async () => ({
      ...job,
      status: 'error' as const,
      error_code: 'analysis_worker_failed'
    }));
    deps.retryAnalysisJob = jest.fn(() => retry.promise);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    findButton(sidebar, '停止监控').click();
    await flush();
    findButton(sidebar, '刷新状态').click();
    await flush();

    const retryButton = findButton(sidebar, '重试分析');
    retryButton.click();
    retryButton.click();
    expect(deps.retryAnalysisJob).toHaveBeenCalledTimes(1);
    const confirmation = sidebar.node.querySelector<HTMLInputElement>(
      '#delete-session-confirmation'
    )!;
    confirmation.value = job.session_id;
    const remove = findButton(sidebar, '删除本次会话');
    remove.click();
    remove.click();
    expect(deps.deleteSession).toHaveBeenCalledTimes(1);
    await flush();

    retry.resolve({ ...job, status: 'queued', error_code: null });
    await flush();
    expect(sidebar.node.textContent).toContain('分析任务：暂无');
    expect(
      sidebar.node.querySelector('#delete-session-confirmation')
    ).toBeNull();
    expect(timers).toHaveBeenCalledTimes(1);
    sidebar.dispose();
  });

  it('keeps session B authoritative when an exact delete receipt for A arrives late', async () => {
    let active = true;
    let currentSessionId = job.session_id;
    const deleteA = deferred<{
      schema_version: 1;
      request_id: string;
      deleted_session_id: string;
    }>();
    const deleteB = deferred<{
      schema_version: 1;
      request_id: string;
      deleted_session_id: string;
    }>();
    const storage = {
      getItem: jest.fn(),
      setItem: jest.fn(),
      removeItem: jest.fn()
    } as unknown as Storage;
    const capture = createCapture();
    capture.isEnabled.mockImplementation(() => active);
    capture.snapshot.mockImplementation(() => ({
      ...snapshot,
      sessionId: currentSessionId,
      uploadState: active ? 'collecting' : 'finalized'
    }));
    capture.stop.mockImplementation(async () => {
      active = false;
      return {
        schema_version: 1,
        request_id: 'synthetic-finalize-a',
        session_id: job.session_id,
        status: 'finalized',
        last_contiguous_sequence: 1,
        analysis_job_id: job.job_id
      };
    });
    capture.start.mockImplementation(async () => {
      active = true;
      currentSessionId = sessionB;
    });
    const deps = dependencies(capture, [profile]);
    deps.storage = storage;
    deps.getAnalysisJob = jest.fn(async () => ({
      ...job,
      status: 'error' as const,
      error_code: 'analysis_worker_failed'
    }));
    deps.deleteSession = jest
      .fn()
      .mockImplementationOnce(() => deleteA.promise)
      .mockImplementationOnce(() => deleteB.promise);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    findButton(sidebar, '停止监控').click();
    await flush();
    findButton(sidebar, '刷新状态').click();
    await flush();
    sidebar.node.querySelector<HTMLInputElement>(
      '#delete-session-confirmation'
    )!.value = job.session_id;
    findButton(sidebar, '删除本次会话').click();

    selectAndConsent(sidebar);
    findButton(sidebar, '开始监控').click();
    await flush();
    deleteA.resolve({
      schema_version: 1,
      request_id: 'synthetic-delete-a',
      deleted_session_id: job.session_id
    });
    await flush();

    expect(sidebar.node.textContent).toContain('监控状态：进行中');
    expect(storage.removeItem).not.toHaveBeenCalled();
    const confirmation = sidebar.node.querySelector<HTMLInputElement>(
      '#delete-session-confirmation'
    )!;
    confirmation.value = sessionB;
    findButton(sidebar, '删除本次会话').click();
    expect(deps.deleteSession).toHaveBeenLastCalledWith(
      settings,
      sessionB,
      'local_teacher',
      'teacher_requested_deletion'
    );
    sidebar.dispose();
  });

  it('does not let a pending review continuation mutate its form after dispose', async () => {
    let active = true;
    const review = deferred<IAnalysisResult['dimension_results'][number]>();
    const resultA = reviewableAnalysis(job.session_id, job.job_id);
    const capture = createCapture();
    capture.isEnabled.mockImplementation(() => active);
    capture.snapshot.mockReturnValue({
      ...snapshot,
      sessionId: job.session_id
    });
    capture.stop.mockImplementation(async () => {
      active = false;
      return {
        schema_version: 1,
        request_id: 'synthetic-finalize-review-a',
        session_id: job.session_id,
        status: 'finalized',
        last_contiguous_sequence: 1,
        analysis_job_id: job.job_id
      };
    });
    const deps = dependencies(capture, [profile]);
    deps.getAnalysisJob = jest.fn(async () => ({
      ...job,
      status: 'ready' as const
    }));
    deps.getSessionAnalysis = jest.fn(async () => resultA);
    deps.reviewDimension = jest.fn(() => review.promise);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    findButton(sidebar, '停止监控').click();
    await flush();
    findButton(sidebar, '刷新状态').click();
    await flush();
    const form = sidebar.node.querySelector<HTMLFormElement>(
      '.jp-BehaviorAudit-reviewForm'
    )!;
    form.querySelector<HTMLTextAreaElement>('textarea')!.value =
      '合成 dispose 复核';
    form.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true })
    );
    await flush();
    sidebar.dispose();
    const disposedMarkup = form.innerHTML;

    review.resolve(resultA.dimension_results[0]);
    await flush();
    expect(form.innerHTML).toBe(disposedMarkup);
  });

  it('does not let pending review A mutate after start B and does not leak A input into B', async () => {
    let active = true;
    let currentSessionId = job.session_id;
    const review = deferred<IAnalysisResult['dimension_results'][number]>();
    const resultA = reviewableAnalysis(job.session_id, job.job_id, 1);
    const resultB = reviewableAnalysis(sessionB, jobBId, 1);
    const capture = createCapture();
    capture.isEnabled.mockImplementation(() => active);
    capture.snapshot.mockImplementation(() => ({
      ...snapshot,
      sessionId: currentSessionId,
      uploadState: active ? 'collecting' : 'finalized'
    }));
    capture.start.mockImplementation(async () => {
      active = true;
      currentSessionId = sessionB;
    });
    capture.stop.mockImplementation(async () => {
      active = false;
      return {
        schema_version: 1,
        request_id: `synthetic-finalize-${currentSessionId}`,
        session_id: currentSessionId,
        status: 'finalized',
        last_contiguous_sequence: 1,
        analysis_job_id:
          currentSessionId === job.session_id ? job.job_id : jobBId
      };
    });
    const deps = dependencies(capture, [profile]);
    deps.getAnalysisJob = jest.fn(async (_settings, requestedJobId) => ({
      ...job,
      job_id: requestedJobId,
      session_id: requestedJobId === job.job_id ? job.session_id : sessionB,
      status: 'ready' as const
    }));
    deps.getSessionAnalysis = jest.fn(async (_settings, requestedSessionId) =>
      requestedSessionId === job.session_id ? resultA : resultB
    );
    deps.reviewDimension = jest.fn(() => review.promise);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    findButton(sidebar, '停止监控').click();
    await flush();
    findButton(sidebar, '刷新状态').click();
    await flush();
    const formA = sidebar.node.querySelector<HTMLFormElement>(
      '.jp-BehaviorAudit-reviewForm'
    )!;
    formA.querySelector<HTMLTextAreaElement>('textarea')!.value =
      '只属于会话 A';
    formA.querySelector<HTMLInputElement>('input[value="clear"]')!.checked =
      true;
    formA.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true })
    );
    await flush();

    selectAndConsent(sidebar);
    findButton(sidebar, '开始监控').click();
    await flush();
    const detachedMarkup = formA.innerHTML;
    review.resolve(resultA.dimension_results[0]);
    await flush();
    expect(formA.innerHTML).toBe(detachedMarkup);

    findButton(sidebar, '停止监控').click();
    await flush();
    findButton(sidebar, '刷新状态').click();
    await flush();
    const formB = sidebar.node.querySelector<HTMLFormElement>(
      '.jp-BehaviorAudit-reviewForm'
    )!;
    expect(formB.querySelector<HTMLTextAreaElement>('textarea')?.value).toBe(
      ''
    );
    expect(
      formB.querySelector<HTMLInputElement>('input[value="confirm"]')?.checked
    ).toBe(true);
    expect(
      formB.querySelector<HTMLInputElement>('input[value="clear"]')?.checked
    ).toBe(false);
    sidebar.dispose();
  });

  it('requires successful explicit abandonment before starting after unfinished restore', async () => {
    const stored: ISessionState = {
      schema_version: 1,
      request_id: 'synthetic-pending-a',
      session_id: job.session_id,
      problem_id: profile.problem_id,
      profile_id: profile.profile_id,
      profile_version: profile.version,
      profile_content_hash: profile.content_hash,
      status: 'collecting',
      last_contiguous_sequence: 2,
      received_event_count: 2,
      analysis_job_id: null
    };
    let current = { ...snapshot };
    const storage = {
      getItem: jest.fn(),
      setItem: jest.fn(),
      removeItem: jest.fn()
    } as unknown as Storage;
    const capture = createCapture();
    capture.snapshot.mockImplementation(() => current);
    capture.start.mockImplementation(async () => {
      current = {
        ...snapshot,
        sessionId: sessionB,
        uploadState: 'collecting'
      };
    });
    const deps = dependencies(capture, [profile]);
    deps.storage = storage;
    deps.getStoredActiveSession = jest.fn(async () => stored);
    deps.abandonSession = jest.fn(async () => ({
      ...stored,
      status: 'abandoned' as const
    }));
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    selectAndConsent(sidebar);
    const blockedStart = Array.from(
      sidebar.node.querySelectorAll<HTMLButtonElement>('button')
    ).find(value =>
      ['开始监控', '请先放弃未完成会话'].includes(value.textContent ?? '')
    )!;
    blockedStart.click();
    expect(capture.start).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();

    findButton(sidebar, '放弃未完成会话').click();
    await flush();
    expect(deps.abandonSession).toHaveBeenCalledTimes(1);
    expect(storage.removeItem).toHaveBeenCalledWith(
      'myextension:active-session'
    );
    findButton(sidebar, '开始监控').click();
    await flush();
    expect(capture.start).toHaveBeenCalledTimes(1);
    sidebar.dispose();
  });

  it('keeps an unfinished restore when its single-flight abandonment fails', async () => {
    const stored: ISessionState = {
      schema_version: 1,
      request_id: 'synthetic-pending-failure',
      session_id: job.session_id,
      problem_id: profile.problem_id,
      profile_id: profile.profile_id,
      profile_version: profile.version,
      profile_content_hash: profile.content_hash,
      status: 'finalizing',
      last_contiguous_sequence: 2,
      received_event_count: 2,
      analysis_job_id: null
    };
    const abandonment = deferred<ISessionState>();
    const storage = {
      getItem: jest.fn(),
      setItem: jest.fn(),
      removeItem: jest.fn()
    } as unknown as Storage;
    const capture = createCapture();
    const deps = dependencies(capture, [profile]);
    deps.storage = storage;
    deps.getStoredActiveSession = jest.fn(async () => stored);
    deps.abandonSession = jest.fn(() => abandonment.promise);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();

    const abandon = findButton(sidebar, '放弃未完成会话');
    abandon.click();
    abandon.click();
    expect(deps.abandonSession).toHaveBeenCalledTimes(1);
    abandonment.reject(new Error('synthetic abandonment failure'));
    await flush();

    expect(storage.removeItem).not.toHaveBeenCalled();
    expect(sidebar.node.textContent).toContain('放弃未完成会话失败，请重试');
    selectAndConsent(sidebar);
    findButton(sidebar, '请先放弃未完成会话').click();
    expect(capture.start).not.toHaveBeenCalled();
    sidebar.dispose();
  });

  it('requires fresh consent after a successful start and stop cycle', async () => {
    let active = false;
    let currentSessionId: string | null = null;
    const capture = createCapture();
    capture.isEnabled.mockImplementation(() => active);
    capture.snapshot.mockImplementation(() => ({
      ...snapshot,
      sessionId: currentSessionId,
      uploadState: active ? 'collecting' : 'finalized'
    }));
    capture.start.mockImplementation(async () => {
      active = true;
      currentSessionId = sessionB;
    });
    capture.stop.mockImplementation(async () => {
      active = false;
      return {
        schema_version: 1,
        request_id: 'synthetic-consent-stop',
        session_id: sessionB,
        status: 'finalized',
        last_contiguous_sequence: 0,
        analysis_job_id: jobBId
      };
    });
    const sidebar = new BehaviorAnalysisSidebar(
      dependencies(capture, [profile])
    );
    await flush();
    selectAndConsent(sidebar);
    findButton(sidebar, '开始监控').click();
    await flush();
    findButton(sidebar, '停止监控').click();
    await flush();

    const consent = sidebar.node.querySelector<HTMLInputElement>(
      '#behavior-analysis-consent'
    )!;
    expect(consent.checked).toBe(false);
    expect(findButton(sidebar, '开始监控').disabled).toBe(true);
    sidebar.dispose();
  });

  it('clears a real abandoned stored fixture without enabling capture', async () => {
    const abandoned: ISessionState = {
      schema_version: 1,
      request_id: 'synthetic-abandoned',
      session_id: job.session_id,
      problem_id: profile.problem_id,
      profile_id: profile.profile_id,
      profile_version: profile.version,
      profile_content_hash: profile.content_hash,
      status: 'abandoned',
      last_contiguous_sequence: 0,
      received_event_count: 0,
      analysis_job_id: null
    };
    const capture = createCapture();
    const storage = {
      getItem: jest.fn(),
      setItem: jest.fn(),
      removeItem: jest.fn()
    } as unknown as Storage;
    const deps = dependencies(capture, [profile]);
    deps.storage = storage;
    deps.getStoredActiveSession = jest.fn(async () => abandoned);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    expect(capture.start).not.toHaveBeenCalled();
    expect(storage.removeItem).toHaveBeenCalledWith(
      'myextension:active-session'
    );
    expect(sidebar.node.textContent).toContain('不会自动分析');
    sidebar.dispose();
  });

  it.each(['mismatch', 'reject'] as const)(
    'reconciles the live same-session delete control after a public render and %s',
    async outcome => {
      let active = true;
      const pendingDelete = deferred<{
        schema_version: 1;
        request_id: string;
        deleted_session_id: string;
      }>();
      const capture = createCapture();
      capture.isEnabled.mockImplementation(() => active);
      capture.snapshot.mockImplementation(() => ({
        ...snapshot,
        sessionId: job.session_id,
        uploadState: active ? 'collecting' : 'finalized'
      }));
      capture.stop.mockImplementation(async () => {
        active = false;
        return {
          schema_version: 1,
          request_id: 'synthetic-delete-render-finalize',
          session_id: job.session_id,
          status: 'finalized',
          last_contiguous_sequence: 1,
          analysis_job_id: job.job_id
        };
      });
      const deps = dependencies(capture, [profile]);
      deps.getAnalysisJob = jest.fn(async () => ({
        ...job,
        status: 'ready' as const
      }));
      deps.getSessionAnalysis = jest.fn(async () => analysis);
      deps.deleteSession = jest
        .fn()
        .mockImplementationOnce(() => pendingDelete.promise)
        .mockImplementation(async (_settings, sessionId) => ({
          schema_version: 1 as const,
          request_id: 'synthetic-delete-retry',
          deleted_session_id: sessionId
        }));
      const sidebar = new BehaviorAnalysisSidebar(deps);
      await flush();
      await stopAndLoadReadyResult(sidebar);

      const exactInput = sidebar.node.querySelector<HTMLInputElement>(
        '#delete-session-confirmation'
      )!;
      exactInput.value = job.session_id;
      const originalButton = findButton(sidebar, '删除本次会话');
      originalButton.click();
      await sidebar.refreshProfiles();
      const liveBusyButton = findButton(sidebar, '删除本次会话');
      expect(liveBusyButton).not.toBe(originalButton);
      expect(liveBusyButton.disabled).toBe(true);
      expect(liveBusyButton.getAttribute('aria-busy')).toBe('true');

      if (outcome === 'mismatch') {
        pendingDelete.resolve({
          schema_version: 1,
          request_id: 'synthetic-delete-mismatch',
          deleted_session_id: sessionB
        });
      } else {
        pendingDelete.reject(new Error('synthetic delete rejection'));
      }
      await flush();

      const retryButton = findButton(sidebar, '删除本次会话');
      expect(retryButton.disabled).toBe(false);
      expect(retryButton.hasAttribute('aria-busy')).toBe(false);
      expect(sidebar.node.textContent).toContain(
        outcome === 'mismatch' ? '删除确认不匹配' : '删除失败'
      );
      expect(
        sidebar.node.querySelector<HTMLInputElement>(
          '#delete-session-confirmation'
        )?.value
      ).toBe(job.session_id);
      retryButton.click();
      await flush();
      expect(deps.deleteSession).toHaveBeenCalledTimes(2);
      sidebar.dispose();
    }
  );

  it.each(['mismatch', 'reject'] as const)(
    'keeps B live when the pending delete for A settles with %s',
    async outcome => {
      let active = true;
      let currentSessionId = job.session_id;
      const pendingDelete = deferred<{
        schema_version: 1;
        request_id: string;
        deleted_session_id: string;
      }>();
      const storage = {
        getItem: jest.fn(),
        setItem: jest.fn(),
        removeItem: jest.fn()
      } as unknown as Storage;
      const capture = createCapture();
      capture.isEnabled.mockImplementation(() => active);
      capture.snapshot.mockImplementation(() => ({
        ...snapshot,
        sessionId: currentSessionId,
        uploadState: active ? 'collecting' : 'finalized'
      }));
      capture.stop.mockImplementation(async () => {
        active = false;
        return {
          schema_version: 1,
          request_id: 'synthetic-delete-a-finalize',
          session_id: job.session_id,
          status: 'finalized',
          last_contiguous_sequence: 1,
          analysis_job_id: job.job_id
        };
      });
      capture.start.mockImplementation(async () => {
        active = true;
        currentSessionId = sessionB;
      });
      const deps = dependencies(capture, [profile]);
      deps.storage = storage;
      deps.deleteSession = jest
        .fn()
        .mockImplementationOnce(() => pendingDelete.promise)
        .mockImplementation(async (_settings, sessionId) => ({
          schema_version: 1 as const,
          request_id: 'synthetic-delete-b',
          deleted_session_id: sessionId
        }));
      const sidebar = new BehaviorAnalysisSidebar(deps);
      await flush();
      findButton(sidebar, '停止监控').click();
      await flush();
      sidebar.node.querySelector<HTMLInputElement>(
        '#delete-session-confirmation'
      )!.value = job.session_id;
      findButton(sidebar, '删除本次会话').click();

      selectAndConsent(sidebar);
      findButton(sidebar, '开始监控').click();
      await flush();
      if (outcome === 'mismatch') {
        pendingDelete.resolve({
          schema_version: 1,
          request_id: 'synthetic-late-mismatch-a',
          deleted_session_id: '823e4567-e89b-42d3-a456-426614174000'
        });
      } else {
        pendingDelete.reject(new Error('synthetic late rejection A'));
      }
      await flush();

      expect(sidebar.node.textContent).toContain('监控状态：进行中');
      expect(sidebar.node.textContent).not.toContain('删除确认不匹配');
      expect(sidebar.node.textContent).not.toContain('删除失败');
      expect(storage.removeItem).not.toHaveBeenCalled();
      const deleteB = findButton(sidebar, '删除本次会话');
      expect(deleteB.disabled).toBe(false);
      expect(deleteB.hasAttribute('aria-busy')).toBe(false);
      sidebar.node.querySelector<HTMLInputElement>(
        '#delete-session-confirmation'
      )!.value = sessionB;
      deleteB.click();
      await flush();
      expect(deps.deleteSession).toHaveBeenLastCalledWith(
        settings,
        sessionB,
        'local_teacher',
        'teacher_requested_deletion'
      );
      sidebar.dispose();
    }
  );

  it('replaces a live review form with an enabled form after a complete 409 reload', async () => {
    let active = true;
    const before = reviewableAnalysis(job.session_id, job.job_id, 1);
    const after = reviewableAnalysis(job.session_id, job.job_id, 2);
    const capture = createCapture();
    capture.isEnabled.mockImplementation(() => active);
    capture.snapshot.mockImplementation(() => ({
      ...snapshot,
      sessionId: job.session_id,
      uploadState: active ? 'collecting' : 'finalized'
    }));
    capture.stop.mockImplementation(async () => {
      active = false;
      return {
        schema_version: 1,
        request_id: 'synthetic-review-409-finalize',
        session_id: job.session_id,
        status: 'finalized',
        last_contiguous_sequence: 1,
        analysis_job_id: job.job_id
      };
    });
    const deps = dependencies(capture, [profile]);
    deps.getAnalysisJob = jest.fn(async () => ({
      ...job,
      status: 'ready' as const
    }));
    deps.getSessionAnalysis = jest
      .fn()
      .mockResolvedValueOnce(before)
      .mockResolvedValueOnce(after);
    deps.reviewDimension = jest.fn(async () => {
      throw new ApiError(409, 'revision_conflict', 'synthetic', false);
    });
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    await stopAndLoadReadyResult(sidebar);
    const oldForm = sidebar.node.querySelector<HTMLFormElement>(
      '.jp-BehaviorAudit-reviewForm'
    )!;
    oldForm.querySelector<HTMLTextAreaElement>('textarea')!.value =
      '合成成功刷新';
    oldForm.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true })
    );
    await flush();

    const liveForm = sidebar.node.querySelector<HTMLFormElement>(
      '.jp-BehaviorAudit-reviewForm'
    )!;
    const liveSubmit = liveForm.querySelector<HTMLButtonElement>(
      'button[type="submit"]'
    )!;
    expect(liveForm).not.toBe(oldForm);
    expect(liveSubmit.disabled).toBe(false);
    expect(liveSubmit.hasAttribute('aria-busy')).toBe(false);
    expect(sidebar.node.textContent).toContain(
      '复核结果已刷新，请确认后再次提交'
    );
    sidebar.dispose();
  });

  it.each(['analysis', 'profile'] as const)(
    're-enables the live review form when a 409 %s reload fails',
    async failureAt => {
      let active = true;
      const before = reviewableAnalysis(job.session_id, job.job_id, 1);
      const capture = createCapture();
      capture.isEnabled.mockImplementation(() => active);
      capture.snapshot.mockImplementation(() => ({
        ...snapshot,
        sessionId: job.session_id,
        uploadState: active ? 'collecting' : 'finalized'
      }));
      capture.stop.mockImplementation(async () => {
        active = false;
        return {
          schema_version: 1,
          request_id: 'synthetic-review-409-failure-finalize',
          session_id: job.session_id,
          status: 'finalized',
          last_contiguous_sequence: 1,
          analysis_job_id: job.job_id
        };
      });
      const deps = dependencies(capture, [profile]);
      deps.getAnalysisJob = jest.fn(async () => ({
        ...job,
        status: 'ready' as const
      }));
      deps.getSessionAnalysis =
        failureAt === 'analysis'
          ? jest
              .fn()
              .mockResolvedValueOnce(before)
              .mockRejectedValueOnce(
                new Error('synthetic analysis reload failure')
              )
          : jest
              .fn()
              .mockResolvedValueOnce(before)
              .mockResolvedValueOnce({ ...before });
      deps.getProfileVersion =
        failureAt === 'profile'
          ? jest
              .fn()
              .mockResolvedValueOnce(profile)
              .mockRejectedValueOnce(
                new Error('synthetic profile reload failure')
              )
          : jest.fn(async () => profile);
      deps.reviewDimension = jest.fn(async () => {
        throw new ApiError(409, 'revision_conflict', 'synthetic', false);
      });
      const sidebar = new BehaviorAnalysisSidebar(deps);
      await flush();
      await stopAndLoadReadyResult(sidebar);
      const form = sidebar.node.querySelector<HTMLFormElement>(
        '.jp-BehaviorAudit-reviewForm'
      )!;
      form.querySelector<HTMLTextAreaElement>('textarea')!.value =
        `合成 ${failureAt} 失败保留`;
      form.querySelector<HTMLInputElement>('input[value="clear"]')!.checked =
        true;
      form.dispatchEvent(
        new Event('submit', { bubbles: true, cancelable: true })
      );
      await flush();

      const liveForm = sidebar.node.querySelector<HTMLFormElement>(
        '.jp-BehaviorAudit-reviewForm'
      )!;
      const liveSubmit = liveForm.querySelector<HTMLButtonElement>(
        'button[type="submit"]'
      )!;
      expect(liveForm).toBe(form);
      expect(liveSubmit.disabled).toBe(false);
      expect(liveSubmit.hasAttribute('aria-busy')).toBe(false);
      expect(
        liveForm.querySelector<HTMLTextAreaElement>('textarea')?.value
      ).toBe(`合成 ${failureAt} 失败保留`);
      expect(
        liveForm.querySelector<HTMLInputElement>('input[value="clear"]')
          ?.checked
      ).toBe(true);
      expect(liveForm.textContent).toContain('复核提交失败，请重试');
      expect(sidebar.node.textContent).not.toContain('复核结果已刷新');
      sidebar.dispose();
    }
  );

  it('does not mutate a disposed review form when its request rejects', async () => {
    let active = true;
    const review = deferred<IAnalysisResult['dimension_results'][number]>();
    const resultA = reviewableAnalysis(job.session_id, job.job_id);
    const capture = createCapture();
    capture.isEnabled.mockImplementation(() => active);
    capture.snapshot.mockImplementation(() => ({
      ...snapshot,
      sessionId: job.session_id,
      uploadState: active ? 'collecting' : 'finalized'
    }));
    capture.stop.mockImplementation(async () => {
      active = false;
      return {
        schema_version: 1,
        request_id: 'synthetic-review-reject-dispose-finalize',
        session_id: job.session_id,
        status: 'finalized',
        last_contiguous_sequence: 1,
        analysis_job_id: job.job_id
      };
    });
    const deps = dependencies(capture, [profile]);
    deps.getAnalysisJob = jest.fn(async () => ({
      ...job,
      status: 'ready' as const
    }));
    deps.getSessionAnalysis = jest.fn(async () => resultA);
    deps.reviewDimension = jest.fn(() => review.promise);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    await stopAndLoadReadyResult(sidebar);
    const form = sidebar.node.querySelector<HTMLFormElement>(
      '.jp-BehaviorAudit-reviewForm'
    )!;
    form.querySelector<HTMLTextAreaElement>('textarea')!.value =
      '合成 dispose reject';
    form.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true })
    );
    await flush();
    sidebar.dispose();
    const disposedMarkup = form.innerHTML;

    review.reject(new Error('synthetic review rejection'));
    await flush();
    expect(form.innerHTML).toBe(disposedMarkup);
  });

  it('does not mutate review A after start B when A rejects', async () => {
    let active = true;
    let currentSessionId = job.session_id;
    const review = deferred<IAnalysisResult['dimension_results'][number]>();
    const resultA = reviewableAnalysis(job.session_id, job.job_id);
    const capture = createCapture();
    capture.isEnabled.mockImplementation(() => active);
    capture.snapshot.mockImplementation(() => ({
      ...snapshot,
      sessionId: currentSessionId,
      uploadState: active ? 'collecting' : 'finalized'
    }));
    capture.stop.mockImplementation(async () => {
      active = false;
      return {
        schema_version: 1,
        request_id: 'synthetic-review-reject-a-finalize',
        session_id: job.session_id,
        status: 'finalized',
        last_contiguous_sequence: 1,
        analysis_job_id: job.job_id
      };
    });
    capture.start.mockImplementation(async () => {
      active = true;
      currentSessionId = sessionB;
    });
    const deps = dependencies(capture, [profile]);
    deps.getAnalysisJob = jest.fn(async () => ({
      ...job,
      status: 'ready' as const
    }));
    deps.getSessionAnalysis = jest.fn(async () => resultA);
    deps.reviewDimension = jest.fn(() => review.promise);
    const sidebar = new BehaviorAnalysisSidebar(deps);
    await flush();
    await stopAndLoadReadyResult(sidebar);
    const formA = sidebar.node.querySelector<HTMLFormElement>(
      '.jp-BehaviorAudit-reviewForm'
    )!;
    formA.querySelector<HTMLTextAreaElement>('textarea')!.value =
      '合成 A reject';
    formA.dispatchEvent(
      new Event('submit', { bubbles: true, cancelable: true })
    );
    await flush();
    selectAndConsent(sidebar);
    findButton(sidebar, '开始监控').click();
    await flush();
    const detachedMarkup = formA.innerHTML;

    review.reject(new Error('synthetic late A review rejection'));
    await flush();
    expect(formA.innerHTML).toBe(detachedMarkup);
    expect(sidebar.node.textContent).toContain('监控状态：进行中');
    expect(sidebar.node.textContent).not.toContain('复核提交失败');
    sidebar.dispose();
  });
});
