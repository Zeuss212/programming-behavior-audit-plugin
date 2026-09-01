import {
  IPlatformContext,
  LOCAL_PLATFORM_CONTEXT
} from '../platform/contextApi';
import { initializeClassroomUi } from '../platform/classroomUiBootstrap';

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

it('initializes the server context when it is available', async () => {
  const initialize = jest.fn();

  await initializeClassroomUi({
    classroomTicketObserved: true,
    getContext: async () => studentContext,
    initialize,
    reportUnavailable: jest.fn()
  });

  expect(initialize).toHaveBeenCalledWith(studentContext);
});

it('uses a no-authoring student context when classroom context is unavailable', async () => {
  const initialize = jest.fn();

  await initializeClassroomUi({
    classroomTicketObserved: true,
    getContext: async () => Promise.reject(new Error('offline')),
    initialize,
    reportUnavailable: jest.fn()
  });

  expect(initialize.mock.calls[0][0]).toMatchObject({
    mode: 'student',
    classroom_session: null,
    capabilities: { canAuthorPlan: false, canPublishPlan: false }
  });
});

it('does not invent a student UI for a non-classroom context failure', async () => {
  const initialize = jest.fn();
  const reportUnavailable = jest.fn();

  await initializeClassroomUi({
    classroomTicketObserved: false,
    getContext: async () => Promise.reject(new Error('offline')),
    initialize,
    reportUnavailable
  });

  expect(initialize).not.toHaveBeenCalled();
  expect(reportUnavailable).toHaveBeenCalledTimes(1);
});
