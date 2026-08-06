import { ServerConnection } from '@jupyterlab/services';

import { IAnalysisJob } from '../models/session';
import {
  IAnalysisResult,
  IDimensionResult,
  IReviewPayload
} from '../models/analysisResult';
import { requestAPI } from '../request';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export function getAnalysisJob(
  settings: ServerConnection.ISettings,
  jobId: string
): Promise<IAnalysisJob> {
  return requestAPI<IAnalysisJob>(
    `analysis-jobs/${encodeURIComponent(jobId)}`,
    settings
  );
}

export function retryAnalysisJob(
  settings: ServerConnection.ISettings,
  jobId: string,
  reason: string
): Promise<IAnalysisJob> {
  return requestAPI<IAnalysisJob>(
    `analysis-jobs/${encodeURIComponent(jobId)}/retry`,
    settings,
    {
      method: 'POST',
      body: JSON.stringify({ reason }),
      headers: JSON_HEADERS
    }
  );
}

export function getSessionAnalysis(
  settings: ServerConnection.ISettings,
  sessionId: string
): Promise<IAnalysisResult> {
  return requestAPI<IAnalysisResult>(
    `sessions/${encodeURIComponent(sessionId)}/analysis`,
    settings
  );
}

export function reviewDimension(
  settings: ServerConnection.ISettings,
  sessionId: string,
  dimensionCode: string,
  payload: IReviewPayload
): Promise<IDimensionResult> {
  return requestAPI<IDimensionResult>(
    `sessions/${encodeURIComponent(sessionId)}/analysis/${encodeURIComponent(
      dimensionCode
    )}/review`,
    settings,
    {
      method: 'PATCH',
      body: JSON.stringify(payload),
      headers: JSON_HEADERS
    }
  );
}
