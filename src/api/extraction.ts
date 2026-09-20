import type { RecordBrief } from './review';
import type { ExtractionSettings } from './screening';

// Mirrors backend/extraction_routes.py, extraction_data.py, and studies_routes.py.

export type FieldDef = {
  id: number;
  name: string;
  position: number;
  section: string;
  field_type: string;
  options: string[];
  unit: string;
  required: boolean;
  help_text: string;
  per_arm: boolean;
  outcome: string;
  timepoint: string;
  settings: Record<string, unknown>;
};

export type FieldTypeInfo = { key: string; label: string; components: string[] };
export type TemplateInfo = { key: string; label: string; description: string; fields: string[] };
export type ConversionInfo = { key: string; label: string; inputs: { name: string; optional: boolean }[] };

export type ExtractionForm = {
  fields: FieldDef[];
  field_types: FieldTypeInfo[];
  templates: TemplateInfo[];
  conversions: ConversionInfo[];
  units: { groups: Record<string, string[]>; analytes: { key: string; label: string }[] };
  settings: ExtractionSettings;
  analysis_outcomes: string[];
};

export type Structured = Record<string, string | number | boolean | string[]>;

export type ValueInfo = {
  id: number;
  extractor: string | null;
  extractor_id: number | null;
  value: Structured | null;
  display: string;
  not_reported: boolean;
  unit: string;
  span_ids: number[];
  quote: string | null;
  flags: string[];
  derivation: Record<string, unknown> | null;
  source: string;
  note: string;
  needs_approval: boolean;
  approved_at: string | null;
  updated_at: string;
};

export type SuggestionInfo = {
  id: number;
  value: string;
  structured: Structured | null;
  display: string;
  unit: string;
  not_reported: boolean;
  quote: string | null;
  span_ids: number[];
  confidence: number | null;
  ambiguous: boolean;
  grounding: 'grounded' | 'ungrounded' | 'not_reported' | '';
  arm_label: string;
};

export type FinalInfo = {
  id: number;
  value: Structured | null;
  display: string;
  not_reported: boolean;
  unit: string;
  span_ids: number[];
  flags: string[];
  derivation: Record<string, unknown> | null;
  source: 'single' | 'agreed' | 'reconciled';
  rationale: string;
  decided_at: string;
};

export type CellState = 'empty' | 'final' | 'agreed' | 'reconciled' | 'discrepancy' | 'awaiting_second_extractor' | 'awaiting_ai' | 'arms_missing';

export type CellInfo = {
  field_id: number;
  arm_id: number | null;
  arm_label: string | null;
  state: CellState;
  my_value: ValueInfo | null;
  other_values: ValueInfo[] | null;
  values_hidden: boolean;
  final: FinalInfo | null;
  ai_suggestion: SuggestionInfo | null;
  ai_hidden: boolean;
  missing_components: string[];
};

export type ArmInfo = { id: number; label: string; description: string };

export type StudyExtraction = {
  study: {
    id: number;
    label: string;
    registry_ids: string[];
    arms: ArmInfo[];
    reports: { record_id: number; title: string; doi: string; is_primary: boolean; document_id: number | null; file_name: string | null }[];
  };
  fields: FieldDef[];
  cells: CellInfo[];
  settings: ExtractionSettings;
  can_reconcile: boolean;
  can_extract: boolean;
};

export type ExtractionProgress = {
  totals: {
    studies: number;
    unlinked_reports: number;
    cells: number;
    settled: number;
    missing_required: number;
    discrepancies: number;
    awaiting: number;
    unapproved_imputations: number;
  };
  studies: { study_id: number; label: string; cells: number; settled: number; discrepancies: number; awaiting: number; missing_required: number }[];
  discrepancies: { study_id: number; study: string; field_id: number; field: string; arm_id: number | null; arm: string | null }[];
  mode: string;
};

export type Conversion = {
  method: string;
  values: Record<string, number>;
  formula: string;
  reference: string;
  inputs: Record<string, number>;
  assumptions: string[];
};

export type AuthorContact = {
  id: number;
  study_id: number;
  contact_name: string;
  email: string;
  field_ids: number[];
  questions: string;
  status: 'draft' | 'sent' | 'replied' | 'no_response' | 'closed';
  reminder_due: string | null;
  reminder_overdue: boolean;
  response_summary: string;
  created_at: string;
  messages: { id: number; direction: 'outgoing' | 'incoming'; subject: string; body: string; occurred_at: string }[];
};

export type StudyInfo = {
  id: number;
  label: string;
  registry_ids: string[];
  notes: string;
  reports: (RecordBrief & { is_primary: boolean; record_id: number })[];
  arms: ArmInfo[];
};

export type LinkCandidate = { record: RecordBrief; other: RecordBrief; study_id: number; other_study_id: number; score: number; reasons: string[] };

export const CELL_STATES: Record<CellState, { label: string; color: string }> = {
  empty: { label: 'Not extracted', color: '#5A6478' },
  final: { label: 'Final', color: '#137A47' },
  agreed: { label: 'Agreed', color: '#137A47' },
  reconciled: { label: 'Reconciled', color: '#137A47' },
  discrepancy: { label: 'Discrepancy', color: '#C62828' },
  awaiting_second_extractor: { label: 'Waiting for a second extractor', color: '#9A5B00' },
  awaiting_ai: { label: 'Waiting for AI extraction', color: '#9A5B00' },
  arms_missing: { label: 'Define the arms first', color: '#9A5B00' },
};
