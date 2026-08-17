import { capabilitiesForMode } from '../platform/studentCapabilities';

describe('classroom platform student capabilities', () => {
  it('removes all plan and AI authoring powers in student mode', () => {
    expect(capabilitiesForMode('student')).toEqual({
      canAuthorPlan: false,
      canPublishPlan: false,
      canConfigureAi: false,
      canUseAssessmentAssist: false,
      canCapture: true,
      canSubmit: true
    });
  });

  it('fails closed for an unrecognised runtime mode', () => {
    expect(() => capabilitiesForMode('unknown' as 'student')).toThrow(
      'Unsupported platform mode'
    );
  });
});
