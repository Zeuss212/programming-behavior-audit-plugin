import {
  IAssessmentProfileDraftInput,
  IAssessmentTest,
  IKnowledgePointSuggestion,
  IProblemContext,
  SubmissionContract
} from '../models/assessmentPlan';
import { sha256Json } from '../utils/canonicalJson';
import { CLEAR_DEFINITION, POSSIBLE_DEFINITION } from './guidedProfileForm';

export interface IAssessmentKnowledgePointEditor {
  id: string;
  name: string;
  description: string;
  source: 'teacher' | 'ai_suggestion';
  order: number;
  evidenceQuestion: string;
  supportStatement: string;
  exclusionStatement: string;
  dimensionCode?: string;
}

export interface IAssessmentPlanState {
  title: string;
  problemId: string;
  problemStatement: string;
  submissionContract: SubmissionContract;
  teacherFocus: string[];
  knowledgePoints: IAssessmentKnowledgePointEditor[];
  assessmentTests: IAssessmentTest[];
  confirmations: {
    knowledge_points_hash: string | null;
    tests_hash: string | null;
  };
}

export type AssessmentPlanErrors = Partial<
  Record<
    | 'title'
    | 'problemId'
    | 'problemStatement'
    | 'entrypoint'
    | 'knowledgePoints'
    | 'assessmentTests',
    string
  >
>;

type IdFactory = () => string;

function generatedId(prefix: 'KP' | 'TEST'): string {
  const bytes = new Uint8Array(4);
  globalThis.crypto.getRandomValues(bytes);
  const suffix = Array.from(bytes, value => value.toString(16).padStart(2, '0'))
    .join('')
    .toUpperCase();
  return `${prefix}_${suffix}`;
}

export function assessmentProblemContext(
  state: IAssessmentPlanState
): IProblemContext {
  return {
    statement: state.problemStatement.trim(),
    language: 'python',
    submission_contract:
      state.submissionContract.kind === 'function'
        ? {
            kind: 'function',
            entrypoint: state.submissionContract.entrypoint.trim()
          }
        : { kind: 'stdin_stdout' }
  };
}

function reindexPoints(
  points: IAssessmentKnowledgePointEditor[]
): IAssessmentKnowledgePointEditor[] {
  return points.map((point, order) => ({ ...point, order }));
}

function reindexTests(tests: IAssessmentTest[]): IAssessmentTest[] {
  return tests.map((test, order) => ({ ...test, order }));
}

function assessmentTestsForProfile(
  state: IAssessmentPlanState
): IAssessmentTest[] {
  return state.assessmentTests.map(test => ({
    ...test,
    name: test.name.trim(),
    knowledge_point_ids: [...test.knowledge_point_ids]
  }));
}

function invalidateKnowledge(
  state: IAssessmentPlanState
): IAssessmentPlanState {
  return {
    ...state,
    confirmations: {
      knowledge_points_hash: null,
      tests_hash: null
    }
  };
}

function invalidateTests(state: IAssessmentPlanState): IAssessmentPlanState {
  return {
    ...state,
    confirmations: {
      ...state.confirmations,
      tests_hash: null
    }
  };
}

function defaultEvidence(name: string): {
  evidenceQuestion: string;
  supportStatement: string;
  exclusionStatement: string;
} {
  return {
    evidenceQuestion: `学生是否通过代码、运行和修改过程正确应用“${name}”？`,
    supportStatement: `代码与验证过程显示学生正确应用了“${name}”。`,
    exclusionStatement: `只出现一次偶然正确输出，或缺少与“${name}”相关的验证，不计入。`
  };
}

export type KnowledgePointRequiredField =
  | 'name'
  | 'evidenceQuestion'
  | 'supportStatement'
  | 'exclusionStatement';

export const KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS: Record<
  KnowledgePointRequiredField,
  string
> = {
  name: '知识点名称',
  evidenceQuestion: '过程观察问题',
  supportStatement: '支持表现',
  exclusionStatement: '排除情况'
};

export function missingKnowledgePointFields(
  point: IAssessmentKnowledgePointEditor
): KnowledgePointRequiredField[] {
  return (
    Object.keys(
      KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS
    ) as KnowledgePointRequiredField[]
  ).filter(field => !point[field].trim());
}

