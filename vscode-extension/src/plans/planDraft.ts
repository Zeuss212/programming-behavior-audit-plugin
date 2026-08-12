import type { PlanSuggestion } from '../ai/aiClient';
import type { PublishPlanInput } from '../domain/types';

export interface PlanDraftKnowledgePoint {
  readonly localId: string;
  readonly name: string;
  readonly description: string;
  readonly observationBasis: string;
  readonly needsReview: boolean;
}

export interface PlanDraftTest {
  readonly localId: string;
  readonly title: string;
  readonly description: string;
  readonly expectedBehavior: string;
}

export interface PlanDraft {
  readonly schemaVersion: 1;
  readonly currentStep: 1 | 2 | 3;
  readonly problemText: string;
  readonly knowledgePoints: readonly PlanDraftKnowledgePoint[];
  readonly tests: readonly PlanDraftTest[];
  readonly updatedAt: string;
}

export type DraftValidation =
  | { readonly ok: true }
  | { readonly ok: false; readonly field: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isStep(value: unknown): value is 1 | 2 | 3 {
  return value === 1 || value === 2 || value === 3;
}

function isKnowledgePoint(value: unknown): value is PlanDraftKnowledgePoint {
  return (
    isRecord(value) &&
    typeof value.localId === 'string' &&
    typeof value.name === 'string' &&
    typeof value.description === 'string' &&
    typeof value.observationBasis === 'string' &&
    typeof value.needsReview === 'boolean'
  );
}

function isTest(value: unknown): value is PlanDraftTest {
  return (
    isRecord(value) &&
    typeof value.localId === 'string' &&
    typeof value.title === 'string' &&
    typeof value.description === 'string' &&
    typeof value.expectedBehavior === 'string'
  );
}

export function emptyPlanDraft(updatedAt: string): PlanDraft {
  return {
    schemaVersion: 1,
    currentStep: 1,
    problemText: '',
    knowledgePoints: [],
    tests: [],
    updatedAt,
  };
}

export function parsePlanDraft(value: unknown): PlanDraft | undefined {
  if (
    !isRecord(value) ||
    value.schemaVersion !== 1 ||
    !isStep(value.currentStep) ||
    typeof value.problemText !== 'string' ||
    !Array.isArray(value.knowledgePoints) ||
    !value.knowledgePoints.every(isKnowledgePoint) ||
    !Array.isArray(value.tests) ||
    !value.tests.every(isTest) ||
    typeof value.updatedAt !== 'string'
  ) {
    return undefined;
  }
  return {
    schemaVersion: 1,
    currentStep: value.currentStep,
    problemText: value.problemText.slice(0, 20_000),
    knowledgePoints: value.knowledgePoints.slice(0, 20),
    tests: value.tests.slice(0, 50),
    updatedAt: value.updatedAt,
  };
}

export function applySuggestion(
  draft: PlanDraft,
  suggestion: PlanSuggestion,
  updatedAt: string,
): PlanDraft {
  return {
    ...draft,
    currentStep: 2,
    knowledgePoints: suggestion.knowledge_points.map((item, index) => ({
      localId: `kp-${String(index + 1)}`,
      name: item.name,
      description: item.description,
      observationBasis: item.observation_basis,
      needsReview: false,
    })),
    tests: suggestion.tests.map((item, index) => ({
      localId: `test-${String(index + 1)}`,
      title: item.title,
      description: item.description,
      expectedBehavior: item.expected_behavior,
    })),
    updatedAt,
  };
}

export function validateDraftForStep(draft: PlanDraft, targetStep: 1 | 2 | 3): DraftValidation {
  if (targetStep >= 2 && draft.problemText.trim().length === 0) {
    return { ok: false, field: 'problemText' };
  }
  if (targetStep >= 3) {
    if (draft.knowledgePoints.length === 0) {
      return { ok: false, field: 'knowledgePoints' };
    }
    for (const [index, item] of draft.knowledgePoints.entries()) {
      if (item.name.trim().length === 0) {
        return { ok: false, field: `knowledgePoints.${String(index)}.name` };
      }
      if (item.description.trim().length === 0) {
        return { ok: false, field: `knowledgePoints.${String(index)}.description` };
      }
      if (item.observationBasis.trim().length === 0) {
        return { ok: false, field: `knowledgePoints.${String(index)}.observationBasis` };
      }
    }
  }
  return { ok: true };
}

export function toPublishPlanInput(draft: PlanDraft): PublishPlanInput {
  const validation = validateDraftForStep(draft, 3);
  if (!validation.ok) {
    throw new Error(`Draft field is incomplete: ${validation.field}`);
  }
  return {
    problem_text: draft.problemText.trim(),
    knowledge_points: draft.knowledgePoints.map((item, index) => ({
      knowledge_point_id: `kp-${String(index + 1)}`,
      name: item.name.trim(),
      description: item.description.trim(),
      observation_basis: item.observationBasis.trim(),
    })),
    tests: draft.tests.map((item, index) => ({
      test_id: `test-${String(index + 1)}`,
      title: item.title.trim(),
      description: item.description.trim(),
      expected_behavior: item.expectedBehavior.trim(),
    })),
  };
}
