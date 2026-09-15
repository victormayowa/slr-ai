import type { RecordBrief } from './review';

// Mirrors backend/screening_routes.py, records_routes.record_out, and prisma_flow.py.

export type ScreeningStage = 'title_abstract' | 'full_text';

export type ScreeningState = 'unscreened' | 'undecided' | 'awaiting_second_reviewer' | 'conflict' | 'decided' | 'adjudicated' | 'hidden';

export type StageView = {
  state: ScreeningState;
  final_decision: string | null;
  reason_code: string | null;
  my_decision: string | null;
  my_reason_code: string | null;
  my_note: string | null;
  reviewers_decided: number;
  reviewers_required: number;
};

export type CriterionJudgment = {
  criterion_id: number;
  kind: 'inclusion' | 'exclusion';
  text: string;
  judgment: 'met' | 'not_met' | 'unclear';
  rationale: string;
  quote: string | null;
  quote_verified: boolean | null;
  span_id: number | null;
};

export type AiScreening = {
  provider: string;
  model: string;
  error: string | null;
  created_at: string;
  decision: string | null;
  reasoning: string | null;
  supporting_quote: string | null;
  quote_verified: boolean | null;
  confidence: number | null;
  criteria_judgments: CriterionJudgment[];
  document_id: number | null;
  supporting_span_id: number | null;
};

export type ScreeningRecord = {
  id: number;
  title: string;
  authors: string;
  year: string;
  venue: string;
  doi: string;
  abstract: string;
  source: string;
  screening: Record<ScreeningStage, StageView>;
  ai_screening: AiScreening | null;
  ai_screening_hidden: boolean;
  ai_full_text_screening: AiScreening | null;
  priority?: { rank: number; score: number } | null;
};

export type ExclusionReason = { code: string; label: string };

export type ScreeningSettings = {
  title_abstract_reviewers: 1 | 2;
  full_text_reviewers: 1 | 2;
  blind_dual_screening: boolean;
  recall_target: number;
  stopping_alpha: number;
  retrain_every: number;
  custom_exclusion_reasons: ExclusionReason[];
};

export type ExtractionSettings = {
  mode: 'single' | 'dual' | 'human_and_ai';
  numeric_absolute_tolerance: number;
  numeric_relative_tolerance: number;
};

export type ReviewSettings = { screening: ScreeningSettings; extraction: ExtractionSettings; exclusion_reasons: ExclusionReason[] };

export type ModelRun = {
  id: number;
  algorithm: string;
  includes: number;
  excludes: number;
  ranked: number;
  top_terms: string[];
  created_at: string;
  decisions_since: number;
  retrain_due: boolean;
};

export type QueueResponse = { stage: ScreeningStage; remaining: number; model: ModelRun | null; records: ScreeningRecord[] };

export type Conflict = {
  record: RecordBrief & { abstract: string };
  state: 'conflict' | 'adjudicated';
  decisions: { reviewer: string; decision: string; reason_code: string | null; reason: string | null; note: string | null }[];
  adjudication: { decision: string; reason_code: string | null; rationale: string; adjudicator: string | null } | null;
};

export type AgreementStats = { n: number; observed_agreement: number | null; kappa: number | null; kappa_ci: [number, number] | null; pabak: number | null };

export type Agreement = { stage: ScreeningStage; categories: string[]; overall: AgreementStats; pairs: (AgreementStats & { reviewers: string[] })[] };

export type AiPerformance = {
  stage: ScreeningStage;
  compared: number;
  true_positives: number;
  false_positives: number;
  true_negatives: number;
  false_negatives: number;
  sensitivity: number | null;
  sensitivity_ci: [number, number] | null;
  specificity: number | null;
  specificity_ci: [number, number] | null;
  recall_target: number;
  below_target: boolean;
};

export type StoppingEvaluation = {
  id: number;
  method: string;
  result: {
    can_stop: boolean;
    p_value: number | null;
    screened: number;
    relevant_found: number;
    unscreened: number;
    recall_target: number;
    alpha: number;
    consecutive_irrelevant: number;
    heuristic_threshold: number;
    heuristic_met: boolean;
    explanation: string;
  };
  created_at: string;
  accepted_at: string | null;
  accepted_by: string | null;
  acceptance_rationale: string | null;
  still_applies: boolean;
};

export type QaSample = {
  id: number;
  created_at: string;
  size: number;
  pool_size: number;
  screened: number;
  includes_found_in_sample: number;
  record_ids: number[];
  recall: number | null;
  recall_low: number | null;
  recall_high: number | null;
  estimated_missed: number | null;
  recall_target: number;
  below_target: boolean;
};

export const STATE_LABELS: Record<ScreeningState, string> = {
  unscreened: 'Not screened',
  undecided: 'Undecided',
  awaiting_second_reviewer: 'Waiting for a second reviewer',
  conflict: 'Reviewers disagree',
  decided: 'Decided',
  adjudicated: 'Adjudicated',
  hidden: 'Hidden until you decide',
};

export const DECISION_LABELS: Record<string, string> = {
  include: 'Include',
  exclude: 'Exclude',
  undecided: 'Undecided',
  not_retrieved: 'Not retrieved',
};