function normalizedSuggestionText(value: unknown, fallback: string): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback;
}

export function createAssessmentPlanState(): IAssessmentPlanState {
  return {
    title: '',
    problemId: '',
    problemStatement: '',
    submissionContract: { kind: 'function', entrypoint: '' },
    teacherFocus: [],
    knowledgePoints: [],
    assessmentTests: [],
    confirmations: {
      knowledge_points_hash: null,
      tests_hash: null
    }
  };
}

export function updateAssessmentPlanContext(
  state: IAssessmentPlanState,
  changes: Partial<
    Pick<
      IAssessmentPlanState,
      'title' | 'problemId' | 'problemStatement' | 'submissionContract'
    >
  >
): IAssessmentPlanState {
  const nextContract =
    changes.submissionContract === undefined
      ? state.submissionContract
      : { ...changes.submissionContract };
  const next = {
    ...state,
    ...changes,
    submissionContract: nextContract
  };
  if (JSON.stringify(next) === JSON.stringify(state)) {
    return state;
  }
  const submissionContractChanged =
    JSON.stringify(nextContract) !== JSON.stringify(state.submissionContract);
  const confirmationContentChanged =
    next.problemStatement !== state.problemStatement ||
    submissionContractChanged;
  const answerKindChanged =
    submissionContractChanged &&
    nextContract.kind !== state.submissionContract.kind;
  const changedState = {
    ...next,
    assessmentTests: answerKindChanged ? [] : state.assessmentTests
  };
  return confirmationContentChanged
    ? invalidateKnowledge(changedState)
    : changedState;
}

export function addTeacherKnowledgePoint(
  state: IAssessmentPlanState,
  input: { name: string; description: string },
  idFactory: IdFactory = () => generatedId('KP')
): IAssessmentPlanState {
  const name = input.name.trim();
  const description = input.description.trim();
  if (
    !name ||
    state.knowledgePoints.length >= 10 ||
    state.knowledgePoints.some(
      point =>
        point.name.trim().toLocaleLowerCase() === name.toLocaleLowerCase()
    )
  ) {
    return state;
  }
  const point: IAssessmentKnowledgePointEditor = {
    id: idFactory(),
    name,
    description,
    source: 'teacher',
    order: state.knowledgePoints.length,
    ...defaultEvidence(name)
  };
  return invalidateKnowledge({
    ...state,
    knowledgePoints: [...state.knowledgePoints, point]
  });
}

export function mergeKnowledgeSuggestions(
  state: IAssessmentPlanState,
  suggestions: IKnowledgePointSuggestion[]
): IAssessmentPlanState {
  const usedNames = new Set(
    state.knowledgePoints.map(point => point.name.trim().toLocaleLowerCase())
  );
  const usedIds = new Set(state.knowledgePoints.map(point => point.id));
  const added: IAssessmentKnowledgePointEditor[] = [];
  for (const suggestion of suggestions) {
    const name = suggestion.name.trim();
    const normalizedName = name.toLocaleLowerCase();
    if (
      !name ||
      usedNames.has(normalizedName) ||
      usedIds.has(suggestion.id) ||
      state.knowledgePoints.length + added.length >= 10
    ) {
      continue;
    }
    usedNames.add(normalizedName);
    usedIds.add(suggestion.id);
    const evidence = defaultEvidence(name);
    added.push({
      id: suggestion.id,
      name,
      description: suggestion.description.trim(),
      source: 'ai_suggestion',
      order: 0,
      evidenceQuestion: normalizedSuggestionText(
        suggestion.evidence_question,
        evidence.evidenceQuestion
      ),
      supportStatement: normalizedSuggestionText(
        suggestion.support_statement,
        evidence.supportStatement
      ),
      exclusionStatement: normalizedSuggestionText(
        suggestion.exclusion_statement,
        evidence.exclusionStatement
      )
    });
  }
  if (added.length === 0) return state;
  return invalidateKnowledge({
    ...state,
    knowledgePoints: reindexPoints([...state.knowledgePoints, ...added])
  });
}

