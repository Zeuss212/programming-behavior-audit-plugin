import { ServerConnection } from '@jupyterlab/services';

import { IKnowledgePoint, IProblemContext } from '../models/assessmentPlan';
import { requestAPI } from '../request';
import {
  generateAssessmentTests,
  recommendKnowledgePoints
} from '../services/assessmentPlanApi';

jest.mock('../request', () => ({
  requestAPI: jest.fn()
}));

const settings = {} as ServerConnection.ISettings;
const problemContext: IProblemContext = {
  statement: '编写 calculate_average(numbers)，返回列表平均值。',
  language: 'python',
  submission_contract: {
    kind: 'function',
    entrypoint: 'calculate_average'
  }
};
const knowledgePoints: IKnowledgePoint[] = [
  {
    id: 'KP_A1B2C3D4',
    name: '循环边界',
    description: '正确遍历列表。',
    source: 'teacher',
    order: 0
  }
];
const mockedRequest = requestAPI as jest.MockedFunction<typeof requestAPI>;

beforeEach(() => {
  mockedRequest.mockReset();
  mockedRequest.mockResolvedValue({} as never);
});

function jsonInit(body: unknown): RequestInit {
  return {
    method: 'POST',
    body: JSON.stringify(body),
    headers: { 'Content-Type': 'application/json' }
  };
}

it('requests knowledge suggestions with only teacher-authored context', async () => {
  await recommendKnowledgePoints(settings, problemContext, ['循环']);

  expect(mockedRequest).toHaveBeenCalledWith(
    'assessment-assist/knowledge-points',
    settings,
    jsonInit({
      schema_version: 1,
      problem_context: problemContext,
      teacher_focus: ['循环']
    })
  );
});

it('requests tests without student events, paths, identities or keys', async () => {
  await generateAssessmentTests(settings, problemContext, knowledgePoints);

  expect(mockedRequest).toHaveBeenCalledWith(
    'assessment-assist/tests',
    settings,
    jsonInit({
      schema_version: 1,
      problem_context: problemContext,
      knowledge_points: [
        {
          id: 'KP_A1B2C3D4',
          name: '循环边界',
          description: '正确遍历列表。'
        }
      ]
    })
  );
});
