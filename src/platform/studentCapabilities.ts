export type PlatformMode = 'local' | 'student';

export interface PlatformCapabilities {
  canAuthorPlan: boolean;
  canPublishPlan: boolean;
  canConfigureAi: boolean;
  canUseAssessmentAssist: boolean;
  canCapture: boolean;
  canSubmit: boolean;
}

const LOCAL_CAPABILITIES: PlatformCapabilities = {
  canAuthorPlan: true,
  canPublishPlan: true,
  canConfigureAi: true,
  canUseAssessmentAssist: true,
  canCapture: true,
  canSubmit: true
};

const STUDENT_CAPABILITIES: PlatformCapabilities = {
  canAuthorPlan: false,
  canPublishPlan: false,
  canConfigureAi: false,
  canUseAssessmentAssist: false,
  canCapture: true,
  canSubmit: true
};

export function capabilitiesForMode(mode: PlatformMode): PlatformCapabilities {
  if (mode === 'local') {
    return { ...LOCAL_CAPABILITIES };
  }
  if (mode === 'student') {
    return { ...STUDENT_CAPABILITIES };
  }
  throw new Error(`Unsupported platform mode: ${String(mode)}`);
}