export function updateKnowledgePoint(
  state: IAssessmentPlanState,
  id: string,
  changes: Partial<
    Pick<
      IAssessmentKnowledgePointEditor,
      | 'name'
      | 'description'
      | 'evidenceQuestion'
      | 'supportStatement'
      | 'exclusionStatement'
    >
  >
): IAssessmentPlanState {
  let changed = false;
  const knowledgePoints = state.knowledgePoints.map(point => {
    if (point.id !== id) return point;
    const next = {
      ...point,
      ...changes,
      name: changes.name === undefined ? point.name : changes.name.trim(),
      description:
        changes.description === undefined
          ? point.description
          : changes.description.trim(),
      source: 'teacher' as const
    };
    changed = JSON.stringify(next) !== JSON.stringify(point);
    return next;
  });
  return changed ? invalidateKnowledge({ ...state, knowledgePoints }) : state;
}

export function removeKnowledgePoint(
  state: IAssessmentPlanState,
  id: string
): IAssessmentPlanState {
  if (!state.knowledgePoints.some(point => point.id === id)) return state;
  const tests = state.assessmentTests
    .map(test => ({
      ...test,
      knowledge_point_ids: test.knowledge_point_ids.filter(
        pointId => pointId !== id
      )
    }))
    .filter(test => test.knowledge_point_ids.length > 0);
  return invalidateKnowledge({
    ...state,
    knowledgePoints: reindexPoints(
      state.knowledgePoints.filter(point => point.id !== id)
    ),
    assessmentTests: reindexTests(tests)
  });
}

export function moveKnowledgePoint(
  state: IAssessmentPlanState,
  id: string,
  direction: -1 | 1
): IAssessmentPlanState {
  const index = state.knowledgePoints.findIndex(point => point.id === id);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= state.knowledgePoints.length) {
    return state;
  }
  const points = [...state.knowledgePoints];
  [points[index], points[target]] = [points[target], points[index]];
  return invalidateKnowledge({
    ...state,
    knowledgePoints: reindexPoints(points)
  });
}

export function replaceAssessmentTests(
  state: IAssessmentPlanState,
  tests: IAssessmentTest[]
): IAssessmentPlanState {
  return invalidateTests({
    ...state,
    assessmentTests: reindexTests(
      tests.map(test => ({
        ...test,
        knowledge_point_ids: [...test.knowledge_point_ids]
      }))
    )
  });
}

export function mergeAssessmentTestSuggestions(
  state: IAssessmentPlanState,
  suggestions: IAssessmentTest[]
): IAssessmentPlanState {
  const teacherTests = state.assessmentTests.filter(
    test => test.source === 'teacher'
  );
  const usedIds = new Set(teacherTests.map(test => test.id));
  const usedNames = new Set(
    teacherTests.map(test => test.name.trim().toLocaleLowerCase())
  );
  const accepted: IAssessmentTest[] = [];
  for (const suggestion of suggestions) {
    const name = suggestion.name.trim();
    const normalizedName = name.toLocaleLowerCase();
    if (
      !name ||
      usedIds.has(suggestion.id) ||
      usedNames.has(normalizedName) ||
      teacherTests.length + accepted.length >= 30
    ) {
      continue;
    }
    usedIds.add(suggestion.id);
    usedNames.add(normalizedName);
    accepted.push({
      ...suggestion,
      name,
      knowledge_point_ids: [...suggestion.knowledge_point_ids],
      source: 'ai_suggestion',
      order: 0
    });
  }
  return replaceAssessmentTests(state, [...teacherTests, ...accepted]);
}

export function invalidateAssessmentTestConfirmation(
  state: IAssessmentPlanState
): IAssessmentPlanState {
  return invalidateTests(state);
}

export function addTeacherAssessmentTest(
  state: IAssessmentPlanState,
  input: Omit<IAssessmentTest, 'id' | 'source' | 'order'>,
  idFactory: IdFactory = () => generatedId('TEST')
): IAssessmentPlanState {
  if (state.assessmentTests.length >= 30) return state;
  return replaceAssessmentTests(state, [
    ...state.assessmentTests,
    {
      ...input,
      id: idFactory(),
      source: 'teacher',
      order: state.assessmentTests.length
    }
  ]);
}

