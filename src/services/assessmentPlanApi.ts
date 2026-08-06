import { ServerConnection } from '@jupyterlab/services';

import {
  IAssessmentTestGenerationResponse,
  IKnowledgePoint,
  IKnowledgeRecommendationResponse,
  IProblemContext
} from '../models/assessmentPlan';
import { requestAPI } from '../request';

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export function recommendKnowledgePoints(
  settings: ServerConnection.ISettings,
  problemContext: IProblemContext,
  teacherFocus: string[] = []
): Promise<IKnowledgeRecommendationResponse> {
  return requestAPI<IKnowledgeRecommendationResponse>(
    'assessment-assist/knowledge-points',
    settings,
    {
      method: 'POST',
      body: JSON.stringify({
        schema_version: 1,
        problem_context: problemContext,
        teacher_focus: teacherFocus
      }),
      headers: JSON_HEADERS
    }
  );
}

export function generateAssessmentTests(
  settings: ServerConnection.ISettings,
  problemContext: IProblemContext,
  knowledgePoints: IKnowledgePoint[]
): Promise<IAssessmentTestGenerationResponse> {
  return requestAPI<IAssessmentTestGenerationResponse>(
    'assessment-assist/tests',
    settings,
    {
      method: 'POST',
      body: JSON.stringify({
        schema_version: 1,
        problem_context: problemContext,
        knowledge_points: knowledgePoints.map(point => ({
          id: point.id,
          name: point.name,
          description: point.description
        }))
      }),
      headers: JSON_HEADERS
    }
  );
}
