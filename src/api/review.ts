export type CriterionInfo = {
  id: number;
  kind: 'inclusion' | 'exclusion';
  text: string;
  status: 'pending' | 'accepted' | 'rejected';
  element: string | null;
  source: 'ai' | 'reviewer';
};

export type StrategyInfo = { id: number; database: string; query: string };

export type ProtocolSettings = {
  review_type: string;
  framework: string;
  description: string;
  suggested_criteria: string;
  extraction_outline: string;
  rob_tool: string;
};

export type PrismaCounts = {
  identified_from_databases: number;
  identified_from_uploads: number;
  by_source: Record<string, number>;
  duplicates_removed: number;
  screened: number;
  excluded: number;
  included: number;
  awaiting_decision: number;
};

export type WorkflowRequirement = { label: string; met: boolean };

export type WorkflowStageInfo = {
  stage: string;
  label: string;
  status: 'open' | 'completed' | 'not_started';
  requirements: WorkflowRequirement[];
  completed_at: string | null;
  completed_by: string | null;
  completion_note: string | null;
  reopen_rationale: string | null;
  latest_version: number | null;
  latest_snapshot_id: number | null;
  can_manage: boolean;
};

type Decision = 'include' | 'exclude' | 'undecided';

type AiRunInfo = { provider: string; model: string; key_source: 'user' | 'platform' | null; error: string | null; created_at: string };

// A quote the AI gave as evidence, and whether it was found in the record's text (null when there is nothing to check).
export type EvidenceInfo = { quote: string | null; verified: boolean | null };

export type ApiRecord = {
  id: number;
  title: string;
  authors: string;
  year: string;
  venue: string;
  doi: string;
  abstract: string;
  source: string;
  duplicate_of_id: number | null;
  ai_screening: (AiRunInfo & { decision: string | null; reasoning: string | null; supporting_quote: string | null; quote_verified: boolean | null }) | null;
  my_decision: Decision | null;
  final_decision: Decision | null;
  extraction: (AiRunInfo & { values: Record<string, string>; evidence: Record<string, EvidenceInfo> }) | null;
  appraisal: (AiRunInfo & { tool: string | null; judgments: Record<string, string> | null }) | null;
};

export type Paper = {
  id: string;
  title: string;
  authors: string;
  year: string;
  source: string;
  doi: string;
  venue?: string;
  abstract?: string;
  ai_decision?: string;
  ai_reasoning?: string;
  ai_quote?: string;
  ai_quote_verified?: boolean;
  ai_error?: string;
  user_decision?: 'Include' | 'Exclude' | 'Undecided' | null;
  extracted_data?: Record<string, string>;
  extraction_evidence?: Record<string, EvidenceInfo>;
  extraction_error?: string;
  rob_data?: Record<string, string>;
  rob_error?: string;
};

const DECISION_LABELS = { include: 'Include', exclude: 'Exclude', undecided: 'Undecided' } as const;

// Adapts a stored record to the shape the review screens render.
export const toPaper = (record: ApiRecord): Paper => ({
  id: String(record.id),
  title: record.title,
  authors: record.authors,
  year: record.year,
  source: record.source,
  doi: record.doi,
  venue: record.venue,
  abstract: record.abstract,
  ai_decision: record.ai_screening?.decision ?? undefined,
  ai_reasoning: record.ai_screening?.reasoning ?? undefined,
  ai_quote: record.ai_screening?.supporting_quote ?? undefined,
  ai_quote_verified: record.ai_screening?.quote_verified ?? undefined,
  ai_error: record.ai_screening?.error ?? undefined,
  user_decision: record.final_decision ? DECISION_LABELS[record.final_decision] : null,
  extracted_data: record.extraction && !record.extraction.error ? record.extraction.values : undefined,
  extraction_evidence: record.extraction && !record.extraction.error ? record.extraction.evidence : undefined,
  extraction_error: record.extraction?.error ?? undefined,
  rob_data: record.appraisal?.judgments ?? undefined,
  rob_error: record.appraisal?.error ?? undefined,
});

export type RecordBrief = { id: number; title: string; authors: string; year: string; doi: string; source: string };

// Mirrors GET /api/projects/{id}/similar-pairs.
export type SimilarPairs = {
  model: string;
  embedded_records: number;
  unique_records: number;
  pairs: { record: RecordBrief; other: RecordBrief; similarity: number }[];
};
