// Mirrors backend/appraisal_routes.py, synthesis_routes.py, and certainty_routes.py.

export type Choice = { key: string; label: string };

export type ToolQuestion = {
  id: string;
  text: string;
  critical: boolean;
  answers: Choice[];
  asked_if: { questions: string[]; answers: string[]; mode: string } | null;
};

export type ToolDomain = { key: string; label: string; kind: 'bias' | 'applicability' | 'screening'; has_algorithm: boolean; judgments: Choice[]; questions: ToolQuestion[] };

export type AppraisalTool = {
  key: string;
  label: string;
  version: string;
  study_types: string;
  reference: string;
  guidance_url: string;
  per_outcome: boolean;
  notes: string[];
  overall_judgments: Choice[];
  has_overall_algorithm: boolean;
  domains: ToolDomain[];
};

export type ChecklistInfo = { key: string; label: string; study_types: string; reference: string; url: string; items: { id: string; section: string; topic: string }[] };

export type AssessmentSummary = {
  id: number;
  study_id: number;
  tool: string;
  outcome: string;
  status: 'in_progress' | 'signed_off';
  overall_judgment: string | null;
  domains: Record<string, string>;
};

export type AppraisalOverviewRow = {
  study_id: number;
  label: string;
  design: string;
  recommended_tools: { tool: string; reason: string }[];
  recommended_checklist: string | null;
  assessments: AssessmentSummary[];
  reporting: { id: number; checklist: string; status: string }[];
};

export type AISuggestion = { question_id: string | null; domain: string | null; answer: string; rationale: string; quote: string | null; span_ids: number[]; grounded: boolean | null };

export type AssessmentDetail = AssessmentSummary & {
  tool_version: string;
  result_description: string;
  selection_reason: string;
  overall_rationale: string;
  signed_off_by: string | null;
  signed_off_at: string | null;
  study: { id: number; label: string; documents: { record_id: number; document_id: number; file_name: string }[] } | null;
  answers: Record<string, { answer: string; note: string; span_ids: number[]; source: string }>;
  domain_judgments: Record<string, { judgment: string; rationale: string; algorithm_judgment: string | null; signed_off_by: string | null; signed_off_at: string | null }>;
  suggested: { domains: Record<string, string | null>; overall: string | null };
  applicable_questions: Record<string, string[]>;
  unanswered: string[];
  ai_suggestions: AISuggestion[];
};

export type AppraisalSummaryTool = {
  tool: string;
  label: string;
  domains: { key: string; label: string; kind: string }[];
  judgments: Record<string, string>;
  rows: { assessment_id: number; study_id: number; study: string; outcome: string; status: string; domains: Record<string, string>; overall: string | null }[];
};

export type ReportingDetail = {
  id: number;
  study_id: number;
  checklist: string;
  status: string;
  signed_off_at: string | null;
  items: {
    item_id: string;
    section: string;
    topic: string;
    status: string;
    location: string;
    note: string;
    span_ids: number[];
    ai_status: string;
    ai_rationale: string;
    ai_quote: string | null;
    ai_grounded: boolean | null;
  }[];
};

// --- Statistics ---

export type EngineInfo = {
  available: boolean;
  r_version: string;
  packages: Record<string, string | null>;
  message: string;
  analysis_types: Record<string, { available: boolean; missing_packages: string[] }>;
  analysis_type_labels: Record<string, string>;
};

export type RunSummary = {
  id: number;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  is_final: boolean;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  source: 'locked' | 'current' | null;
  studies: number;
};

export type AnalysisInfo = {
  id: number;
  title: string;
  outcome: string;
  analysis_type: string;
  analysis_type_label: string;
  spec: Record<string, any>;
  prespecified: boolean;
  plan_reference: string;
  justification: string;
  status: 'draft' | 'approved';
  approved_by: string | null;
  approved_at: string | null;
  approval_note: string;
  runs: RunSummary[];
  final_run_id: number | null;
};

export type PlotInfo = { name: string; format: string; media_type: string; size_bytes: number };

export type RunDetail = RunSummary & {
  analysis_id: number;
  spec: Record<string, any>;
  spec_sha256: string;
  dataset: { rows: Record<string, any>[]; excluded: { study_id: number; study: string; reason: string }[]; source: string };
  dataset_sha256: string;
  seed: number;
  results: Record<string, any> | null;
  plots: PlotInfo[];
  script: string;
  session_info: string;
  r_version: string;
  packages: Record<string, string>;
  log: string;
};