export function updateAssessmentTest(
  state: IAssessmentPlanState,
  id: string,
  changes: Partial<
    Pick<
      IAssessmentTest,
      'name' | 'knowledge_point_ids' | 'input' | 'expected' | 'enabled'
    >
  >
): IAssessmentPlanState {
  let changed = false;
  const assessmentTests = state.assessmentTests.map(test => {
    if (test.id !== id) return test;
    const next = {
      ...test,
      ...changes,
      name: changes.name === undefined ? test.name : changes.name.trim(),
      knowledge_point_ids:
        changes.knowledge_point_ids === undefined
          ? test.knowledge_point_ids
          : [...changes.knowledge_point_ids],
      source: 'teacher' as const
    };
    changed = JSON.stringify(next) !== JSON.stringify(test);
    return next;
  });
  return changed ? invalidateTests({ ...state, assessmentTests }) : state;
}

export function removeAssessmentTest(
  state: IAssessmentPlanState,
  id: string
): IAssessmentPlanState {
  if (!state.assessmentTests.some(test => test.id === id)) return state;
  return replaceAssessmentTests(
    state,
    state.assessmentTests.filter(test => test.id !== id)
  );
}

export function moveAssessmentTest(
  state: IAssessmentPlanState,
  id: string,
  direction: -1 | 1
): IAssessmentPlanState {
  const index = state.assessmentTests.findIndex(test => test.id === id);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= state.assessmentTests.length) {
    return state;
  }
  const tests = [...state.assessmentTests];
  [tests[index], tests[target]] = [tests[target], tests[index]];
  return replaceAssessmentTests(state, tests);
}

export function validateAssessmentPlanState(
  state: IAssessmentPlanState
): AssessmentPlanErrors {
  const errors: AssessmentPlanErrors = {};
  if (!state.title.trim()) errors.title = '请输入方案名称';
  if (!state.problemId.trim()) errors.problemId = '请输入题目标识';
  if (!state.problemStatement.trim()) {
    errors.problemStatement = '请输入完整题目';
  }
  if (
    state.submissionContract.kind === 'function' &&
    !state.submissionContract.entrypoint.trim()
  ) {
    errors.entrypoint = '请输入学生需要实现的函数名';
  }
  const incompletePointIndex = state.knowledgePoints.findIndex(
    point => missingKnowledgePointFields(point).length > 0
  );
  if (state.knowledgePoints.length === 0) {
    errors.knowledgePoints = '请至少确认一个知识点';
  } else if (incompletePointIndex >= 0) {
    const labels = missingKnowledgePointFields(
      state.knowledgePoints[incompletePointIndex]
    ).map(field => KNOWLEDGE_POINT_REQUIRED_FIELD_LABELS[field]);
    errors.knowledgePoints = `知识点 ${incompletePointIndex + 1} 缺少：${labels.join('、')}`;
  }
  if (
    state.assessmentTests.some(
      test => !test.name.trim() || test.knowledge_point_ids.length === 0
    )
  ) {
    errors.assessmentTests = '请补全每个测试的名称和对应知识点';
  } else {
    const knownPoints = new Set(state.knowledgePoints.map(point => point.id));
    if (
      state.assessmentTests.some(test =>
        test.knowledge_point_ids.some(id => !knownPoints.has(id))
      )
    ) {
      errors.assessmentTests = '测试引用了不存在的知识点';
      return errors;
    }
    const expectedKind =
      state.submissionContract.kind === 'function'
        ? 'function_call'
        : 'stdin_stdout';
    if (state.assessmentTests.some(test => test.kind !== expectedKind)) {
      errors.assessmentTests = '测试形式与题目答题形式不一致';
    }
  }
  return errors;
}

async function currentKnowledgeHash(
  state: IAssessmentPlanState,
  subtle: SubtleCrypto
): Promise<string> {
  return sha256Json(
    {
      problem_context: assessmentProblemContext(state),
      knowledge_points: state.knowledgePoints.map(point => ({
        id: point.id,
        name: point.name.trim(),
        description: point.description.trim(),
        source: point.source,
        order: point.order
      }))
    },
    subtle
  );
}

