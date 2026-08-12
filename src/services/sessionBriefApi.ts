import { ServerConnection } from '@jupyterlab/services';

import { requestAPI } from '../request';

export interface IClassroomBrief {
  schema_version: 1;
  request_id: string;
  session_id: string;
  status: 'complete' | 'partial';
  data_completeness: 'complete' | 'partial';
  active_duration_ms: number;
  run_summary: string;
  process_highlights: string[];
  attention_message: string | null;
  generated_at: string;
}

export async function getClassroomBrief(
  settings: ServerConnection.ISettings,
  sessionId: string
): Promise<IClassroomBrief> {
  const value = await requestAPI<unknown>(
    `sessions/${encodeURIComponent(sessionId)}/brief`,
    settings
  );
  if (!isClassroomBrief(value) || value.session_id !== sessionId) {
    throw new Error('本地课堂简报响应无效。');
  }
  return value;
}

function isClassroomBrief(value: unknown): value is IClassroomBrief {
  if (typeof value !== 'object' || value === null) return false;
  const candidate = value as Record<string, unknown>;
  const status = candidate.status;
  const completeness = candidate.data_completeness;
  const highlights = candidate.process_highlights;
  return (
    candidate.schema_version === 1 &&
    typeof candidate.request_id === 'string' &&
    candidate.request_id.length > 0 &&
    typeof candidate.session_id === 'string' &&
    candidate.session_id.length > 0 &&
    (status === 'complete' || status === 'partial') &&
    (completeness === 'complete' || completeness === 'partial') &&
    status === completeness &&
    Number.isSafeInteger(candidate.active_duration_ms) &&
    (candidate.active_duration_ms as number) >= 0 &&
    typeof candidate.run_summary === 'string' &&
    candidate.run_summary.length > 0 &&
    Array.isArray(highlights) &&
    highlights.length <= 3 &&
    highlights.every(
      item => typeof item === 'string' && item.length > 0 && item.length <= 200
    ) &&
    (candidate.attention_message === null ||
      typeof candidate.attention_message === 'string') &&
    typeof candidate.generated_at === 'string' &&
    candidate.generated_at.length > 0
  );
}
