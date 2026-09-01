import { ServerConnection } from '@jupyterlab/services';

import { IBehaviorCaptureController } from '../behaviorCapture';
import {
  IPlatformContext,
  LOCAL_PLATFORM_CONTEXT
} from '../platform/contextApi';
import {
  BehaviorAnalysisSidebar,
  sidebarDependencies
} from '../ui/behaviorAnalysisSidebar';

jest.mock('@jupyterlab/ui-components', () => ({
  inspectorIcon: { name: 'ui-components:inspector' }
}));

const settings = {} as ServerConnection.ISettings;
const capture = {
  logger: {},
  isEnabled: jest.fn(() => false),
  snapshot: jest.fn(() => ({
    sessionId: null,
    uploadState: 'idle' as const,
    eventCount: 0,
    queuedCount: 0,
    lastSequence: 0,
    lastServerSequence: 0,
    validObservationDurationMs: 0,
    pageAwayDurationMs: 0,
    observationAnchorAt: null
  })),
  subscribe: jest.fn(() => () => undefined),
  start: jest.fn(),
  resume: jest.fn(),
  stop: jest.fn()
} as unknown as IBehaviorCaptureController;

beforeEach(() => {
  jest.clearAllMocks();
});

const studentContext: IPlatformContext = {
  ...LOCAL_PLATFORM_CONTEXT,
  mode: 'student',
  capabilities: {
    canAuthorPlan: false,
    canPublishPlan: false,
    canConfigureAi: false,
    canUseAssessmentAssist: false,
    canCapture: true,
    canSubmit: true
  },
  classroom_session: {
    assignment_id: 'd7647a1a-89c3-4c6d-9b5f-7e803918aa9d',
    plan_id: '2b16b5c0-4e58-48f9-9448-9067de005e4a',
    plan_version: 1,
    session_id: '23d7d803-524a-4d9f-b8bd-152a540dba12',
    profile: {
      schema_version: 2,
      profile_id: '5c0a7494-7f0e-41c3-a7a2-0c1bc19ed7b3',
      version: 1,
      problem_id: 'average-debug',
      title: '平均分知识点分析',
      problem_context: {
        statement: '实现平均值函数。',
        language: 'python',
        submission_contract: {
          kind: 'function',
          entrypoint: 'calculate_average'
        }
      },
      knowledge_points: [
        {
          id: 'KP_B2C3D4E5',
          name: '边界条件处理',
          description: '处理空输入与除零。',
          source: 'teacher',
          order: 1
        },
        {
          id: 'KP_A1B2C3D4',
          name: '平均值计算',
          description: '使用总和除以元素数量。',
          source: 'teacher',
          order: 0
        }
      ],
      assessment_tests: [],
      confirmations: { knowledge_points_hash: null, tests_hash: null },
      dimensions: [],
      content_hash: 'a'.repeat(64),
      deployment_status: 'pilot',
      preview_status: 'pending_real_samples'
    },
    scheduled_end_at: '2026-08-12T08:30:00Z',
    evidence_cutoff_at: '2026-08-12T08:45:00Z',
    last_sync_at: '2026-08-12T08:00:00Z'
  }
};

