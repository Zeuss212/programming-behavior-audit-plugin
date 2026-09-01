import { ServerConnection } from '@jupyterlab/services';

import type {
  IAssessmentProfileVersion,
  IKnowledgePoint
} from '../models/assessmentPlan';
import { IDimensionProfileVersion } from '../models/dimensionProfile';
import { ISessionState } from '../models/session';
import { requestAPI } from '../request';
import {
  capabilitiesForMode,
  PlatformCapabilities,
  PlatformMode
} from './studentCapabilities';

export interface IClassroomPlatformSession {
  assignment_id: string;
  plan_id: string;
  plan_version: number;
  session_id: string;
  profile: IDimensionProfileVersion;
  scheduled_end_at: string;
  evidence_cutoff_at: string;
  last_sync_at: string;
}

export interface IPlatformContext {
  schema_version: 1;
  request_id: string;
  mode: PlatformMode;
  capabilities: PlatformCapabilities;
  classroom_session: IClassroomPlatformSession | null;
}

export interface IValidatedClassroomPlatformSession extends Omit<
  IClassroomPlatformSession,
  'profile'
> {
  profile: IAssessmentProfileVersion;
}

export interface IValidatedStudentPlatformContext extends IPlatformContext {
  mode: 'student';
  classroom_session: IValidatedClassroomPlatformSession | null;
}

export type IPlatformCaptureSession = Omit<
  ISessionState,
  'schema_version' | 'request_id'
>;

export interface IPlatformCaptureBootstrapResponse {
  schema_version: 1;
  request_id: string;
  outcome: 'created' | 'resumed' | 'terminal';
  assignment_id: string;
  plan_id: string;
  plan_version: number;
  session: IPlatformCaptureSession | null;
}

export const LOCAL_PLATFORM_CONTEXT: IPlatformContext = {
  schema_version: 1,
  request_id: 'local',
  mode: 'local',
  capabilities: capabilitiesForMode('local'),
  classroom_session: null
};

export function createUnavailableStudentPlatformContext(): IPlatformContext {
  return {
    schema_version: 1,
    request_id: 'classroom-context-unavailable',
    mode: 'student',
    capabilities: capabilitiesForMode('student'),
    classroom_session: null
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isNonEmptyString(value: unknown, maximum: number): value is string {
  return (
    typeof value === 'string' &&
    value.trim().length > 0 &&
    value.length <= maximum
  );
}

function isIntegerInRange(
  value: unknown,
  minimum: number,
  maximum: number
): value is number {
  return (
    typeof value === 'number' &&
    Number.isSafeInteger(value) &&
    value >= minimum &&
    value <= maximum
  );
}

function hasStudentCapabilities(value: unknown): value is PlatformCapabilities {
  if (!isRecord(value)) return false;
  const expected = capabilitiesForMode('student');
  return Object.entries(expected).every(
    ([name, allowed]) => value[name] === allowed
  );
}

function isValidKnowledgePoint(value: unknown): value is IKnowledgePoint {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === 'string' &&
    /^KP_[A-Z0-9]{8}$/.test(value.id) &&
    isNonEmptyString(value.name, 80) &&
    typeof value.description === 'string' &&
    value.description.length <= 500 &&
    (value.source === 'teacher' || value.source === 'ai_suggestion') &&
    isIntegerInRange(value.order, 0, 9)
  );
}

function hasValidKnowledgePoints(value: unknown): value is IKnowledgePoint[] {
  if (
    !Array.isArray(value) ||
    value.length > 10 ||
    !value.every(isValidKnowledgePoint)
  ) {
    return false;
  }
  const ids = new Set(value.map(point => point.id));
  const orders = [...value]
    .map(point => point.order)
    .sort((left, right) => left - right);
  return (
    ids.size === value.length &&
    new Set(orders).size === value.length &&
    orders.every((order, index) => order === index)
  );
}

function isValidClassroomSession(
  value: unknown
): value is IValidatedClassroomPlatformSession {
  if (!isRecord(value) || !isRecord(value.profile)) return false;
  const profile = value.profile;
  return (
    isNonEmptyString(value.assignment_id, 200) &&
    isNonEmptyString(value.plan_id, 200) &&
    isIntegerInRange(value.plan_version, 1, Number.MAX_SAFE_INTEGER) &&
    isNonEmptyString(value.session_id, 200) &&
    profile.schema_version === 2 &&
    isNonEmptyString(profile.title, 200) &&
    hasValidKnowledgePoints(profile.knowledge_points) &&
    isNonEmptyString(value.scheduled_end_at, 200) &&
    isNonEmptyString(value.evidence_cutoff_at, 200) &&
    isNonEmptyString(value.last_sync_at, 200)
  );
}

/**
 * Treat the HTTP response as untrusted before it determines classroom UI
 * capabilities or replaces a previously displayed classroom snapshot.
 */
export function isValidStudentPlatformContext(
  value: unknown
): value is IValidatedStudentPlatformContext {
  if (!isRecord(value)) return false;
  return (
    value.schema_version === 1 &&
    isNonEmptyString(value.request_id, 200) &&
    value.mode === 'student' &&
    hasStudentCapabilities(value.capabilities) &&
    (value.classroom_session === null ||
      isValidClassroomSession(value.classroom_session))
  );
}

export function hasPublishedStudentClassroomSnapshot(
  value: unknown
): value is IValidatedStudentPlatformContext & {
  classroom_session: IValidatedClassroomPlatformSession;
} {
  return (
    isValidStudentPlatformContext(value) &&
    value.classroom_session !== null &&
    value.classroom_session.profile.knowledge_points.length > 0
  );
}

export function getPlatformContext(
  settings: ServerConnection.ISettings
): Promise<IPlatformContext> {
  return requestAPI<IPlatformContext>('platform/context', settings);
}

export function refreshPlatformContext(
  settings: ServerConnection.ISettings
): Promise<IPlatformContext> {
  return requestAPI<IPlatformContext>('platform/context', settings, {
    method: 'POST',
    body: '',
    headers: { 'Content-Type': 'application/json' }
  });
}

export function bootstrapPlatformCapture(
  settings: ServerConnection.ISettings
): Promise<IPlatformCaptureBootstrapResponse> {
  return requestAPI<IPlatformCaptureBootstrapResponse>(
    'platform/capture/bootstrap',
    settings,
    {
      method: 'POST',
      body: '',
      headers: { 'Content-Type': 'application/json' }
    }
  );
}
