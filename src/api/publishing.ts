// Mirrors backend/manuscript_routes.py, publication_routes.py, and living_routes.py.

export type SentenceCheck = {
  paragraph: number;
  index: number;
  text: string;
  hash: string;
  status: 'verified' | 'unsupported' | 'unverifiable_numbers' | 'number_mismatch' | 'invalid_link' | 'retracted_citation';
  confidence: 'high' | 'medium' | 'low' | 'acknowledged' | null;
  evidence: string[];
  citations: number[];
  issues: string[];
  acknowledged: boolean;
};

export type VerificationReport = {
  sections: { key: string; title: string; sentences: SentenceCheck[]; unresolved: number }[];
  unresolved: number;
  counts: Record<string, number>;
};

export type ManuscriptSection = { key: string; title: string; content: string; updated_at: string; guidance: string };

export type ManuscriptAuthor = {
  id?: number;
  user_id: number | null;
  name: string;
  email: string;
  affiliation: string;
  orcid: string;
  corresponding: boolean;
  credit_roles: string[];
  competing_interests: string;
};

export type ApprovalState = {
  version_id: number | null;
  version_number: number | null;
  current: boolean;
  authors: { author_id: number; name: string; user_id: number | null; approved: boolean; approved_at: string | null; note: string }[];
  all_approved: boolean;
};

export type ManuscriptInfo = {
  id: number;
  title: string;
  citation_style: string;
  built_in_styles: Record<string, string>;
  keywords: string[];
  statements: Record<string, string>;
  extras: Record<string, { items?: string[]; unverified_numbers?: string[] }>;
  sections: ManuscriptSection[];
  authors: ManuscriptAuthor[];
  credit_roles: string[];
  statement_keys: string[];
  verification: VerificationReport;
  approvals: ApprovalState;
  versions: { id: number; number: number; note: string; created_at: string; sha256: string }[];
  tools: { pandoc: boolean; tectonic: boolean; rsvg_convert: boolean };
};

export type EvidenceEntry = { key: string; marker: string; label: string; group: string; facts: string[] };

export type DiffOp = { op: 'equal' | 'insert' | 'delete'; text: string };

export type SuggestionInfo = {
  id: number;
  section_key: string;
  kind: string;
  instruction: string;
  content: string;
  problems: string[];
  status: string;
  created_at: string;
  diff: DiffOp[];
  verification: { sentences: SentenceCheck[]; unverified: number };
};

export type RevisionInfo = { id: number; source: string; note: string; created_by: string | null; created_at: string; characters: number };

export type ReferenceInfo = {
  id: number;
  record_id: number | null;
  doi: string;
  pmid: string;
  formatted: string;
  cited: boolean;
  number: number | null;
  verification_status: 'unchecked' | 'verified' | 'mismatch' | 'not_found' | 'error';
  verification: Record<string, unknown>;
  retracted: boolean;
  checked_at: string | null;
};

export type ChecklistItemInfo = {
  item_id: string;
  topic: string;
  section: string;
  auto_status: string;
  auto_location: string;
  status: string;
  location: string;
  note: string;
  overridden: boolean;
};

export type ManuscriptChecklist = { key: string; label: string; reference: string; url: string; note: string; applicable: boolean; complete: boolean; items: ChecklistItemInfo[] };

export type AssetInfo = { marker: string; kind: 'table' | 'figure'; key: string; title: string };

export type JournalCandidate = {
  id: number;
  name: string;
  issns: string[];
  publisher: string;
  homepage: string;
  is_oa: boolean | null;
  in_doaj: boolean | null;
  apc_usd: number | null;
  h_index: number | null;
  medline_indexed: boolean | null;
  topic_works: number;
  included_study_reports: number;
  score: number;
  reasons: string[];
  warnings: string[];
  shortlisted: boolean;
};