export type DatasetPreview = {
  source: 'locked' | 'current';
  rows: Record<string, any>[];
  excluded: { study_id: number; study: string; reason: string }[];
  sha256: string;
  pooling: { recommendation: 'meta_analysis' | 'swim' | 'not_possible'; studies: number; reasons: string[] };
};

export type IpdInfo = {
  id: number;
  study_id: number;
  file_name: string;
  rows: number;
  columns: string[];
  mapping: Record<string, string>;
  validation: { problems?: string[]; outcome_type?: string };
  created_at: string;
};

// --- Certainty ---

export type CertaintyCatalog = {
  levels: Choice[];
  downgrade_domains: Record<string, string>;
  upgrade_domains: Record<string, string>;
  etd_criteria: { key: string; label: string; options: string[] }[];
  recommendation_types: string[];
  conclusion_fields: string[];
  conclusion_directions: string[];
};

export type AbsoluteEffect = {
  label: string;
  baseline_per_1000: number;
  intervention_per_1000: number;
  intervention_per_1000_ci: [number, number];
  difference_per_1000: number;
  difference_per_1000_ci: [number, number];
  nnt: { type: string; value: number; interval: string } | null;
};

export type SofRow = {
  grade_assessment_id: number;
  outcome: string;
  comparison: string;
  importance: string;
  certainty: string;
  certainty_label: string;
  status: string;
  analysis_id: number | null;
  run_id: number | null;
  final: boolean;
  studies: number | null;
  participants: number | null;
  relative_effect: string;
  absolute_effects: AbsoluteEffect[];
  footnotes: string[];
  direction: string;
  informative_statement: string;
};

export type DomainSuggestion = { inputs: Record<string, unknown>; suggested_rating: number; reason: string };

export type GradeInfo = {
  id: number;
  outcome: string;
  comparison: string;
  analysis_id: number | null;
  importance: 'critical' | 'important' | 'not_important';
  starting_certainty: 'high' | 'low';
  domains: Record<string, { rating: number; rationale: string }>;
  certainty: string;
  mid: number | null;
  mid_scale: '' | 'per_1000' | 'units';
  outcome_direction: 'lower_is_better' | 'higher_is_better';
  baseline_risks: { label: string; risk: number }[];
  status: 'draft' | 'signed_off';
  signed_off_by: string | null;
  signed_off_at: string | null;
  sign_off_note: string;
  suggestions: Record<string, DomainSuggestion>;
  summary_of_findings: SofRow;
};

export type OutcomeToGrade = { analysis_id: number; title: string; outcome: string; analysis_type: string; final_run_id: number; grade_assessment_id: number | null };

export type EtdInfo = {
  id: number;
  title: string;
  question: string;
  perspective: string;
  criteria: Record<string, { judgment: string; research_evidence: string; additional_considerations: string }>;
  conclusions: Record<string, string>;
  grade_assessment_ids: number[];
  suggested_certainty: string | null;
  status: 'draft' | 'signed_off';
  signed_off_at: string | null;
};

export type PriorReviewInfo = { id: number; title: string; doi: string; year: string; openalex_id: string; references: number; outcome: string; conclusion: string; conclusion_direction: string };

export type PriorComparison = {
  reviews: PriorReviewInfo[];
  matrix: { study_id: number; label: string; identified: boolean; cited_by: boolean[] }[];
  corrected_covered_area: number | null;
  overlap: string;
  note: string;
  lookup_error: string | null;
  conclusions: { prior_review_id: number; outcome: string; prior: string; this_review: string; changed: boolean }[];
};

export type InterpretationInfo = {
  id: number;
  kind: 'informative_statement' | 'limitations' | 'plain_language_summary';
  outcome: string;
  content: string;
  generated_by: 'rules' | 'ai' | 'reviewer';
  ai_run_id: number | null;
  unverified_numbers: string[];
  status: 'draft' | 'approved';
  approved_at: string | null;
  updated_at: string;
};

export const JUDGMENT_COLORS: Record<string, string> = {
  low: '#137A47',
  good: '#137A47',
  high_quality: '#137A47',
  include: '#137A47',
  some_concerns: '#9A5B00',
  moderate: '#9A5B00',
  unclear: '#9A5B00',
  fair: '#9A5B00',
  no_information: '#5A6478',
  seek_information: '#5A6478',
  high: '#C62828',
  serious: '#C62828',
  poor: '#C62828',
  exclude: '#C62828',
  critical: '#8E1B1B',
  very_high: '#8E1B1B',
  critically_low: '#8E1B1B',
};

export const CERTAINTY_SYMBOLS: Record<string, string> = { high: '⊕⊕⊕⊕', moderate: '⊕⊕⊕◯', low: '⊕⊕◯◯', very_low: '⊕◯◯◯' };
