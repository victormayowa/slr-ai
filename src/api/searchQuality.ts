// Mirrors backend/search_quality_routes.py, backend/search_query.py, and backend/search_quality.py.
import { normalizeDatabaseName } from './search';

export type SyntaxInfo = { key: string; label: string; databases: string };

export type PressElement = { key: string; label: string; guidance: string };

export type PressRating = 'no_revision' | 'revision_suggested' | 'revision_required';

export type SearchQualityCatalog = {
  syntaxes: SyntaxInfo[];
  press: { elements: PressElement[]; ratings: PressRating[] };
  recall_connectors: string[];
  database_syntaxes: Record<string, string>;
};

export type ValidationResult = { syntax: string; valid: boolean; errors: string[]; warnings: string[]; term_count: number | null };

export type Translation = { target: string; target_label: string; query: string; warnings: string[] };

export type MeshHeading = { ui: string; heading: string; entry_terms: string[]; scope_note: string; tree_numbers: string[] };

export type RecallCheck = {
  id: number;
  connector: string;
  strategy_version: number;
  seeds: string[];
  found: string[];
  missed: string[];
  recall: number | null;
  created_by: string | null;
  created_at: string;
};

export type PressReview = {
  id: number;
  strategy_id: number | null;
  strategy_version: number | null;
  status: 'completed' | 'waived';
  answers: Record<string, { rating: PressRating; comment: string }>;
  overall: 'approved' | 'revisions_required' | null;
  comment: string;
  reviewer: string | null;
  created_at: string;
};

export type PressStrategyStatus = {
  strategy_id: number;
  database: string;
  version: number;
  status: 'approved' | 'revisions_required' | 'outdated' | 'not_reviewed';
  review_id: number | null;
};

export type PressStatus = { waived: boolean; waiver_reason: string | null; strategies: PressStrategyStatus[]; met: boolean };

export type StrategyVersion = { version: number; database: string; query: string; note: string | null; created_by: string | null; created_at: string };

export const PRESS_RATING_LABELS: Record<PressRating, string> = {
  no_revision: 'No revision',
  revision_suggested: 'Revision suggested',
  revision_required: 'Revision required',
};

export const PRESS_STATUS_LABELS: Record<PressStrategyStatus['status'], string> = {
  approved: 'PRESS approved',
  revisions_required: 'PRESS: revisions required',
  outdated: 'PRESS review is for an older version',
  not_reviewed: 'Not peer reviewed',
};

export const syntaxForDatabase = (catalog: SearchQualityCatalog | null, database: string) =>
  catalog?.database_syntaxes[normalizeDatabaseName(database)] ?? null;