export type GuidelineInfo = {
  id: number;
  journal_name: string;
  source_url: string;
  file_name: string;
  requirements: Record<string, { value: string; quotes?: string[]; grounded: boolean; source: string }>;
  created_at: string;
};

export type ReadinessCheck = { key: string; label: string; status: 'pass' | 'fail' | 'not_applicable'; detail: string; severity: 'error' | 'warning' };

export type PackageInfo = {
  id: number;
  journal_name: string;
  guideline_id: number | null;
  manuscript_version_id: number | null;
  cover_letter: string;
  files: { path: string; size_bytes: number; sha256: string }[];
  sha256: string;
  readiness: ReadinessCheck[];
  blocking: number;
  status: 'draft' | 'built' | 'confirmed';
};

export type DepositInfo = { id: number; target: string; status: string; sandbox: boolean; external_id: string; doi: string; url: string; error: string | null; release_id: number | null; created_at: string };

export type ReviewerComment = {
  id: number;
  reviewer: string;
  number: string;
  body: string;
  category: string;
  response: string;
  status: 'open' | 'addressed' | 'rebutted';
  changes: { section_key: string; revision_id: number }[];
  reanalysis: { stage: string; rationale: string } | null;
};

export type ReviewRound = { id: number; journal: string; round_number: number; decision: string; received_on: string; response_letter: string; status: string; comments: ReviewerComment[]; open_comments: number };

export type ScheduleInfo = { id: number; strategy_id: number; database: string; connector: string; frequency_days: number; next_run_at: string; last_run_at: string | null; active: boolean; thresholds: Record<string, number> };

export type SurveillanceRunInfo = { id: number; kind: string; status: string; database: string; retrieved: number; new_candidates: number; duplicates: number; error: string | null; started_at: string };

export type CandidateInfo = {
  id: number;
  title: string;
  authors: string;
  year: string;
  venue: string;
  doi: string;
  abstract: string;
  relevance: number | null;
  sample_size: number | null;
  ai_decision: string | null;
  ai_reasoning: string | null;
  status: 'pending' | 'promoted' | 'dismissed' | 'imported';
  decision_reason: string;
};

export type AlertInfo = { id: number; kind: string; title: string; detail: Record<string, unknown>; status: 'open' | 'acknowledged' | 'dismissed'; note: string; created_at: string };

export type FeedInfo = { id: number; label: string; url: string; items_seen: number; last_checked_at: string | null; last_error: string | null };

export type EffectSummary = { k: number | null; estimate: number | null; ci_lower: number | null; ci_upper: number | null; I2: number | null; ratio: boolean };

export type ImpactInfo = {
  id: number;
  analysis_id: number;
  baseline: EffectSummary;
  provisional: EffectSummary;
  shift: Record<string, unknown>;
  grade_changes: Record<string, unknown>[];
  status: string;
  error: string | null;
  created_at: string;
};

export type ReleaseInfo = { id: number; version: number; title: string; notes: string; changelog: string[]; sha256: string; created_at: string; deposits: DepositInfo[] };

export type EvidenceMap = {
  interventions: string[];
  outcomes: string[];
  bubbles: { intervention: string; outcome: string; studies: number; certainty: string | null }[];
  fields: { id: number; name: string }[];
  coverage: { study_id: number; study: string; fields: Record<string, 'reported' | 'not_reported' | 'missing'> }[];
  trends: {
    records_by_year: Record<string, number>;
    included_by_year: Record<string, number>;
    surveillance: { date: string; new_candidates: number; database: string }[];
    releases: { version: number; date: string; studies: number }[];
  };
};

export const CLAIM_LABELS: Record<SentenceCheck['status'], string> = {
  verified: 'Verified',
  unsupported: 'No evidence',
  unverifiable_numbers: "Numbers can't be checked",
  number_mismatch: 'Numbers differ from evidence',
  invalid_link: 'Unknown link',
  retracted_citation: 'Cites a retracted work',
};