function createStudentSidebar(
  context: IPlatformContext = studentContext,
  overrides: object = {}
): BehaviorAnalysisSidebar {
  const dependencies = Object.assign(
    sidebarDependencies(
      settings,
      capture,
      {
        openProfileEditor: jest.fn(),
        openDataFile: jest.fn(),
        confirmClearAIKey: jest.fn(),
        getStoredActiveSession: jest.fn(),
        openLogFolder: jest.fn(),
        openSessionLog: jest.fn(),
        downloadSessionLog: jest.fn()
      },
      context
    ),
    overrides
  );
  return new BehaviorAnalysisSidebar(dependencies);
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

async function flushPromises(): Promise<void> {
  for (let index = 0; index < 16; index += 1) {
    await Promise.resolve();
  }
  await new Promise(resolve => setTimeout(resolve, 0));
}

it('renders published knowledge points in teacher order', () => {
  const sidebar = createStudentSidebar();
  const text = sidebar.node.textContent ?? '';

  expect(text).toContain('本次实验知识点');
  expect(text.indexOf('平均值计算')).toBeLessThan(text.indexOf('边界条件处理'));
  expect(text).toContain('使用总和除以元素数量。');
  expect(text).not.toContain('创建题目考核方案');
  sidebar.dispose();
});

it('keeps the latest snapshot when a refresh fails', async () => {
  const refreshPlatformContext = jest.fn(() =>
    Promise.reject(new Error('offline'))
  );
  const sidebar = createStudentSidebar(studentContext, {
    refreshPlatformContext
  });

  findButton(sidebar, '刷新课堂信息').click();
  await flushPromises();

  expect(refreshPlatformContext).toHaveBeenCalledWith(settings);
  expect(sidebar.node.textContent).toContain('平均值计算');
  expect(sidebar.node.textContent).toContain('知识点暂时无法加载，请重试');
  expect(findButton(sidebar, '提交本节简报')).toBeDefined();
  sidebar.dispose();
});

it('does not leave student mode when refresh returns a local context', async () => {
  const refreshPlatformContext = jest.fn(async () => LOCAL_PLATFORM_CONTEXT);
  const sidebar = createStudentSidebar(studentContext, {
    refreshPlatformContext
  });

  findButton(sidebar, '刷新课堂信息').click();
  await flushPromises();

  expect(sidebar.node.textContent).toContain('平均值计算');
  expect(sidebar.node.textContent).toContain('知识点暂时无法加载，请重试');
  expect(sidebar.node.textContent).not.toContain('创建题目考核方案');
  sidebar.dispose();
});

it('shows a neutral error for a malformed knowledge-point snapshot', () => {
  const malformedContext = {
    ...studentContext,
    classroom_session: {
      ...studentContext.classroom_session!,
      profile: {
        ...studentContext.classroom_session!.profile,
        knowledge_points: [{ name: '缺少必须字段' }]
      }
    }
  } as unknown as IPlatformContext;
  const sidebar = createStudentSidebar(malformedContext);

  expect(sidebar.node.textContent).toContain('知识点暂时无法加载，请重试');
  sidebar.dispose();
});

it('renders only the classroom task card in student mode and never loads teacher tools', () => {
  const dependencies = sidebarDependencies(
    settings,
    capture,
    {
      openProfileEditor: jest.fn(),
      openDataFile: jest.fn(),
      confirmClearAIKey: jest.fn(),
      getStoredActiveSession: jest.fn(),
      openLogFolder: jest.fn(),
      openSessionLog: jest.fn(),
      downloadSessionLog: jest.fn()
    },
    studentContext
  );
  const sidebar = new BehaviorAnalysisSidebar(dependencies);

  expect(sidebar.node.textContent).toContain('平均分知识点分析');
  expect(sidebar.node.textContent).toContain('最近同步：2026-08-12T08:00:00Z');
  expect(sidebar.node.textContent).toContain('提交本节简报');
  expect(sidebar.node.textContent).not.toContain('创建题目考核方案');
  expect(sidebar.node.textContent).not.toContain('AI 服务配置');
  expect(sidebar.node.textContent).not.toContain('题目与分析方案');
  expect(capture.subscribe).toHaveBeenCalledTimes(1);
  sidebar.dispose();
});

it('ends active monitoring and shows the submitted brief result after a student submits', async () => {
  const stop = jest.fn(async () => ({
    schema_version: 1 as const,
    request_id: 'finalize-request',
    session_id: studentContext.classroom_session!.session_id,
    status: 'finalized' as const,
    last_contiguous_sequence: 12,
    analysis_job_id: 'e046c012-bff4-4e3e-9764-f7cdf7782dd1'
  }));
  const activeCapture = {
    ...capture,
    isEnabled: jest.fn(() => true),
    stop
  } as unknown as IBehaviorCaptureController;
  const submitClassroomBrief = jest.fn(async () => ({
    session_id: studentContext.classroom_session!.session_id,
    status: 'submitted' as const,
    reason: 'student_manual' as const,
    brief_id: '84521d4e-b27d-42b7-a8dd-4beabc0ab3f5',
    revision: 1,
    remote_status: 'completed'
  }));
  const dependencies = Object.assign(
    sidebarDependencies(
      settings,
      activeCapture,
      {
        openProfileEditor: jest.fn(),
        openDataFile: jest.fn(),
        confirmClearAIKey: jest.fn(),
        getStoredActiveSession: jest.fn(),
        openLogFolder: jest.fn(),
        openSessionLog: jest.fn(),
        downloadSessionLog: jest.fn()
      },
      studentContext
    ),
    { submitClassroomBrief }
  );
  const sidebar = new BehaviorAnalysisSidebar(dependencies);
  const submit = Array.from(
    sidebar.node.querySelectorAll<HTMLButtonElement>('button')
  ).find(value => value.textContent === '提交本节简报');

  expect(submit).toBeDefined();
  expect(submit?.disabled).toBe(false);
  submit!.click();
  for (let index = 0; index < 10; index += 1) await Promise.resolve();

  expect(stop).toHaveBeenCalledTimes(1);
  expect(submitClassroomBrief).toHaveBeenCalledWith(
    settings,
    studentContext.classroom_session!.session_id
  );
  expect(sidebar.node.textContent).toContain(
    '本节简报已提交，老师可查看课堂结果。'
  );
  sidebar.dispose();
});

it('does not submit when local capture finalizes a different classroom session', async () => {
  const stop = jest.fn(async () => ({
    schema_version: 1 as const,
    request_id: 'wrong-finalize-request',
    session_id: '8c385f81-cb48-4c36-b0e4-c8633f70ca58',
    status: 'finalized' as const,
    last_contiguous_sequence: 12,
    analysis_job_id: 'e046c012-bff4-4e3e-9764-f7cdf7782dd1'
  }));
  const activeCapture = {
    ...capture,
    isEnabled: jest.fn(() => true),
    stop
  } as unknown as IBehaviorCaptureController;
  const submitClassroomBrief = jest.fn();
  const dependencies = Object.assign(
    sidebarDependencies(
      settings,
      activeCapture,
      {
        openProfileEditor: jest.fn(),
        openDataFile: jest.fn(),
        confirmClearAIKey: jest.fn(),
        getStoredActiveSession: jest.fn(),
        openLogFolder: jest.fn(),
        openSessionLog: jest.fn(),
        downloadSessionLog: jest.fn()
      },
      studentContext
    ),
    { submitClassroomBrief }
  );
  const sidebar = new BehaviorAnalysisSidebar(dependencies);
  const submit = Array.from(
    sidebar.node.querySelectorAll<HTMLButtonElement>('button')
  ).find(value => value.textContent === '提交本节简报');

  submit!.click();
  for (let index = 0; index < 10; index += 1) await Promise.resolve();

  expect(stop).toHaveBeenCalledTimes(1);
  expect(submitClassroomBrief).not.toHaveBeenCalled();
  expect(sidebar.node.textContent).toContain(
    '提交未完成，本地行为记录仍会保留。请稍后重试。'
  );
  sidebar.dispose();
});
