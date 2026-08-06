import type {
  IAssessmentProfileDraft,
  IAssessmentProfileDraftInput,
  IAssessmentProfileVersion
} from './assessmentPlan';

export type EvidenceDirection = 'support' | 'exclude';
export type GuidedLevelCode = 'possible' | 'clear';

export interface IEvidenceCriterion {
  id: string;
  direction: EvidenceDirection;
  statement: string;
}

export interface IDimensionLevel {
  code: GuidedLevelCode;
  name: string;
  definition: string;
}

export interface ITeachingActions {
  possible: string;
  clear: string;
  not_observed?: string;
}

export interface IDimensionDefinition {
  code: string;
  name: string;
  question: string;
  no_known_exclusion?: boolean;
  evidence_criteria: IEvidenceCriterion[];
  levels: IDimensionLevel[];
  teaching_actions?: ITeachingActions;
  analysis_config: {
    mode: 'llm_evidence';
    minimum_observation: Record<string, number>;
  };
}

export type IDimensionInput = Omit<IDimensionDefinition, 'code'> & {
  code?: string;
};

export interface IDimensionTemplate extends Omit<
  IDimensionDefinition,
  'teaching_actions'
> {
  teaching_actions: ITeachingActions;
  template_id: string;
  version: 1;
  deployment_status: 'pilot';
  examples: Array<{
    kind: 'positive' | 'negative';
    summary: string;
  }>;
}

export interface IBehaviorProfileDraft {
  schema_version: 1;
  profile_id: string;
  problem_id: string;
  title: string;
  revision: number;
  dimensions: IDimensionDefinition[];
}

export interface IBehaviorProfileVersion extends Omit<
  IBehaviorProfileDraft,
  'revision'
> {
  version: number;
  content_hash: string;
  deployment_status: 'pilot';
  preview_status: 'pending_real_samples' | 'completed';
}

export interface IProfileReference {
  problem_id: string;
  profile_id: string;
  profile_version: number;
  profile_content_hash: string;
}

export interface IBehaviorProfileDraftInput {
  schema_version: 1;
  problem_id: string;
  title: string;
  dimensions: IDimensionInput[];
}

export type IProfileDraftInput =
  | IBehaviorProfileDraftInput
  | IAssessmentProfileDraftInput;

export type IDimensionProfileDraft =
  | IBehaviorProfileDraft
  | IAssessmentProfileDraft;

export type IDimensionProfileVersion =
  | IBehaviorProfileVersion
  | IAssessmentProfileVersion;
