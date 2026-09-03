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
  }
};

it('mounts a constrained student UI when a ticketed classroom context is temporarily unavailable', async () => {
  const initialize = jest.fn();

  const result = await initializeClassroomUi({
    classroomTicketObserved: true,
    getContext: async () => Promise.reject(new Error('offline')),
    initialize,
    reportUnavailable: jest.fn()
  });

  expect(result).toBe('student-unavailable');
  expect(initialize).toHaveBeenCalledWith(
    expect.objectContaining({
      mode: 'student',
      classroom_session: null,
      capabilities: expect.objectContaining({
        canAuthorPlan: false,
        canPublishPlan: false,
        canCapture: true
      })
    })
  );
});

it('keeps a successfully resolved student context intact', async () => {
  const initialize = jest.fn();

  await initializeClassroomUi({
    classroomTicketObserved: true,
    getContext: async () => studentContext,
    initialize,
    reportUnavailable: jest.fn()
  });

  expect(initialize).toHaveBeenCalledWith(studentContext);
});

it('does not invent student mode when no classroom ticket was observed', async () => {
  const initialize = jest.fn();
  const reportUnavailable = jest.fn();

  const result = await initializeClassroomUi({
    classroomTicketObserved: false,
    getContext: async () => Promise.reject(new Error('offline')),
    initialize,
    reportUnavailable
  });

  expect(result).toBe('unavailable');
  expect(initialize).not.toHaveBeenCalled();
  expect(reportUnavailable).toHaveBeenCalledTimes(1);
});
