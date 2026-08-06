export type EvidenceStatus =
  | 'observed'
  | 'not_observed'
  | 'insufficient_evidence'
  | 'not_computable';

export interface IEvidenceClaim {
  event_id: string;
  criterion_id: string;
  direction: 'support' | 'exclude';
  claim: string;
  occurred_at?: string;
  event_type?: string;
}

export interface IDimensionResult {
  schema_version?: 1;
  request_id?: string;
  dimension_code: string;
  decision: {
    status: 'resolved' | 'needs_review' | 'partial' | 'failed';
    final_evidence_status: EvidenceStatus | null;
    final_level_code: 'possible' | 'clear' | null;
    display_label: string;
    source: 'llm_evidence' | 'coverage';
  };
  data_quality?: {
    missing_required_signals: string[];
    observation_opportunities: number;
    reason_code: string | null;
    reason: string | null;
    status?: string;
    missing_optional_signals?: string[];
  };
  ai_result?: {
    confidence: number;
    evidence_claims: IEvidenceClaim[];
    explanation: string;
  } | null;
  review?: {
    revision: number;
    status: 'unreviewed' | 'reviewed';
  };
}

export interface IAnalysisProvenance {
  analysis_pipeline_version: string;
  feature_extractor_version: string;
  signal_dictionary_version: string;
  signal_dictionary_hash: string;
  model_name: string;
  model_version: string | null;
  model_parameters: { temperature: number };
  prompt_version: string;
  prompt_content_hash: string;
  provider_request_id: string | null;
  raw_response_hash: string;
  input_snapshot_hash: string;
}

export interface IAnalysisResult {
  schema_version: 1;
  request_id: string;
  analysis_id: string;
  job_id: string;
  attempt_id: string;
  session_id: string;
  profile_id: string;
  profile_version: number;
  profile_content_hash: string;
  status: 'ready' | 'partial';
  error_code:
    | 'ai_not_configured'
    | 'ai_analysis_failed'
    | 'invalid_profile'
    | null;
  dimension_results: IDimensionResult[];
  provenance: IAnalysisProvenance;
}

export interface IReviewPayload {
  revision: number;
  decision_status: 'resolved' | 'needs_review';
  evidence_status: EvidenceStatus | null;
  level_code: 'possible' | 'clear' | null;
  evidence_event_ids: string[];
  reason_code: 'teacher_confirmed' | 'teacher_correction' | 'uncertain';
  comment: string;
}
