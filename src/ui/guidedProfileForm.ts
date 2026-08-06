import { IDimensionInput } from '../models/dimensionProfile';

export interface IGuidedDimensionForm {
  name: string;
  question: string;
  supportStatements: string[];
  exclusionStatements: string[];
  noKnownExclusion: boolean;
  possibleDefinition: string;
  clearDefinition: string;
  possibleAction: string;
  clearAction: string;
}

export const POSSIBLE_DEFINITION = '存在相关行为证据，但范围或持续性有限';
export const CLEAR_DEFINITION = '在多个有效阶段持续出现相关行为';

const SYSTEM_MINIMUM_OBSERVATION = {
  valid_observation_duration_ms: 30000,
  edit_event_count: 1
};

export function validateGuidedDimension(
  value: IGuidedDimensionForm
): Record<string, string> {
  const errors: Record<string, string> = {};
  if (value.name.trim().length === 0) {
    errors.name = '请输入维度名称';
  } else if (value.name.trim().length > 50) {
    errors.name = '维度名称不能超过 50 个字符';
  }
  if (value.question.trim().length === 0) {
    errors.question = '请输入希望观察的教学问题';
  } else if (value.question.trim().length > 200) {
    errors.question = '教学问题不能超过 200 个字符';
  }
  if (!value.supportStatements.some(statement => statement.trim().length > 0)) {
    errors.supportStatements = '请至少填写一条符合表现';
  }
  if (
    !value.noKnownExclusion &&
    !value.exclusionStatements.some(statement => statement.trim().length > 0)
  ) {
    errors.exclusionStatements = '请选择排除情况，或确认暂无已知排除情况';
  }
  const hasPossibleAction = value.possibleAction.trim().length > 0;
  const hasClearAction = value.clearAction.trim().length > 0;
  if (hasPossibleAction !== hasClearAction) {
    errors.teachingActions = '教学建议请同时填写，或全部留空';
  }
  return errors;
}

export function buildGuidedDimension(
  form: IGuidedDimensionForm,
  generatedCode?: string
): IDimensionInput {
  const hasTeachingActions =
    form.possibleAction.trim().length > 0 && form.clearAction.trim().length > 0;
  const evidenceCriteria: IDimensionInput['evidence_criteria'] = [
    {
      id: 'support-1',
      direction: 'support',
      statement: form.supportStatements[0].trim()
    }
  ];
  if (!form.noKnownExclusion) {
    evidenceCriteria.push({
      id: 'exclude-1',
      direction: 'exclude',
      statement: form.exclusionStatements[0].trim()
    });
  }
  return {
    ...(generatedCode ? { code: generatedCode } : {}),
    name: form.name.trim(),
    question: form.question.trim(),
    ...(form.noKnownExclusion ? { no_known_exclusion: true } : {}),
    evidence_criteria: evidenceCriteria,
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
    ...(hasTeachingActions
      ? {
          teaching_actions: {
            possible: form.possibleAction.trim(),
            clear: form.clearAction.trim()
          }
        }
      : {}),
    analysis_config: {
      mode: 'llm_evidence',
      minimum_observation: { ...SYSTEM_MINIMUM_OBSERVATION }
    }
  };
}
