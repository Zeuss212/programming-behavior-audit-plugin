import type { IDimensionDefinition, IDimensionInput } from './dimensionProfile';

export type SubmissionContract =
  | { kind: 'function'; entrypoint: string }
  | { kind: 'stdin_stdout' };

export interface IProblemContext {
  statement: string;
  language: 'python';
  submission_contract: SubmissionContract;
}

export type AssessmentAuthoringSource = 'teacher' | 'ai_suggestion';

export interface IKnowledgePoint {
  id: string;
  name: string;
  description: string;
  source: AssessmentAuthoringSource;
  order: number;
}

export interface IKnowledgePointSuggestion extends IKnowledgePoint {
  evidence_question: string;
  support_statement: string;
  exclusion_statement: string;
}

export type AssessmentTestKind = 'function_call' | 'stdin_stdout';

export interface IAssessmentTest {
  id: string;
  name: string;
  knowledge_point_ids: string[];
  kind: AssessmentTestKind;
  input: string;
  expected: string;
  enabled: boolean;
  source: AssessmentAuthoringSource;
  order: number;
}

export interface IAssessmentConfirmations {
  knowledge_points_hash: string | null;
  tests_hash: string | null;
}

export interface IAssessmentDimensionDefinition extends IDimensionDefinition {
  knowledge_point_id: string;
}

export interface IAssessmentDimensionInput extends IDimensionInput {
  knowledge_point_id: string;
}

export interface IAssessmentProfileDraftInput {
  schema_version: 2;
  problem_id: string;
  title: string;
  problem_context: IProblemContext;
  knowledge_points: IKnowledgePoint[];
  assessment_tests: IAssessmentTest[];
  confirmations: IAssessmentConfirmations;
  dimensions: IAssessmentDimensionInput[];
}

export interface IAssessmentProfileDraft extends Omit<
  IAssessmentProfileDraftInput,
  'dimensions'
> {
  profile_id: string;
  revision: number;
  dimensions: IAssessmentDimensionDefinition[];
}

export interface IAssessmentProfileVersion extends Omit<
  IAssessmentProfileDraft,
  'revision'
> {
  version: number;
  content_hash: string;
  deployment_status: 'pilot';
  preview_status: 'pending_real_samples' | 'completed';
}

export interface IKnowledgeRecommendationResponse {
  schema_version: 1;
  request_id: string;
  knowledge_points: IKnowledgePointSuggestion[];
}

export interface IAssessmentTestGenerationResponse {
  schema_version: 1;
  request_id: string;
  assessment_tests: IAssessmentTest[];
}