export async function confirmKnowledgePoints(
  state: IAssessmentPlanState,
  subtle: SubtleCrypto = globalThis.crypto.subtle
): Promise<IAssessmentPlanState> {
  const { assessmentTests: _assessmentTests, ...blockingErrors } =
    validateAssessmentPlanState(state);
  if (Object.keys(blockingErrors).length > 0) {
    throw new Error('请先完成题目和知识点');
  }
  const knowledgeHash = await currentKnowledgeHash(state, subtle);
  return {
    ...state,
    confirmations: {
      knowledge_points_hash: knowledgeHash,
      tests_hash: null
    }
  };
}

function ensureTestCoverage(state: IAssessmentPlanState): void {
  const known = new Set(state.knowledgePoints.map(point => point.id));
  const covered = new Set<string>();
  for (const test of state.assessmentTests) {
    if (
      test.knowledge_point_ids.length === 0 ||
      test.knowledge_point_ids.some(id => !known.has(id))
    ) {
      throw new Error('测试引用了不存在的知识点');
    }
    if (!test.enabled) continue;
    test.knowledge_point_ids.forEach(id => covered.add(id));
  }
  if (
    known.size === 0 ||
    covered.size !== known.size ||
    [...known].some(id => !covered.has(id))
  ) {
    throw new Error('每个知识点至少需要一个启用的测试');
  }
}

export async function confirmAssessmentTests(
  state: IAssessmentPlanState,
  subtle: SubtleCrypto = globalThis.crypto.subtle
): Promise<IAssessmentPlanState> {
  const knowledgeHash = await currentKnowledgeHash(state, subtle);
  if (state.confirmations.knowledge_points_hash !== knowledgeHash) {
    throw new Error('请先重新确认知识点');
  }
  if (validateAssessmentPlanState(state).assessmentTests) {
    throw new Error('请先补全并修正测试');
  }
  ensureTestCoverage(state);
  const testsHash = await sha256Json(
    {
      problem_context: assessmentProblemContext(state),
      knowledge_points_hash: knowledgeHash,
      assessment_tests: assessmentTestsForProfile(state)
    },
    subtle
  );
  return {
    ...state,
    confirmations: {
      knowledge_points_hash: knowledgeHash,
      tests_hash: testsHash
    }
  };
}

export function canPublishAssessmentPlan(state: IAssessmentPlanState): boolean {
  if (Object.keys(validateAssessmentPlanState(state)).length > 0) {
    return false;
  }
  try {
    ensureTestCoverage(state);
  } catch {
    return false;
  }
  return (
    state.confirmations.knowledge_points_hash !== null &&
    state.confirmations.tests_hash !== null
  );
}

export function buildAssessmentProfileDraft(
  state: IAssessmentPlanState
): IAssessmentProfileDraftInput {
  const context = assessmentProblemContext(state);
  return {
    schema_version: 2,
    problem_id: state.problemId.trim(),
    title: state.title.trim(),
    problem_context: context,
    knowledge_points: state.knowledgePoints.map(point => ({
      id: point.id,
      name: point.name.trim(),
      description: point.description.trim(),
      source: point.source,
      order: point.order
    })),
    assessment_tests: assessmentTestsForProfile(state),
    confirmations: { ...state.confirmations },
    dimensions: state.knowledgePoints.map(point => ({
      ...(point.dimensionCode ? { code: point.dimensionCode } : {}),
      knowledge_point_id: point.id,
      name: `知识点：${point.name.trim()}`.slice(0, 50),
      question: point.evidenceQuestion.trim(),
      evidence_criteria: [
        {
          id: `${point.id}-support`,
          direction: 'support',
          statement: point.supportStatement.trim()
        },
        {
          id: `${point.id}-exclude`,
          direction: 'exclude',
          statement: point.exclusionStatement.trim()
        }
      ],
      levels: [
        {
          code: 'possible',
          name: '可能出现',
          definition: POSSIBLE_DEFINITION
        },
        {
          code: 'clear',
          name: '明显出现',
          definition: CLEAR_DEFINITION
        }
      ],
      analysis_config: {
        mode: 'llm_evidence',
        minimum_observation: {
          valid_observation_duration_ms: 30000,
          edit_event_count: 1
        }
      }
    }))
  };
}
