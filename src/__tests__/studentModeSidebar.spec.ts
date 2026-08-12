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
      knowledge_points: [],
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
  expect(capture.subscribe).not.toHaveBeenCalled();
  sidebar.dispose();
});
